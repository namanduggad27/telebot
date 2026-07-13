import pytest
from unittest.mock import AsyncMock, MagicMock
from src.db.models import MediaItem, PipelineStatus
from src.services.native_batch_engine import NativeBatchEngine


@pytest.mark.asyncio
async def test_generate_shareable_url():
    """Verify deep-link start parameter generation for both single items and TV seasons."""
    item = MediaItem(
        id="123e4567-e89b-12d3-a456-426614174000",
        raw_message_id=1,
        raw_channel_id=-1001,
        raw_file_id="fid123",
        file_unique_id="funique123",
        parsed_title="House of the Dragon",
        season_num=2,
        episode_num=4,
        tmdb_id=94997,
        status=PipelineStatus.SHADOW_ARCHIVED,
    )

    # 1. Season batch preferred
    url_season = await NativeBatchEngine.generate_shareable_url(item, prefer_season_batch=True)
    assert "start=s_94997_2" in url_season

    # 2. Single item preferred
    url_single = await NativeBatchEngine.generate_shareable_url(item, prefer_season_batch=False)
    assert "start=f_123e4567e89b12d3a456426614174000" in url_single
