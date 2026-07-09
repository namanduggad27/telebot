import logging
from typing import Any, Dict, Optional
from aiogram import Bot, Dispatcher
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config.settings import settings
from src.db.models import MediaItem

logger = logging.getLogger("bot.admin_bot")

bot: Optional[Bot] = None
dp = Dispatcher()


def get_bot() -> Optional[Bot]:
    """Get or initialize the aiogram Bot instance for Admin HITL notifications."""
    global bot
    if not settings.ADMIN_BOT_TOKEN:
        logger.warning("ADMIN_BOT_TOKEN not provided in settings. Control Bot will not start.")
        return None
    if bot is None:
        bot = Bot(token=settings.ADMIN_BOT_TOKEN)
    return bot


def build_confirmation_keyboard(item_id: str) -> InlineKeyboardMarkup:
    """Build inline keyboard markup with Approve, Edit, and Reject buttons for a media item."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Approve & Download", callback_data=f"approve:{item_id}"),
            ],
            [
                InlineKeyboardButton(text="✏️ Edit Title/Season", callback_data=f"edit:{item_id}"),
                InlineKeyboardButton(text="❌ Reject", callback_data=f"reject:{item_id}"),
            ],
        ]
    )


async def send_confirmation_card(item: MediaItem, tmdb_info: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Send a rich media review card to the Admin User ID with TMDB poster and interactive approval buttons."""
    bot_instance = get_bot()
    if not bot_instance or not settings.ADMIN_USER_ID:
        logger.warning(
            f"Cannot send confirmation card for ID={item.id}: Bot or ADMIN_USER_ID not configured."
        )
        return None

    title = item.parsed_title or "Unknown Title"
    season_str = f"S{item.season_num:02d}" if item.season_num is not None else "N/A"
    episode_str = f"E{item.episode_num:02d}" if item.episode_num is not None else "N/A"
    quality_str = item.quality_tag or "Standard"
    clean_name = item.clean_file_name or f"{title}.mkv"
    size_mb = round((item.file_size_bytes or 0) / (1024 * 1024), 2)

    poster_url = tmdb_info.get("poster_url") if tmdb_info else None
    overview = tmdb_info.get("overview") if tmdb_info else "No TMDB overview available."
    vote_avg = tmdb_info.get("vote_average", 0.0) if tmdb_info else 0.0

    caption = (
        f"🎬 **NEW MEDIA SCRAPED & ENRICHED**\n\n"
        f"📌 **Title:** `{title}`\n"
        f"📺 **Season/Episode:** `{season_str} / {episode_str}`\n"
        f"⭐ **TMDB Rating:** `{vote_avg}/10`\n"
        f"⚙️ **Quality Tag:** `{quality_str}`\n"
        f"💾 **File Size:** `{size_mb} MB`\n"
        f"📂 **Suggested Clean Name:**\n`{clean_name}`\n\n"
        f"📖 **Overview:**\n_{overview[:350]}..._\n\n"
        f"⚡ **Action Required:** Please review metadata and click approve or edit below."
    )

    keyboard = build_confirmation_keyboard(str(item.id))

    try:
        if poster_url:
            message = await bot_instance.send_photo(
                chat_id=settings.ADMIN_USER_ID,
                photo=poster_url,
                caption=caption,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        else:
            message = await bot_instance.send_message(
                chat_id=settings.ADMIN_USER_ID,
                text=caption,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        logger.info(f"Sent confirmation card for ID={item.id} to Admin={settings.ADMIN_USER_ID}")
        return message.message_id
    except Exception as e:
        logger.error(f"Failed to send confirmation card for ID={item.id}: {e}", exc_info=True)
        return None
