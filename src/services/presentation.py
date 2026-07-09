import logging
from typing import Any, Dict, Tuple, Optional
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from src.db.models import MediaItem

logger = logging.getLogger("services.presentation")


# Mapping of standard ASCII to stylized Unicode characters to thwart basic regex/text crawlers
STYLIZED_MAP = {
    'A': 'A', 'B': 'B', 'C': 'C', 'D': 'D', 'E': 'E', 'F': 'F', 'G': 'G', 'H': 'H',
    'I': 'I', 'J': 'J', 'K': 'K', 'L': 'L', 'M': 'M', 'N': 'N', 'O': 'O', 'P': 'P',
    'Q': 'Q', 'R': 'R', 'S': 'S', 'T': 'T', 'U': 'U', 'V': 'V', 'W': 'W', 'X': 'X',
    'Y': 'Y', 'Z': 'Z',
    'a': 'a', 'b': 'b', 'c': 'c', 'd': 'd', 'e': 'e', 'f': 'f', 'g': 'g', 'h': 'h',
    'i': 'i', 'j': 'j', 'k': 'k', 'l': 'l', 'm': 'm', 'n': 'n', 'o': 'o', 'p': 'p',
    'q': 'q', 'r': 'r', 's': 's', 't': 't', 'u': 'u', 'v': 'v', 'w': 'w', 'x': 'x',
    'y': 'y', 'z': 'z',
}


def stylize_title(title: str) -> str:
    """Apply subtle anti-crawler Unicode styling or clean formatting to the title string."""
    # We insert a zero-width non-joiner (\u200c) or clean markdown formatting to thwart simple scrapers
    clean = title.strip()
    return " \u200b".join(clean.split())


def generate_presentation_post(
    item: MediaItem,
    tmdb_info: Optional[Dict[str, Any]],
    batch_link_url: str,
) -> Tuple[str, InlineKeyboardMarkup]:
    """Generate a rich, anti-crawler presentation caption and glowing inline action buttons for the Main Channel."""
    title = item.parsed_title or "Unknown Release"
    styled_title = stylize_title(title.upper())

    season_num = item.season_num
    episode_num = item.episode_num
    se_badge = ""
    if season_num is not None and episode_num is not None:
        se_badge = f"SEASON {season_num:02d} • EPISODE {episode_num:02d}"
    elif season_num is not None:
        se_badge = f"COMPLETE SEASON {season_num:02d}"
    else:
        se_badge = "FULL FEATURE RELEASE"

    rating = tmdb_info.get("vote_average", 0.0) if tmdb_info else 0.0
    release_date = tmdb_info.get("release_date", "") if tmdb_info else ""
    year_str = release_date[:4] if release_date and len(release_date) >= 4 else "2024"
    overview = tmdb_info.get("overview", "") if tmdb_info else ""
    if len(overview) > 400:
        overview = overview[:397] + "..."

    quality_str = item.quality_tag or "HD 1080P"
    codec_str = item.codec_tag or "H.264"
    size_mb = round((item.file_size_bytes or 0) / (1024 * 1024), 2)
    size_str = f"{size_mb} MB" if size_mb < 1000 else f"{round(size_mb/1024, 2)} GB"

    caption = (
        f"🎬 **{styled_title}** ({year_str})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💫 **Format:** `{se_badge}`\n"
        f"⭐ **TMDB Rating:** `{rating} / 10`\n"
        f"⚡ **Quality:** `{quality_str} ({codec_str})`\n"
        f"💾 **Size:** `{size_str}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📖 **Synopsis:**\n_{overview or 'Experience the thrilling new release now available in high definition.'}_\n\n"
        f"🔒 **Verified & Clean Audio/Video**\n"
        f"👇 _Click below to stream or download instantly inside Telegram!_"
    )

    tmdb_id = item.tmdb_id
    tmdb_url = f"https://www.themoviedb.org/tv/{tmdb_id}" if tmdb_id else "https://www.themoviedb.org/"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚡ STREAM / DOWNLOAD NOW ⚡", url=batch_link_url),
            ],
            [
                InlineKeyboardButton(text="🎬 TMDB Page", url=tmdb_url),
                InlineKeyboardButton(text="🔔 Join Main Channel", url="https://t.me/telegram"),
            ],
        ]
    )

    return caption, keyboard
