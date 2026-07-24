import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config.settings import settings
from src.db.models import PipelineStatus
from src.services.state_machine import StateMachine
from src.services.native_batch_engine import NativeBatchEngine

logger = logging.getLogger("bot.handlers")
router = Router()


class EditTitleState(StatesGroup):
    """FSM states for interactive admin title editing."""
    waiting_for_new_title = State()


class GroupManageState(StatesGroup):
    """FSM states for interactive grouped media library management."""
    waiting_for_show_rename = State()
    waiting_for_file_rename = State()
    waiting_for_thumbnail = State()


class GlobalThumbState(StatesGroup):
    """FSM state for setting the global thumbnail."""
    waiting_for_global_thumbnail = State()

class ConfigState(StatesGroup):
    """FSM state for dynamic settings configuration."""
    waiting_for_rename_format = State()
    waiting_for_caption_format = State()
    waiting_for_presentation_format = State()



async def send_channel_list(target) -> None:
    from sqlalchemy import select
    from src.db.session import get_db_session
    from src.db.models import MediaItem, PipelineStatus
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    items = []
    async for db in get_db_session():
        stmt = select(MediaItem).where(
            MediaItem.status.in_([PipelineStatus.SCRAPED, PipelineStatus.ENRICHED]),
            MediaItem.shadow_message_id.is_(None)
        ).order_by(MediaItem.created_at.desc())
        res = await db.execute(stmt)
        items = list(res.scalars().all())
        break

    if not items:
        text = "📂 **No Pending Media Files Found**\nDrop files into your raw channel."
        if hasattr(target, 'message') and target.message:
            await target.message.edit_text(text, parse_mode="Markdown")
        else:
            await target.reply(text, parse_mode="Markdown")
        return

    groups = {}
    for item in items:
        groups.setdefault(item.raw_channel_id, []).append(item)

    text = f"📦 **Library Channels (`{len(items)}` total files)**\nSelect a raw channel to view unprocessed files:\n\n"
    buttons = []
    for chan_id, grp_items in groups.items():
        text += f"▪️ **Channel ID:** `{chan_id}` — `{len(grp_items)}` file(s)\n"
        buttons.append([InlineKeyboardButton(
            text=f"📁 Channel: {chan_id} ({len(grp_items)})",
            callback_data=f"c_view:{chan_id}"
        )])

    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    if hasattr(target, 'message') and target.message:
        await target.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await target.reply(text, reply_markup=markup, parse_mode="Markdown")

@router.message(Command("start"))
async def on_start_command(message: Message) -> None:
    text = (message.text or "").strip()
    parts = text.split(" ")
    if len(parts) >= 2:
        param = parts[1].strip()
        delivered = await NativeBatchEngine.handle_start_parameter(message.bot, message.chat.id, param)
        if delivered > 0:
            await message.reply(f"✨ **Delivered {delivered} file(s)!**", parse_mode="Markdown")
        else:
            await message.reply("❌ **Media Not Found**", parse_mode="Markdown")
        return

    if settings.ADMIN_USER_ID and message.from_user and message.from_user.id == settings.ADMIN_USER_ID:
        await send_channel_list(message)
    else:
        await message.reply("👋 **Welcome!**")

@router.message(Command("files", "list", "manage"))
async def on_files_command(message: Message) -> None:
    if settings.ADMIN_USER_ID and message.from_user and message.from_user.id != settings.ADMIN_USER_ID:
        return
    await send_channel_list(message)

