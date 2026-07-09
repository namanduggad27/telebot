import asyncio
import logging
from typing import Optional
from hydrogram import Client, filters
from hydrogram.types import Message

from config.settings import settings
from config.logging_config import configure_logging
from src.db.session import async_engine, get_db_session
from src.db.models import MediaItem, PipelineStatus
from src.scrapers.regex_engine import RegexEngine

logger = logging.getLogger("scrapers.userbot_listener")


class RawChannelListener:
    """Hydrogram Userbot listener that monitors the Raw Channel for new video drops and logs them to Postgres + Redis."""

    def __init__(self) -> None:
        self.client: Optional[Client] = None

    def create_client(self) -> Client:
        """Initialize the Hydrogram MTProto Client."""
        if not settings.TG_API_ID or not settings.TG_API_HASH:
            logger.warning(
                "TG_API_ID or TG_API_HASH not set in environment or .env. Listener will require valid credentials to run."
            )

        client = Client(
            name=settings.TG_USERBOT_SESSION,
            api_id=settings.TG_API_ID,
            api_hash=settings.TG_API_HASH,
            workdir=str(settings.BASE_DIR),
        )
        self.client = client
        self.register_handlers(client)
        return client

    def register_handlers(self, client: Client) -> None:
        """Register Hydrogram event handlers for incoming raw media files."""

        @client.on_message(filters.channel & (filters.video | filters.document))
        async def on_raw_media_received(client: Client, message: Message) -> None:
            """Handler triggered when a video or document arrives in the monitored Raw Channel."""
            if settings.RAW_CHANNEL_ID and message.chat.id != settings.RAW_CHANNEL_ID:
                return  # Ignore messages from other channels if RAW_CHANNEL_ID filter is set

            media = message.video or message.document
            if not media:
                return

            file_unique_id = media.file_unique_id
            file_id = media.file_id
            file_name = getattr(media, "file_name", None) or message.caption or f"unknown_{message.id}.mkv"
            file_size = media.file_size or 0

            logger.info(
                f"New raw media received: message_id={message.id}, name='{file_name}', size={file_size} bytes"
            )

            # Run regex parsing engine on the raw filename
            parsed = RegexEngine.parse(file_name)
            logger.info(
                f"Regex parsed -> clean_title='{parsed.clean_title}', S={parsed.season_num}, E={parsed.episode_num}, quality='{parsed.quality}'"
            )

            # Persist initial record to Postgres using async session generator
            try:
                async for db in get_db_session():
                    item = MediaItem(
                        raw_message_id=message.id,
                        raw_channel_id=message.chat.id,
                        raw_file_id=file_id,
                        file_unique_id=file_unique_id,
                        file_size_bytes=file_size,
                        parsed_title=parsed.clean_title,
                        season_num=parsed.season_num,
                        episode_num=parsed.episode_num,
                        quality_tag=parsed.quality,
                        codec_tag=parsed.codec,
                        clean_file_name=parsed.clean_file_name,
                        status=PipelineStatus.SCRAPED,
                    )
                    db.add(item)
                    await db.flush()
                    await db.refresh(item)
                    logger.info(
                        f"Inserted MediaItem ID={item.id} (status={item.status.value}) for unique_id='{file_unique_id}'"
                    )
                    break
            except Exception as e:
                logger.error(
                    f"Failed to record MediaItem for unique_id='{file_unique_id}': {e}", exc_info=True
                )
                return

            # Push the newly recorded item ID to the ARQ / Redis queue for Phase 2 TMDB enrichment
            try:
                from arq import create_pool
                from arq.connections import RedisSettings
                redis_pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
                await redis_pool.enqueue_job("enrich_metadata_task", str(item.id))
                await redis_pool.aclose()
                logger.info(
                    f"Enqueued enrich_metadata_task for item ID={item.id} on ARQ Redis queue."
                )
            except Exception as q_err:
                logger.warning(
                    f"Could not enqueue enrich_metadata_task for ID={item.id}: {q_err}. (Make sure Redis is running)"
                )

    async def start(self) -> None:
        """Start the async Userbot listener service."""
        configure_logging()
        settings.ensure_directories()
        logger.info("Starting Raw Channel Userbot Listener...")
        if not self.client:
            self.create_client()
        assert self.client is not None
        await self.client.start()
        logger.info(f"Userbot connected: {(await self.client.get_me()).username or 'User'}")

    async def stop(self) -> None:
        """Gracefully stop the Userbot client and database connection pool."""
        logger.info("Stopping Raw Channel Userbot Listener...")
        if self.client and self.client.is_connected:
            await self.client.stop()
        await async_engine.dispose()
        logger.info("Userbot and DB engine shut down cleanly.")


async def main() -> None:
    """Entry point for running the userbot listener standalone."""
    listener = RawChannelListener()
    try:
        await listener.start()
        await asyncio.Event().wait()  # Keep running indefinitely
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await listener.stop()


if __name__ == "__main__":
    asyncio.run(main())
