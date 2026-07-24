import logging
from typing import Any, Dict
from sqlalchemy import select

from src.db.models import MediaItem, PipelineStatus
from src.db.session import get_db_session
from src.services.state_machine import StateMachine
from src.services.omdb_client import OMDBClient
from src.bot.admin_bot import send_confirmation_card

logger = logging.getLogger("workers.metadata_worker")


async def enrich_metadata_task(ctx: Dict[str, Any], item_id: str) -> bool:
    """ARQ background task that queries OMDB for poster/overview and triggers the HITL confirmation card."""
    logger.info(f"Starting metadata enrichment task for item ID={item_id}")

    # 1. Fetch item from database
    item = None
    async for db in get_db_session():
        stmt = select(MediaItem).where(MediaItem.id == int(item_id))
        result = await db.execute(stmt)
        item = result.scalar_one_or_none()
        break

    if not item:
        logger.error(f"MediaItem ID={item_id} not found in database.")
        return False

    if item.status != PipelineStatus.SCRAPED and item.status != PipelineStatus.ENRICHED:
        logger.warning(
            f"Item ID={item_id} in state {item.status.value}, expected SCRAPED/ENRICHED. Aborting."
        )
        return False

    # 2. Query OMDB API
    omdb_client = OMDBClient()
    # Check if title has year appended or in parsed_title
    query_title = item.parsed_title or ""
    omdb_result = None

    try:
        omdb_result = await omdb_client.search_media(query_title=query_title, year=item.season_num if item.season_num is None else None) # we could use year here but we don't have it easily
    except Exception as e:
        logger.error(f"OMDB query error for ID={item_id}: {e}")

    omdb_info = {}
    extra_meta = {}

    if omdb_result:
        logger.info(
            f"OMDB Match ID={item_id}: title='{omdb_result.title}', omdb_id={omdb_result.omdb_id}, rating={omdb_result.vote_average}"
        )
        extra_meta["omdb_id"] = omdb_result.omdb_id
        omdb_info = {
            "poster_url": omdb_result.poster_url,
            "overview": omdb_result.overview,
            "vote_average": omdb_result.vote_average,
            "release_date": omdb_result.year,
        }
    else:
        logger.warning(f"No OMDB match for ID={item_id}. Proceeding with raw parsed metadata.")

    # 3. Transition to ENRICHED
    success = await StateMachine.transition_item(
        item_id=item_id, target_status=PipelineStatus.ENRICHED, extra_metadata=extra_meta
    )
    if not success:
        logger.error(f"Failed to transition ID={item_id} to ENRICHED.")
        return False

    logger.info(f"Metadata enrichment complete for ID={item_id}. Item stored in ENRICHED state for batch/grouped processing.")
    return True