async def _render_channel_view(callback: CallbackQuery, state: FSMContext, chan_id: str) -> None:
    from sqlalchemy import select
    from src.db.session import get_db_session
    from src.db.models import MediaItem, PipelineStatus
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from aiogram.exceptions import TelegramBadRequest

    async for db in get_db_session():
        stmt = select(MediaItem).where(
            MediaItem.raw_channel_id == int(chan_id),
            MediaItem.status.in_([PipelineStatus.SCRAPED, PipelineStatus.ENRICHED]),
            MediaItem.shadow_message_id.is_(None)
        ).order_by(MediaItem.created_at.desc())
        res = await db.execute(stmt)
        items = list(res.scalars().all())
        break
        
    data = await state.get_data()
    selected_items = data.get("selected_items", [])
    
    text = f"📁 **Channel `{chan_id}` Files**\nSelect files to process as a batch:\n\n"
    buttons = []
    
    for item in items:
        is_sel = item.id in selected_items
        mark = "✅" if is_sel else "❌"
        buttons.append([InlineKeyboardButton(
            text=f"{mark} {item.parsed_title} S{item.season_num or 1:02d}E{item.episode_num or 1:02d}",
            callback_data=f"toggle:{item.id}:{chan_id}"
        )])
        
    buttons.append([InlineKeyboardButton(text="👁️ Preview Selected", callback_data=f"preview:{chan_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Back to Channels", callback_data="c_back")])
    
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e
            
    await callback.answer()

@router.callback_query(F.data.startswith("c_view:"))
async def on_channel_view(callback: CallbackQuery, state: FSMContext) -> None:
    chan_id = callback.data.split(":")[1]
    await _render_channel_view(callback, state, chan_id)

@router.callback_query(F.data == "c_back")
async def on_c_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(selected_items=[])
    await send_channel_list(callback)
    await callback.answer()

@router.callback_query(F.data.startswith("toggle:"))
async def on_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    item_id = int(parts[1])
    chan_id = parts[2]
    
    data = await state.get_data()
    selected = set(data.get("selected_items", []))
    
    if item_id in selected:
        selected.remove(item_id)
    else:
        selected.add(item_id)
        
    await state.update_data(selected_items=list(selected))
    
    await _render_channel_view(callback, state, chan_id)

