import logging
from typing import Any, Dict, Optional
from aiogram import Bot, Dispatcher
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeChat,
)

from config.settings import settings
from src.db.models import MediaItem

logger = logging.getLogger("bot.admin_bot")

from src.bot.handlers import router as bot_router

bot: Optional[Bot] = None
dp = Dispatcher()
dp.include_router(bot_router)


def get_bot() -> Optional[Bot]:
    """Get or initialize the aiogram Bot instance for Admin HITL notifications."""
    global bot
    if not settings.ADMIN_BOT_TOKEN:
        logger.warning("ADMIN_BOT_TOKEN not provided in settings. Control Bot will not start.")
        return None
    if bot is None:
        bot = Bot(token=settings.ADMIN_BOT_TOKEN)
    return bot


async def setup_bot_commands(bot_instance: Bot) -> None:
    """Register native Telegram menu button commands for regular users and admins."""
    # Only actually functioning commands for users
    user_commands = [
        BotCommand(command="start", description="Start bot & instructions"),
        BotCommand(command="help", description="How to download media files"),
    ]

    # Only actually functioning commands for admins
    admin_commands = [
        BotCommand(command="start", description="Open media library & channels"),
        BotCommand(command="files", description="View and manage pending media files"),
        BotCommand(command="thumbnail", description="View or manage permanent thumbnail"),
        BotCommand(command="setformat", description="Configure file renaming format"),
        BotCommand(command="setcaption", description="Configure video caption format"),
        BotCommand(command="setpresentation", description="Configure channel presentation post format"),
        BotCommand(command="help", description="Admin command guide & instructions"),
        BotCommand(command="cancel", description="Cancel active prompt or rename"),
    ]

    try:
        # Default menu for public users
        await bot_instance.set_my_commands(user_commands, scope=BotCommandScopeDefault())

        # Scoped menu for authorized admins
        for admin_id in settings.all_admin_ids:
            try:
                await bot_instance.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
            except Exception as e:
                logger.debug(f"Could not set admin command menu for ID={admin_id}: {e}")

        logger.info("Telegram native command menu buttons configured successfully.")
    except Exception as e:
        logger.warning(f"Failed to register Telegram command menu: {e}")


async def start_admin_bot() -> None:
    """Start polling for Admin Bot interactive review card callbacks and deep links."""
    bot_instance = get_bot()
    if bot_instance:
        await setup_bot_commands(bot_instance)
        logger.info("Starting Aiogram Admin Bot polling (`Approve`/`Edit`/`Reject` handlers active)...")
        await dp.start_polling(bot_instance)


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


async def send_confirmation_card(item: MediaItem, omdb_info: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Send a rich media review card to all configured Admin User IDs with OMDB poster and interactive approval buttons."""
    bot_instance = get_bot()
    admin_ids = settings.all_admin_ids
    if not bot_instance or not admin_ids:
        logger.warning(
            f"Cannot send confirmation card for ID={item.id}: Bot or ADMIN_USER_IDS not configured."
        )
        return None

    title = item.parsed_title or "Unknown Title"
    season_str = f"S{item.season_num:02d}" if item.season_num is not None else "N/A"
    episode_str = f"E{item.episode_num:02d}" if item.episode_num is not None else "N/A"
    quality_str = item.quality_tag or "Standard"
    clean_name = item.clean_file_name or f"{title}.mkv"
    size_mb = round((item.file_size_bytes or 0) / (1024 * 1024), 2)

    poster_url = omdb_info.get("poster_url") if omdb_info else None
    overview = omdb_info.get("overview") if omdb_info else "No OMDB overview available."
    vote_avg = omdb_info.get("vote_average", 0.0) if omdb_info else 0.0

    caption = (
        f"🎬 **NEW MEDIA SCRAPED & ENRICHED**\n\n"
        f"📌 **Title:** `{title}`\n"
        f"📺 **Season/Episode:** `{season_str} / {episode_str}`\n"
        f"⭐ **OMDB Rating:** `{vote_avg}/10`\n"
        f"⚙️ **Quality Tag:** `{quality_str}`\n"
        f"💾 **File Size:** `{size_mb} MB`\n"
        f"📂 **Suggested Clean Name:**\n`{clean_name}`\n\n"
        f"📖 **Overview:**\n_{overview[:350]}..._\n\n"
        f"⚡ **Action Required:** Please review metadata and click approve or edit below."
    )

    keyboard = build_confirmation_keyboard(str(item.id))
    last_msg_id = None

    for admin_id in admin_ids:
        try:
            if poster_url:
                message = await bot_instance.send_photo(
                    chat_id=admin_id,
                    photo=poster_url,
                    caption=caption,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
            else:
                message = await bot_instance.send_message(
                    chat_id=admin_id,
                    text=caption,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
            last_msg_id = message.message_id
            logger.info(f"Sent confirmation card for ID={item.id} to Admin={admin_id}")
        except Exception as e:
            logger.error(f"Failed to send confirmation card for ID={item.id} to Admin={admin_id}: {e}", exc_info=True)

    return last_msg_id

