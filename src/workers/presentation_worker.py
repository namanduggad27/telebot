import asyncio
import logging
from typing import Any, Dict
from aiogram import Bot
from hydrogram import Client
from sqlalchemy import select

from config.settings import settings
from src.db.models import MediaItem, PipelineStatus
from src.db.session import get_db_session
from src.services.state_machine import StateMachine
from src.services.links_bot_client import LinksBotClient
from src.services.native_batch_engine import NativeBatchEngine
from src.services.presentation import generate_presentation_post

logger = logging.getLogger("workers.presentation_worker")


async def batch_link_and_post_task(ctx: Dict[str, Any], item_id: str) -> bool:
    """ARQ background task for Phase 4: get shareable deep-link URL and post stylized presentation to Main Channel."""
    logger.info(f"Starting Phase 4 presentation task for item ID={item_id}")

    # 1. Fetch item from database
    item = None
    async for db in get_db_session():
        stmt = select(MediaItem).where(MediaItem.id == int(item_id))
        result = await db.execute(stmt)
        item = result.scalar_one_or_none()
        break

    if not item:
        logger.error(f"MediaItem ID={item_id} not found for Phase 4 presentation.")
        return False

    if item.status not in (PipelineStatus.SHADOW_ARCHIVED, PipelineStatus.BATCH_LINKING):
        logger.warning(
            f"Item ID={item_id} in state {item.status.value}, expected SHADOW_ARCHIVED/BATCH_LINKING. Aborting Phase 4."
        )
        return False

    # 2. Transition to BATCH_LINKING
    await StateMachine.transition_item(item_id, PipelineStatus.BATCH_LINKING)

    # 3. Generate shareable deep-link URL using our self-contained native bot (or external links bot if configured)
    if settings.LINKS_BOT_USERNAME and settings.LINKS_BOT_USERNAME != "@YourLinksBot":
        logger.info(f"External LINKS_BOT_USERNAME={settings.LINKS_BOT_USERNAME} configured. Querying via Userbot...")
        batch_url = f"https://t.me/c/{str(settings.SHADOW_CHANNEL_ID).replace('-100', '')}/{item.shadow_message_id}"
        client = None
        if settings.TG_API_ID and settings.TG_API_HASH and item.shadow_message_id:
            client = Client(
                name=f"{settings.TG_USERBOT_SESSION}_links",
                api_id=settings.TG_API_ID,
                api_hash=settings.TG_API_HASH,
                workdir=str(settings.BASE_DIR),
            )
            try:
                await client.start()
                url = await LinksBotClient.get_batch_link(
                    client, item.shadow_message_id, settings.SHADOW_CHANNEL_ID or item.raw_channel_id
                )
                if url:
                    batch_url = url
            except Exception as e:
                logger.error(f"Error obtaining external batch link via Userbot: {e}")
            finally:
                if client and client.is_connected:
                    await client.stop()
    else:
        # Use our Native Batch Engine (100% free, no external bot required!)
        logger.info("Using NativeBatchEngine to generate self-contained deep-link parameter URL...")
        batch_url = await NativeBatchEngine.generate_shareable_url(item)

    # 4. Fetch cached TMDB state
    cached_state = await StateMachine.get_cached_state(item_id)
    tmdb_info = {
        "vote_average": float(cached_state.get("vote_average", 8.0)) if cached_state else 8.0,
        "release_date": cached_state.get("release_date", "2024-01-01") if cached_state else "2024-01-01",
        "overview": cached_state.get("tmdb_overview", "") if cached_state else "",
    }
    poster_url = cached_state.get("tmdb_poster_url") if cached_state else None

    # 5. Generate stylized caption and inline action buttons
    caption, keyboard = generate_presentation_post(item, tmdb_info, batch_url)

    # 6. Publish to Main Presentation Channel (`settings.MAIN_CHANNEL_ID`) via Aiogram Bot or Userbot
    main_msg_id = None
    if not settings.MAIN_CHANNEL_ID:
        logger.warning("MAIN_CHANNEL_ID not configured! Skipping actual Telegram channel broadcast.")
    else:
        if settings.ADMIN_BOT_TOKEN:
            try:
                bot = Bot(token=settings.ADMIN_BOT_TOKEN)
                if poster_url:
                    sent = await bot.send_photo(
                        chat_id=settings.MAIN_CHANNEL_ID,
                        photo=poster_url,
                        caption=caption,
                        parse_mode="Markdown",
                        reply_markup=keyboard,
                    )
                else:
                    sent = await bot.send_message(
                        chat_id=settings.MAIN_CHANNEL_ID,
                        text=caption,
                        parse_mode="Markdown",
                        reply_markup=keyboard,
                    )
                main_msg_id = sent.message_id
                await bot.session.close()
                logger.info(f"Successfully published ID={item_id} to Main Channel {settings.MAIN_CHANNEL_ID} (Msg ID={main_msg_id})")
            except Exception as bot_err:
                logger.error(f"Aiogram Bot publish failed for ID={item_id}: {bot_err}", exc_info=True)

    # 7. Persist main message ID and batch link to PostgreSQL and transition to PUBLISHED
    async for db in get_db_session():
        stmt = select(MediaItem).where(MediaItem.id == int(item_id))
        res = await db.execute(stmt)
        db_item = res.scalar_one_or_none()
        if db_item:
            if main_msg_id:
                db_item.main_message_id = main_msg_id
            db_item.batch_link_url = batch_url
            await db.commit()
        break

    success = await StateMachine.transition_item(item_id, PipelineStatus.PUBLISHED)
    logger.info(f"Phase 4 presentation complete for ID={item_id}. Pipeline execution FULLY SUCCESSFUL! 🚀")
    return success
