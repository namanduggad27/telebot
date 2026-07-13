import logging
from arq.connections import RedisSettings
from arq.worker import Worker

from config.settings import settings
from config.logging_config import configure_logging
from src.workers.metadata_worker import enrich_metadata_task
from src.workers.io_worker import process_media_io_task
from src.workers.presentation_worker import batch_link_and_post_task
from src.services.state_machine import StateMachine
from src.db.session import async_engine, init_db

logger = logging.getLogger("workers.arq_worker")


async def startup(ctx: dict) -> None:
    """Run on ARQ worker process startup."""
    configure_logging()
    settings.ensure_directories()
    logger.info("ARQ Worker initializing pool and database connections...")
    await init_db()


async def shutdown(ctx: dict) -> None:
    """Run on ARQ worker process shutdown."""
    logger.info("ARQ Worker shutting down cleanly...")
    await StateMachine.close_redis()
    await async_engine.dispose()


class WorkerSettings:
    """Configuration and registered task definitions for ARQ worker process."""
    functions = [
        enrich_metadata_task,
        process_media_io_task,
        batch_link_and_post_task,
    ]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 10
    job_timeout = 600  # 10 minutes timeout per task
