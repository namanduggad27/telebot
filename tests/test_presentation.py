import pytest
from src.db.models import MediaItem, PipelineStatus
from src.services.presentation import generate_presentation_post, stylize_title


def test_stylize_title():
    """Verify clean styling of show title strings."""
    raw = "  breaking   bad  "
    styled = stylize_title(raw)
    assert "breaking" in styled
    assert "bad" in styled


def test_generate_presentation_post():
    """Verify complete Main Channel presentation caption formatting and inline buttons."""
    item = MediaItem(
        id="123e4567-e89b-12d3-a456-426614174000",
        raw_message_id=1,
        raw_channel_id=-1001,
        raw_file_id="fid123",
        file_unique_id="funique123",
        file_size_bytes=1500000000,
        parsed_title="Severance",
        season_num=1,
        episode_num=9,
        quality_tag="2160P WEB-DL",
        codec_tag="x265",
        clean_file_name="Severance - S01E09.mkv",
        status=PipelineStatus.SHADOW_ARCHIVED,
        tmdb_id=95557,
    )

    tmdb_info = {
        "vote_average": 8.7,
        "release_date": "2022-02-18",
        "overview": "Mark leads a team of office workers whose memories have been surgically divided between their work and personal lives.",
    }

    caption, keyboard = generate_presentation_post(item, tmdb_info, batch_link_url="https://t.me/LinksBot?start=batch123")

    assert "SEVERANCE" in caption
    assert "(2022)" in caption
    assert "SEASON 01 • EPISODE 09" in caption
    assert "8.7 / 10" in caption
    assert "2160P WEB-DL (x265)" in caption
    assert "1.4 GB" in caption
    assert len(keyboard.inline_keyboard) == 2
    assert keyboard.inline_keyboard[0][0].url == "https://t.me/LinksBot?start=batch123"
    assert "themoviedb.org/tv/95557" in keyboard.inline_keyboard[1][0].url
