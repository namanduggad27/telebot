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


def test_prepare_thumbnail_normalizes_image(tmp_path):
    """Verify prepare_thumbnail creates MTProto-compliant RGB JPEG <= 320x320."""
    from PIL import Image

    # 1. Non-existent returns None
    assert FileIOEngine.prepare_thumbnail(tmp_path / "nonexistent.jpg") is None

    # 2. Large image is resized and converted to JPEG <= 320x320
    large_img = tmp_path / "large.png"
    im = Image.new("RGBA", (1000, 1000), color=(255, 0, 0, 128))
    im.save(large_img, "PNG")

    with patch("src.services.file_io_engine.settings.SCRATCH_DIR", tmp_path / "scratch"):
        thumb_result = FileIOEngine.prepare_thumbnail(large_img)
        assert thumb_result is not None
        result_path = tmp_path / "scratch" / f"thumb_{large_img.stem}.jpg"
        assert result_path.exists()
        with Image.open(result_path) as res_im:
            assert res_im.format == "JPEG"
            assert res_im.mode == "RGB"
            assert max(res_im.size) <= 320


@pytest.mark.asyncio
async def test_process_batch_io_rtg_main_channel_tmdb_poster(tmp_path):
    """Verify that process_batch_io fetches TMDB poster and posts to MAIN_CHANNEL_ID with RTG format."""
    from unittest.mock import MagicMock, AsyncMock
    from src.db.models import MediaItem, PipelineStatus
    from src.services.tmdb_client import TMDBMetadata

    item = MediaItem(
        id=1,
        raw_message_id=10,
        raw_channel_id=-1001,
        raw_file_id="fid1",
        file_unique_id="funique1",
        file_size_bytes=1024,
        parsed_title="A Knight of the Seven Kingdoms",
        season_num=1,
        episode_num=1,
        quality_tag="1080p",
        clean_file_name="[TIF]_S01_E01_AKnightOfTheSevenKingdoms_1080p_Eng.mkv",
        status=PipelineStatus.CONFIRMED,
    )

    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir(exist_ok=True)
    mock_file = scratch_dir / "1_[TIF]_S01_E01_AKnightOfTheSevenKingdoms_1080p_Eng.mkv"
    mock_file.write_text("dummy video")

    mock_client = AsyncMock()
    mock_raw_msg = MagicMock()
    mock_raw_msg.video = MagicMock()
    mock_client.get_messages.return_value = mock_raw_msg
    mock_client.download_media.return_value = str(mock_file)
    mock_sent_doc = MagicMock()
    mock_sent_doc.id = 999
    mock_sent_doc.video = None
    mock_sent_doc.document = MagicMock(file_id="shadow_fid_1")
    mock_client.send_document.return_value = mock_sent_doc
    mock_client.send_message.return_value = MagicMock()

    mock_db = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [item]
    mock_res.scalar_one_or_none.return_value = item
    mock_db.execute.return_value = mock_res
    async def db_gen():
        yield mock_db

    mock_tmdb = AsyncMock()
    mock_tmdb.search_media.return_value = TMDBMetadata(
        tmdb_id=9999,
        title="A Knight of the Seven Kingdoms",
        media_type="tv",
        overview="overview",
        poster_url="https://image.tmdb.org/t/p/w500/sample.jpg",
        backdrop_url=None,
        release_date="2026-01-01",
        vote_average=8.5,
    )
    async def fake_download(url, dest):
        dest.write_bytes(b"mock_poster_bytes")
        return True
    mock_tmdb.download_poster.side_effect = fake_download

    mock_bot = MagicMock()
    mock_bot.send_photo = AsyncMock()
    mock_bot.session.close = AsyncMock()

    with patch("src.services.file_io_engine.settings") as mock_settings, \
         patch("src.services.file_io_engine.get_db_session", side_effect=db_gen), \
         patch("src.services.native_batch_engine.NativeBatchEngine.generate_custom_batch_url", new=AsyncMock(return_value="https://t.me/testbot?start=b_batch123")), \
         patch("src.services.file_io_engine.StateMachine") as mock_sm, \
         patch("src.services.file_io_engine.Client", return_value=mock_client), \
         patch("src.services.tmdb_client.TMDBClient", return_value=mock_tmdb), \
         patch("aiogram.Bot", return_value=mock_bot):

        mock_settings.TG_API_ID = 12345
        mock_settings.TG_API_HASH = "hash"
        mock_settings.SCRATCH_DIR = scratch_dir
        mock_settings.BASE_DIR = tmp_path
        mock_settings.SHADOW_CHANNEL_ID = -1009999
        mock_settings.MAIN_CHANNEL_ID = -1008888
        mock_settings.ADMIN_BOT_TOKEN = "bot_token"
        mock_settings.TG_USERBOT_SESSION = "test_session"
        mock_settings.RENAME_FORMAT = "[TIF]_S{season:02d}_E{episode:02d}_{clean_title}_{quality}_{audio}.{ext}"
        mock_settings.CAPTION_FORMAT = "caption"
        mock_settings.PRESENTATION_FORMAT = (
            "🍿 Title: {title}\n📆 Year: {year}\n📦 Quality: {quality}\n🔊 Audio : {audio}\n💬 English Subtitles 👍"
        )
        mock_settings.RTG_MESSAGE_FORMAT = (
            "🎭 {title} • {year}\n{season_line}🎧 ᴀᴜᴅɪᴏ - {audio}\n💬 sᴜʙᴛɪᴛʟᴇs 👍\n\n"
            "🎞 ϙᴜᴀʟɪᴛʏ - {quality}\n\n༺━━━━━━━━━━━━━━━༻\n@TIF_TvSeries11🌹@TIF_WebSeries"
        )
        mock_settings.get_thumbnail_path.return_value = None

        mock_sm.transition_item = AsyncMock(return_value=True)

        res = await FileIOEngine.process_batch_io([1])
        assert res is True

        # Verify Shadow Channel received previous shadow description format
        mock_client.send_message.assert_called()
        shadow_call_kwargs = mock_client.send_message.call_args[1]
        assert shadow_call_kwargs["chat_id"] == -1009999
        assert "🍿 Title: A Knight of the Seven Kingdoms" in shadow_call_kwargs["text"]

        # Verify Bot sent photo to MAIN_CHANNEL_ID (RTG channel) with TMDB poster
        assert mock_bot.send_photo.call_count == 1
        _, kwargs = mock_bot.send_photo.call_args
        assert kwargs.get("chat_id") == -1008888
        caption = kwargs.get("caption", "")
        assert "🎭 ᴀ ᴋɴɪɢʜᴛ ᴏғ ᴛʜᴇ sᴇᴠᴇɴ ᴋɪɴɢᴅᴏᴍs" in caption
        assert "BATCH LINK" in caption


