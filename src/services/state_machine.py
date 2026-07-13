import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set
import redis.asyncio as aioredis
from sqlalchemy import select, update

from config.settings import settings
from src.db.models import MediaItem, PipelineStatus
from src.db.session import async_engine, get_db_session

logger = logging.getLogger("services.state_machine")


# Valid transitions mapping
VALID_TRANSITIONS: Dict[PipelineStatus, Set[PipelineStatus]] = {
    PipelineStatus.SCRAPED: {PipelineStatus.ENRICHED, PipelineStatus.FAILED, PipelineStatus.REJECTED},
    PipelineStatus.ENRICHED: {PipelineStatus.CONFIRMED, PipelineStatus.REJECTED, PipelineStatus.FAILED},
    PipelineStatus.CONFIRMED: {PipelineStatus.DOWNLOADING, PipelineStatus.FAILED, PipelineStatus.REJECTED},
    PipelineStatus.DOWNLOADING: {PipelineStatus.UPLOADING_SHADOW, PipelineStatus.SHADOW_UPLOADED, PipelineStatus.FAILED},
    PipelineStatus.UPLOADING_SHADOW: {PipelineStatus.SHADOW_UPLOADED, PipelineStatus.FAILED},
    PipelineStatus.SHADOW_UPLOADED: {PipelineStatus.BATCH_LINKED, PipelineStatus.FINAL_POSTED, PipelineStatus.FAILED},
    PipelineStatus.BATCH_LINKED: {PipelineStatus.FINAL_POSTED, PipelineStatus.FAILED},
    PipelineStatus.FINAL_POSTED: set(),  # Terminal success state
    PipelineStatus.REJECTED: {PipelineStatus.ENRICHED, PipelineStatus.CONFIRMED},  # Allow manual re-trial if needed
    PipelineStatus.FAILED: {PipelineStatus.SCRAPED, PipelineStatus.ENRICHED, PipelineStatus.CONFIRMED, PipelineStatus.DOWNLOADING},
}


class StateMachine:
    """Orchestrates pipeline state transitions with transient Redis caching and PostgreSQL persistence."""

    _redis_pool: Optional[aioredis.Redis] = None

    @classmethod
    def get_redis(cls) -> aioredis.Redis:
        """Get or initialize the async Redis client connection pool."""
        if cls._redis_pool is None:
            cls._redis_pool = aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
            )
        return cls._redis_pool

    @classmethod
    async def close_redis(cls) -> None:
        """Close the Redis connection pool."""
        if cls._redis_pool:
            await cls._redis_pool.aclose()
            cls._redis_pool = None

    @classmethod
    def can_transition(cls, current_status: PipelineStatus, target_status: PipelineStatus) -> bool:
        """Check if a transition from current_status to target_status is valid according to state machine rules."""
        if current_status == target_status:
            return True
        allowed = VALID_TRANSITIONS.get(current_status, set())
        return target_status in allowed

    @classmethod
    async def transition_item(
        cls,
        item_id: str,
        target_status: PipelineStatus,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Atomically transition a MediaItem's state in both PostgreSQL and Redis Hash cache."""
        redis = cls.get_redis()
        redis_key = f"pipeline:item:{item_id}"

        async for db in get_db_session():
            # 1. Fetch current item from DB to verify current state
            stmt = select(MediaItem).where(MediaItem.id == int(item_id))
            result = await db.execute(stmt)
            item = result.scalar_one_or_none()

            if not item:
                logger.error(f"MediaItem ID={item_id} not found in database for state transition.")
                return False

            if not cls.can_transition(item.status, target_status):
                logger.warning(
                    f"Invalid state transition attempted for ID={item_id}: {item.status.value} -> {target_status.value}"
                )
                return False

            old_status = item.status
            item.status = target_status
            item.updated_at = datetime.now(timezone.utc)

            # Update extra fields if provided
            if extra_metadata:
                for k, v in extra_metadata.items():
                    if hasattr(item, k):
                        setattr(item, k, v)

            await db.commit()
            await db.refresh(item)

            # 2. Update Redis Hash cache with 72-hour TTL for transient state lookup
            try:
                cache_payload = {
                    "id": str(item.id),
                    "status": item.status.value,
                    "updated_at": item.updated_at.isoformat(),
                    "parsed_title": item.parsed_title or "",
                    "clean_file_name": item.clean_file_name or "",
                    "season_num": str(item.season_num) if item.season_num is not None else "",
                    "episode_num": str(item.episode_num) if item.episode_num is not None else "",
                    "quality_tag": item.quality_tag or "",
                    "tmdb_id": str(item.tmdb_id) if item.tmdb_id is not None else "",
                }
                if extra_metadata and "tmdb_poster_url" in extra_metadata:
                    cache_payload["tmdb_poster_url"] = str(extra_metadata["tmdb_poster_url"])
                if extra_metadata and "tmdb_overview" in extra_metadata:
                    cache_payload["tmdb_overview"] = str(extra_metadata["tmdb_overview"])

                await redis.hset(redis_key, mapping=cache_payload)
                await redis.expire(redis_key, settings.STATE_TTL_SECONDS)
            except Exception as redis_err:
                logger.warning(f"Failed to update Redis cache for ID={item_id}: {redis_err}")

            logger.info(f"State transition ID={item_id}: {old_status.value} -> {target_status.value}")
            return True

        return False

    @classmethod
    async def get_cached_state(cls, item_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached item state from Redis, falling back to PostgreSQL if cache expired."""
        redis = cls.get_redis()
        redis_key = f"pipeline:item:{item_id}"

        try:
            cached = await redis.hgetall(redis_key)
            if cached and "status" in cached:
                return cached
        except Exception as e:
            logger.debug(f"Redis cache miss/error for ID={item_id}: {e}")

        # Fallback to DB query
        async for db in get_db_session():
            stmt = select(MediaItem).where(MediaItem.id == int(item_id))
            result = await db.execute(stmt)
            item = result.scalar_one_or_none()
            if item:
                return {
                    "id": str(item.id),
                    "status": item.status.value,
                    "updated_at": item.updated_at.isoformat(),
                    "parsed_title": item.parsed_title or "",
                    "clean_file_name": item.clean_file_name or "",
                    "season_num": str(item.season_num) if item.season_num is not None else "",
                    "episode_num": str(item.episode_num) if item.episode_num is not None else "",
                    "quality_tag": item.quality_tag or "",
                    "tmdb_id": str(item.tmdb_id) if item.tmdb_id is not None else "",
                }
        return None
