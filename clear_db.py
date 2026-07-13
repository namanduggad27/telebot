import asyncio
import logging
from sqlalchemy import delete
from src.db.models import MediaItem
from src.db.session import AsyncSessionFactory, async_engine
from redis.asyncio import Redis
from config.settings import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("clear_db")


async def clear_all_media() -> None:
    """Clear all records from PostgreSQL media_items table and flush related Redis keys."""
    logger.info("Connecting to PostgreSQL database to delete all MediaItem records...")
    async with AsyncSessionFactory() as session:
        result = await session.execute(delete(MediaItem))
        await session.commit()
        logger.info(f"Successfully deleted {result.rowcount if hasattr(result, 'rowcount') else 'all'} media records from PostgreSQL.")
    
    await async_engine.dispose()

    try:
        logger.info("Connecting to Redis to clear pipeline item cache and ARQ queue...")
        r = Redis.from_url(settings.REDIS_URL)
        await r.flushdb()
        await r.aclose()
        logger.info("Successfully flushed Redis database.")
    except Exception as e:
        logger.warning(f"Could not clear Redis cache: {e}")

    logger.info("Database and cache cleanup completed successfully! ✨")


if __name__ == "__main__":
    asyncio.run(clear_all_media())