@pytest.mark.asyncio
async def test_process_batch_io_rtg_main_channel_no_tmdb_poster(tmp_path):
    """Verify that when TMDB has no poster, RTG message is sent as text-only (no photo, no thumbnail fallback)."""
    from unittest.mock import MagicMock, AsyncMock, patch
    from src.db.models import MediaItem, PipelineStatus
    from src.services.tmdb_client import TMDBMetadata

    item = MediaItem(
        id=2,
        raw_message_id=20,
        raw_channel_id=-1001,
        raw_file_id="fid2",
        file_unique_id="funique2",
        file_size_bytes=1024,
        parsed_title="Homebound",
        season_num=None,
        episode_num=None,
        quality_tag="1080p",
        clean_file_name="Homebound.2025.1080p.Hindi.mkv",
        status=PipelineStatus.CONFIRMED,
    )

    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir(exist_ok=True)
    mock_file = scratch_dir / "2_Homebound.mkv"
    mock_file.write_text("dummy video")

    mock_client = AsyncMock()
    mock_raw_msg = MagicMock()
    mock_raw_msg.video = MagicMock()
    mock_client.get_messages.return_value = mock_raw_msg
    mock_client.download_media.return_value = str(mock_file)
    mock_sent_doc = MagicMock()
    mock_sent_doc.id = 999
    mock_sent_doc.video = None
    mock_sent_doc.document = MagicMock(file_id="shadow_fid_2")
    mock_client.send_document.return_value = mock_sent_doc
    mock_client.send_message.return_value = MagicMock()

    mock_db = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [item]
    mock_res.scalar_one_or_none.return_value = item
    mock_db.execute.return_value = mock_res
    async def db_gen():
        yield mock_db

    mock_tmdb = AsyncMock()
    # TMDB returns metadata WITHOUT a poster
    mock_tmdb.search_media.return_value = TMDBMetadata(
        tmdb_id=8888,
        title="Homebound",
        media_type="movie",
        overview="overview",
        poster_url=None,
        backdrop_url=None,
        release_date="2025-01-01",
        vote_average=7.0,
    )

    mock_bot = MagicMock()
    mock_bot.send_photo = AsyncMock()
    mock_bot.send_message = AsyncMock()
    mock_bot.session.close = AsyncMock()

    # Even if global thumbnail exists, it must NOT be used for RTG message
    dummy_thumb = tmp_path / "thumb.jpg"
    dummy_thumb.write_bytes(b"thumb_bytes")

    with patch("src.services.file_io_engine.settings") as mock_settings, \
         patch("src.services.file_io_engine.get_db_session", side_effect=db_gen), \
         patch("src.services.native_batch_engine.NativeBatchEngine.generate_custom_batch_url", new=AsyncMock(return_value="https://t.me/testbot?start=b_batch123")), \
         patch("src.services.file_io_engine.StateMachine") as mock_sm, \
         patch("src.services.file_io_engine.Client", return_value=mock_client), \
         patch("src.services.tmdb_client.TMDBClient", return_value=mock_tmdb), \
         patch("aiogram.Bot", return_value=mock_bot):

        mock_settings.TG_API_ID = 12345
        mock_settings.TG_API_HASH = "hash"
        mock_settings.SCRATCH_DIR = scratch_dir
        mock_settings.BASE_DIR = tmp_path
        mock_settings.SHADOW_CHANNEL_ID = -1009999
        mock_settings.MAIN_CHANNEL_ID = -1008888
        mock_settings.ADMIN_BOT_TOKEN = "bot_token"
        mock_settings.TG_USERBOT_SESSION = "test_session"
        mock_settings.RENAME_FORMAT = "{clean_title}.{ext}"
        mock_settings.CAPTION_FORMAT = "caption"
        mock_settings.PRESENTATION_FORMAT = (
            "🍿 Title: {title}\n📆 Year: {year}\n📦 Quality: {quality}\n🔊 Audio : {audio}\n💬 English Subtitles 👍"
        )
        mock_settings.RTG_MESSAGE_FORMAT = (
            "🎭 {title} • {year}\n{season_line}🎧 ᴀᴜᴅɪᴏ - {audio}\n💬 sᴜʙᴛɪᴛʟᴇs 👍\n\n"
            "🎞 ϙᴜᴀʟɪᴛʏ - {quality}\n\n༺━━━━━━━━━━━━━━━༻\n@TIF_TvSeries11🌹@TIF_WebSeries"
        )
        mock_settings.get_thumbnail_path.return_value = dummy_thumb

        mock_sm.transition_item = AsyncMock(return_value=True)

        res = await FileIOEngine.process_batch_io([2])
        assert res is True

        # send_photo MUST NOT be called because TMDB poster is None
        assert mock_bot.send_photo.call_count == 0

        # send_message MUST be called with the text-only RTG message
        assert mock_bot.send_message.call_count == 1
        _, kwargs = mock_bot.send_message.call_args
        assert kwargs.get("chat_id") == -1008888
        text = kwargs.get("text", "")
        assert "🎭 ʜᴏᴍᴇʙᴏᴜɴᴅ • 2025" in text
        assert "BATCH LINK" in text