@router.callback_query(F.data.startswith("preview:"))
async def on_preview(callback: CallbackQuery, state: FSMContext) -> None:
    chan_id = callback.data.split(":")[1]
    data = await state.get_data()
    selected_items = data.get("selected_items", [])
    
    if not selected_items:
        await callback.answer("⚠️ No files selected!", show_alert=True)
        return
        
    from sqlalchemy import select
    from src.db.session import get_db_session
    from src.db.models import MediaItem
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from src.scrapers.regex_engine import RegexEngine
    
    async for db in get_db_session():
        stmt = select(MediaItem).where(MediaItem.id.in_(selected_items)).order_by(MediaItem.episode_num)
        res = await db.execute(stmt)
        items = list(res.scalars().all())
        break
        
    if not items:
        await callback.answer("Items not found.", show_alert=True)
        return
        
    first_item = items[0]
    
    pres_text = settings.PRESENTATION_FORMAT.format(
        title=first_item.parsed_title or "Unknown",
        year="2024",
        quality=first_item.quality_tag or "1080p",
        audio="Hindi"
    )
    
    preview_text = f"👁️ **BATCH PREVIEW**\n\n**Presentation Message:**\n```\n{pres_text}\n```\n\n**Files to rename:**\n"
    for item in items:
        preview_text += f"- `{RegexEngine.format_custom_name(item, settings.RENAME_FORMAT)}`\n"
        
    buttons = [
        [InlineKeyboardButton(text="✅ Confirm & Process", callback_data=f"process_batch:{chan_id}")],
        [InlineKeyboardButton(text="🔙 Back", callback_data=f"c_view:{chan_id}")]
    ]
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await callback.message.edit_text(preview_text, reply_markup=markup, parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data.startswith("process_batch:"))
async def on_process_batch(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected_items = data.get("selected_items", [])
    
    if not selected_items:
        await callback.answer("No items selected.", show_alert=True)
        return
        
    from sqlalchemy import select
    from src.db.session import get_db_session
    from src.db.models import MediaItem, PipelineStatus
    
    async for db in get_db_session():
        stmt = select(MediaItem).where(MediaItem.id.in_(selected_items))
        res = await db.execute(stmt)
        items = list(res.scalars().all())
        for i in items:
            i.status = PipelineStatus.CONFIRMED
        await db.commit()
        break
        
    from arq import create_pool
    from arq.connections import RedisSettings
    redis_pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    
    await redis_pool.enqueue_job("process_batch_io_task", selected_items)
    await redis_pool.aclose()
    
    await state.update_data(selected_items=[])
    
    await callback.message.edit_text(f"🚀 **Batch Queued for Processing!**\n`{len(selected_items)}` files sent to pipeline.", parse_mode="Markdown")
    await callback.answer()

@router.message(Command("setformat"))
async def on_setformat_command(message: Message, state: FSMContext) -> None:
    if settings.ADMIN_USER_ID and message.from_user and message.from_user.id != settings.ADMIN_USER_ID:
        return
    await state.set_state(ConfigState.waiting_for_rename_format)
    await message.reply(
        "🛠️ **Set Rename Format**\n\n"
        "Available tags: `{title}`, `{season}`, `{episode}`, `{quality}`, `{ext}`\n"
        f"Current: `{settings.RENAME_FORMAT}`\n\n"
        "Send the new format, or `/cancel` to abort.",
        parse_mode="Markdown"
    )

@router.message(ConfigState.waiting_for_rename_format)
async def on_rename_format_received(message: Message, state: FSMContext) -> None:
    if settings.ADMIN_USER_ID and message.from_user and message.from_user.id != settings.ADMIN_USER_ID:
        return
    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.reply("Cancelled.")
        return
    settings.RENAME_FORMAT = message.text.strip()
    settings.save_dynamic_config()
    await state.clear()
    await message.reply(f"✅ **Rename format saved!**\n\nNew format: `{settings.RENAME_FORMAT}`", parse_mode="Markdown")


@router.message(Command("setcaption"))
async def on_setcaption_command(message: Message, state: FSMContext) -> None:
    if settings.ADMIN_USER_ID and message.from_user and message.from_user.id != settings.ADMIN_USER_ID:
        return
    await state.set_state(ConfigState.waiting_for_caption_format)
    await message.reply(
        "📝 **Set Caption Format**\n\n"
        "Available tags: `{title}`, `{season}`, `{episode}`, `{quality}`, `{filename}`\n"
        f"Current:\n`{settings.CAPTION_FORMAT}`\n\n"
        "Send the new format, or `/cancel` to abort.",
        parse_mode="Markdown"
    )

@router.message(ConfigState.waiting_for_caption_format)
async def on_caption_format_received(message: Message, state: FSMContext) -> None:
    if settings.ADMIN_USER_ID and message.from_user and message.from_user.id != settings.ADMIN_USER_ID:
        return
    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.reply("Cancelled.")
        return
    settings.CAPTION_FORMAT = message.text.strip()
    settings.save_dynamic_config()
    await state.clear()
    await message.reply(f"✅ **Caption format saved!**\n\nNew format:\n`{settings.CAPTION_FORMAT}`", parse_mode="Markdown")


@router.message(Command("setpresentation"))
async def on_setpresentation_command(message: Message, state: FSMContext) -> None:
    if settings.ADMIN_USER_ID and message.from_user and message.from_user.id != settings.ADMIN_USER_ID:
        return
    await state.set_state(ConfigState.waiting_for_presentation_format)
    await message.reply(
        "📰 **Set Presentation Format**\n\n"
        "Available tags: `{title}`, `{year}`, `{quality}`, `{audio}`\n"
        f"Current:\n`{settings.PRESENTATION_FORMAT}`\n\n"
        "Send the new format, or `/cancel` to abort.",
        parse_mode="Markdown"
    )

@router.message(ConfigState.waiting_for_presentation_format)
async def on_presentation_format_received(message: Message, state: FSMContext) -> None:
    if settings.ADMIN_USER_ID and message.from_user and message.from_user.id != settings.ADMIN_USER_ID:
        return
    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.reply("Cancelled.")
        return
    settings.PRESENTATION_FORMAT = message.text.strip()
    settings.save_dynamic_config()
    await state.clear()
    await message.reply(f"✅ **Presentation format saved!**\n\nNew format:\n`{settings.PRESENTATION_FORMAT}`", parse_mode="Markdown")
