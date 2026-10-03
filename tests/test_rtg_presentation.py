import pytest
from src.db.models import MediaItem, PipelineStatus
from src.scrapers.regex_engine import RegexEngine


def test_rtg_presentation_format_series():
    """Verify RTG presentation matches the exact template requested for a TV series."""
    item = MediaItem(
        id=1,
        raw_message_id=1,
        raw_channel_id=-1001,
        raw_file_id="fid1",
        file_unique_id="funique1",
        file_size_bytes=1000,
        parsed_title="A Knight of the Seven Kingdoms",
        season_num=1,
        episode_num=1,
        quality_tag="720p",
        codec_tag="x264",
        clean_file_name="[TIF]_S01_E01_AKnightOfTheSevenKingdoms_720p_Hindi_Eng.mkv",
        status=PipelineStatus.SCRAPED,
    )
    # Set explicit year
    setattr(item, "year", "2026")

    item2 = MediaItem(
        id=2,
        raw_message_id=2,
        raw_channel_id=-1001,
        raw_file_id="fid2",
        file_unique_id="funique2",
        file_size_bytes=2000,
        parsed_title="A Knight of the Seven Kingdoms",
        season_num=1,
        episode_num=1,
        quality_tag="1080p",
        codec_tag="x265",
        clean_file_name="[TIF]_S01_E01_AKnightOfTheSevenKingdoms_1080p_Hindi_Eng.mkv",
        status=PipelineStatus.SCRAPED,
    )

    output = RegexEngine.format_rtg_message(item, batch_items=[item, item2])

    assert "🎭 ᴀ ᴋɴɪɢʜᴛ ᴏғ ᴛʜᴇ sᴇᴠᴇɴ ᴋɪɴɢᴅᴏᴍs • 2026" in output
    assert "📂 sᴇᴀsᴏɴ - 1" in output
    assert "🎧 ᴀᴜᴅɪᴏ - ᴇɴɢʟɪsʜ + ʜɪɴᴅɪ" in output
    assert "💬 sᴜʙᴛɪᴛʟᴇs 👍" in output
    assert "🎞 ϙᴜᴀʟɪᴛʏ - ‖ 𝟽𝟸𝟶ᴘ ‖ 𝟷𝟶𝟾𝟶ᴘ ‖" in output
    assert "@TIF_TvSeries11🌹@TIF_WebSeries" in output


def test_rtg_presentation_format_movie_no_season():
    """Verify movies without season omit the season line cleanly."""
    item = MediaItem(
        id=3,
        raw_message_id=3,
        raw_channel_id=-1001,
        raw_file_id="fid3",
        file_unique_id="funique3",
        file_size_bytes=1000,
        parsed_title="Homebound",
        season_num=None,
        episode_num=None,
        quality_tag="1080p",
        codec_tag="x264",
        clean_file_name="Homebound.2025.1080p.Hindi.mkv",
        status=PipelineStatus.SCRAPED,
    )
    setattr(item, "year", "2025")

    output = RegexEngine.format_rtg_message(item)

    assert "🎭 ʜᴏᴍᴇʙᴏᴜɴᴅ • 2025" in output
    assert "📂 sᴇᴀsᴏɴ" not in output
    assert "🎧 ᴀᴜᴅɪᴏ - ʜɪɴᴅɪ" in output
    assert "💬 sᴜʙᴛɪᴛʟᴇs 👍" in output
    assert "🎞 ϙᴜᴀʟɪᴛʏ - ‖ 𝟷𝟶𝟾𝟶ᴘ ‖" in output
    assert "@TIF_TvSeries11🌹@TIF_WebSeries" in output


def test_shadow_description_format_preserved():
    """Verify shadow channel description uses the previous standard format, not the RTG format."""
    item = MediaItem(
        id=4,
        raw_message_id=4,
        raw_channel_id=-1001,
        raw_file_id="fid4",
        file_unique_id="funique4",
        file_size_bytes=1000,
        parsed_title="A Knight of the Seven Kingdoms",
        season_num=1,
        episode_num=1,
        quality_tag="720p",
        codec_tag="x264",
        clean_file_name="[TIF]_S01_E01_AKnightOfTheSevenKingdoms_720p_Hindi_Eng.mkv",
        status=PipelineStatus.SCRAPED,
    )
    setattr(item, "year", "2026")

    output = RegexEngine.format_shadow_description(item)

    assert "🍿 Title: A Knight of the Seven Kingdoms" in output
    assert "📆 Year: 2026" in output
    assert "📦 Quality: 720p" in output
    assert "🔊 Audio : Hindi - English" in output
    assert "💬 English Subtitles 👍" in output

    # Ensure format_presentation aliases format_shadow_description
    assert RegexEngine.format_presentation(item) == output

