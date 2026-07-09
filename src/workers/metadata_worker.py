import logging
from typing import Any, Dict
from sqlalchemy import select

from src.db.models import MediaItem, PipelineStatus
from src.db.session import get_db_session
from src.services.state_machine import StateMachine
from src.services.tmdb_client import TMDBClient
from src.bot.admin_bot import send_confirmation_card

logger = logging.getLogger("workers.metadata_worker")


async def enrich_metadata_task(ctx: Dict[str, Any], item_id: str) -> bool:
    """ARQ background task that queries TMDB for poster/overview and triggers the HITL confirmation card."""
    logger.info(f"Starting metadata enrichment task for item ID={item_id}")

    # 1. Fetch item from database
    item = None
    async for db in get_db_session():
        stmt = select(MediaItem).where(MediaItem.id == item_id)
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

    # 2. Query TMDB API
    tmdb_client = TMDBClient()
    # Check if title has year appended or in parsed_title
    query_title = item.parsed_title or ""
    tmdb_result = None

    try:
        tmdb_result = await tmdb_client.search_media(query_title=query_title)
    except Exception as e:
        logger.error(f"TMDB query error for ID={item_id}: {e}")

    tmdb_info = {}
    extra_meta = {}

    if tmdb_result:
        logger.info(
            f"TMDB Match ID={item_id}: title='{tmdb_result.title}', tmdb_id={tmdb_result.tmdb_id}, rating={tmdb_result.vote_average}"
        )
        extra_meta["tmdb_id"] = tmdb_result.tmdb_id
        tmdb_info = {
            "poster_url": tmdb_result.poster_url,
            "backdrop_url": tmdb_result.backdrop_url,
            "overview": tmdb_result.overview,
            "vote_average": tmdb_result.vote_average,
            "release_date": tmdb_result.release_date,
        }
    else:
        logger.warning(f"No TMDB match for ID={item_id}. Proceeding with raw parsed metadata.")

    # 3. Transition to ENRICHED
    success = await StateMachine.transition_item(
        item_id=item_id, target_status=PipelineStatus.ENRICHED, extra_metadata=extra_meta
    )
    if not success:
        logger.error(f"Failed to transition ID={item_id} to ENRICHED.")
        return False

    # 4. Send Confirmation Card to Admin Bot and transition to PENDING_CONFIRMATION
    msg_id = await send_confirmation_card(item, tmdb_info=tmdb_info)
    if msg_id:
        await StateMachine.transition_item(item_id=item_id, target_status=PipelineStatus.CONFIRMED if not msg_id else PipelineStatus.ENRICHED)
        logger.info(f"Metadata enrichment & notification complete for ID={item_id}")
        return True
    else:
        logger.warning(
            f"Could not send notification card for ID={item_id}. Remaining in ENRICHED state."
        )
        return True
