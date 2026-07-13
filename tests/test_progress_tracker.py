import asyncio
import time
from unittest.mock import AsyncMock, patch
import pytest
from src.services.progress_tracker import ProgressTracker


@pytest.mark.asyncio
async def test_progress_tracker_formatting():
    mock_redis = AsyncMock()
    tracker = ProgressTracker(
        action_name="DOWNLOAD",
        item_id="101",
        clean_name="Test.mkv",
        log_interval=1000.0,
        redis_pool=mock_redis,
    )
    tracker.telegram_interval = 1000.0
    tracker.terminal_interval = 1000.0

    total_bytes = 100 * 1024 * 1024  # 100 MB

    # First chunk (10 MB)
    await tracker.on_progress(10 * 1024 * 1024, total_bytes)
    assert tracker.last_bytes == 10 * 1024 * 1024
    mock_redis.hset.assert_called_once()
    mapping = mock_redis.hset.call_args.kwargs["mapping"]
    assert mapping["progress_action"] == "DOWNLOAD"
    assert "10.0%" in mapping["progress_percent"]
    assert "10.00 MB / 100.00 MB" in mapping["progress_bytes"]
    assert "Speed:" in mapping["progress_speed"] or mapping["progress_speed"] != ""

    mock_redis.reset_mock()

    # Second chunk immediately (within 1000.0s, guaranteed throttled)
    await tracker.on_progress(15 * 1024 * 1024, total_bytes)
    mock_redis.hset.assert_not_called()

    # Simulate time passing beyond interval for third chunk
    tracker.last_log_time = time.time() - 2000.0
    await tracker.on_progress(50 * 1024 * 1024, total_bytes)
    assert tracker.last_bytes == 50 * 1024 * 1024
    mock_redis.hset.assert_called_once()
    mapping2 = mock_redis.hset.call_args.kwargs["mapping"]
    assert "50.0%" in mapping2["progress_percent"]

    # Final completion chunk (100 MB)
    mock_redis.reset_mock()
    await tracker.on_progress(total_bytes, total_bytes)
    assert tracker._is_completed is True
    assert mock_redis.hset.called
