import pytest
from unittest.mock import AsyncMock, patch
from hydrogram.errors import FloodWait
from src.services.file_io_engine import FileIOEngine


@pytest.mark.asyncio
async def test_safe_mtproto_action_retry_on_floodwait():
    """Verify that _safe_mtproto_action catches FloodWait and retries automatically without failing."""
    mock_coro = AsyncMock()
    # First invocation raises FloodWait(value=1), second invocation succeeds returning "SUCCESS"
    flood_exc = FloodWait("FloodWait")
    flood_exc.value = 0.05  # Sleep 50ms for test speed
    mock_coro.side_effect = [flood_exc, "SUCCESS"]

    result = await FileIOEngine._safe_mtproto_action(mock_coro)
    assert result == "SUCCESS"
    assert mock_coro.call_count == 2


@pytest.mark.asyncio
async def test_process_item_io_parse_mode_enum(tmp_path):
    """Verify that send_video and send_document receive ParseMode.MARKDOWN enum rather than string."""
    from unittest.mock import MagicMock
    from hydrogram.enums import ParseMode
    from src.db.models import MediaItem, PipelineStatus

    # Create dummy MediaItem
    item = MediaItem(
        id=10,
        raw_message_id=100,
        raw_channel_id=-100123456789,
        raw_file_id="dummy_file_id",
        file_unique_id="dummy_unique",
        file_size_bytes=1024,
        parsed_title="Dummy Movie",
        clean_file_name="Dummy.Movie.mkv",
        status=PipelineStatus.QUEUED_FOR_IO,
    )

    # Create dummy scratch file
    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir(exist_ok=True)
    scratch_file = scratch_dir / "Dummy.Movie.mkv"
    scratch_file.write_text("dummy video content")

    mock_client = AsyncMock()
    mock_raw_msg = MagicMock()
    mock_raw_msg.video = MagicMock()
    mock_client.get_messages.return_value = mock_raw_msg
    mock_client.download_media.return_value = str(scratch_file)
    mock_client.send_document.return_value = MagicMock()

    with patch("src.services.file_io_engine.settings") as mock_settings, \
         patch("src.services.file_io_engine.get_db_session") as mock_get_db, \
         patch("src.services.file_io_engine.StateMachine") as mock_state_machine, \
         patch("src.services.file_io_engine.Client", return_value=mock_client):
        
        mock_settings.TG_API_ID = 12345
        mock_settings.TG_API_HASH = "hash"
        mock_settings.SCRATCH_DIR = scratch_dir
        mock_settings.BASE_DIR = tmp_path
        mock_settings.SHADOW_CHANNEL_ID = -100999999999
        mock_settings.TG_USERBOT_SESSION = "test_session"
        mock_settings.MAX_CONCURRENT_TRANSFERS = 2

        # Mock db session
        mock_db = AsyncMock()
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = item
        mock_db.execute.return_value = mock_res
        async def db_gen():
            yield mock_db
        mock_get_db.return_value = db_gen()

        mock_state_machine.get_cached_state = AsyncMock(return_value={})
        mock_state_machine.transition_item = AsyncMock(return_value=True)

        result = await FileIOEngine.process_item_io("10")
        assert result is True
        assert mock_client.send_document.call_count == 1
        _, kwargs = mock_client.send_document.call_args
        assert kwargs.get("parse_mode") == ParseMode.MARKDOWN
        assert not isinstance(kwargs.get("parse_mode"), str)
