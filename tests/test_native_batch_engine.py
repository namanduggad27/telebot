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
        omdb_id="94997",
        status=PipelineStatus.SHADOW_ARCHIVED,
    )

    # 1. Season batch preferred
    url_season = await NativeBatchEngine.generate_shareable_url(item, prefer_season_batch=True)
    assert "start=s_94997_2" in url_season

    # 2. Single item preferred
    url_single = await NativeBatchEngine.generate_shareable_url(item, prefer_season_batch=False)
    assert "start=f_123e4567e89b12d3a456426614174000" in url_single


@pytest.mark.asyncio
async def test_handle_start_parameter_sends_description():
    """Verify that handle_start_parameter sends the media description before copying files."""
    from unittest.mock import patch

    item = MediaItem(
        id=1,
        raw_message_id=1,
        raw_channel_id=-1001,
        raw_file_id="fid123",
        file_unique_id="funique123",
        parsed_title="Breaking Bad",
        season_num=1,
        episode_num=1,
        quality_tag="1080p",
        clean_file_name="[TIF]_S01_E01_BreakingBad_1080p_Eng.mkv",
        shadow_message_id=999,
        status=PipelineStatus.SHADOW_ARCHIVED,
    )

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    mock_bot.send_photo = AsyncMock()
    mock_bot.copy_message = AsyncMock()

    mock_db = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = item
    mock_db.execute.return_value = mock_res

    async def mock_get_db():
        yield mock_db

    with patch("src.services.native_batch_engine.get_db_session", return_value=mock_get_db()), \
         patch("src.services.native_batch_engine.settings") as mock_settings:
        mock_settings.SHADOW_CHANNEL_ID = -1004375695262
        mock_settings.get_thumbnail_path.return_value = None
        mock_settings.GLOBAL_THUMBNAIL_PATH = "nonexistent_path.jpg"
        mock_settings.PRESENTATION_FORMAT = "🍿 Title: {title}\n📆 Year: {year}\n📦 Quality: {quality}\n🔊 Audio : {audio}"

        delivered, err = await NativeBatchEngine.handle_start_parameter(mock_bot, 12345, "f_1")
        assert delivered == 1
        assert err == ""
        # Description message was sent
        assert mock_bot.send_message.call_count == 1
        _, kwargs = mock_bot.send_message.call_args
        assert "ʙʀᴇᴀᴋɪɴɢ ʙᴀᴅ" in kwargs.get("text", "") or "Breaking Bad" in kwargs.get("text", "")
        # File was copied
        assert mock_bot.copy_message.call_count == 1


@pytest.mark.asyncio
async def test_handle_start_parameter_description_text_only_with_thumbnail(tmp_path):
    """Verify description is sent as text (send_message) and NEVER as photo, even when thumbnail exists."""
    from unittest.mock import patch

    thumb_file = tmp_path / "global_thumb.jpg"
    thumb_file.write_bytes(b"dummy image bytes")

    item = MediaItem(
        id=2,
        raw_message_id=2,
        raw_channel_id=-1001,
        raw_file_id="fid456",
        file_unique_id="funique456",
        parsed_title="MobLand",
        season_num=1,
        episode_num=1,
        clean_file_name="[TIF]_S01_E01_MobLand_1080p_Eng.mkv",
        shadow_message_id=888,
        custom_thumbnail_path=str(thumb_file),
        status=PipelineStatus.SHADOW_ARCHIVED,
    )

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    mock_bot.send_photo = AsyncMock()
    mock_bot.copy_message = AsyncMock()

    mock_db = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = item
    mock_db.execute.return_value = mock_res

    async def mock_get_db():
        yield mock_db

    with patch("src.services.native_batch_engine.get_db_session", return_value=mock_get_db()), \
         patch("src.services.native_batch_engine.settings") as mock_settings:
        mock_settings.SHADOW_CHANNEL_ID = -1004375695262
        mock_settings.get_thumbnail_path.return_value = thumb_file

        delivered, err = await NativeBatchEngine.handle_start_parameter(mock_bot, 12345, "f_2")
        assert delivered == 1
        assert err == ""
        # Description sent as text message ONLY
        assert mock_bot.send_message.call_count == 1
        # send_photo is NOT called for description
        mock_bot.send_photo.assert_not_called()
        # File was copied
        assert mock_bot.copy_message.call_count == 1


