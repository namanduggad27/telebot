import logging
from pathlib import Path
from typing import Optional, List
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton, User, FSInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select

from config.settings import settings
from src.db.models import MediaItem, PipelineStatus, BatchLink
from src.db.session import get_db_session
from src.services.native_batch_engine import NativeBatchEngine
from src.scrapers.regex_engine import RegexEngine

logger = logging.getLogger("bot.handlers")
router = Router()


# ==============================================================================
# FSM State Definitions
# ==============================================================================

class EditMediaState(StatesGroup):
    """FSM states for interactive admin media and batch title editing."""
    waiting_for_batch_rename = State()
    waiting_for_card_title = State()


class ConfigState(StatesGroup):
    """FSM states for dynamic settings configuration."""
    waiting_for_rename_format = State()
    waiting_for_caption_format = State()
    waiting_for_presentation_format = State()
    waiting_for_thumbnail = State()


# ==============================================================================
# Permission Helpers
# ==============================================================================

def is_admin_user(user: Optional[User]) -> bool:
    """Verify if the Telegram user is authorized as an administrator."""
    if not user:
        return False
    return settings.is_admin(user.id)


# ==============================================================================
# Admin Channel Library View
# ==============================================================================

async def send_channel_list(target) -> None:
    """Send or edit message to show list of raw channels with unprocessed media files."""
    items: List[MediaItem] = []
    async for db in get_db_session():
        stmt = select(MediaItem).where(
            MediaItem.status.in_([PipelineStatus.SCRAPED, PipelineStatus.ENRICHED]),
            MediaItem.shadow_message_id.is_(None),
        ).order_by(MediaItem.created_at.desc())
        res = await db.execute(stmt)
        items = list(res.scalars().all())
        break

    if not items:
        text = (
            "📂 **Admin Library: No Pending Files**\n\n"
            "Drop or forward new media files into your configured Raw Channel.\n"
            "The Userbot will automatically scrape and display them here."
        )
        buttons = [
            [InlineKeyboardButton(text="🖼️ Manage Thumbnail", callback_data="thumb_menu")],
            [InlineKeyboardButton(text="🔄 Refresh", callback_data="c_back")],
        ]
        markup = InlineKeyboardMarkup(inline_keyboard=buttons)
        if hasattr(target, "message") and target.message:
            await target.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        else:
            await target.reply(text, reply_markup=markup, parse_mode="Markdown")
        return

    groups: dict[int, list[MediaItem]] = {}
    for item in items:
        groups.setdefault(item.raw_channel_id, []).append(item)

    text = f"📦 **Admin Media Library (`{len(items)}` total pending files)**\n\nSelect a raw channel to review or batch-process files:\n\n"
    buttons = []
    for chan_id, grp_items in groups.items():
        text += f"▪️ **Channel:** `{chan_id}` — `{len(grp_items)}` file(s)\n"
        buttons.append([InlineKeyboardButton(
            text=f"📁 Channel: {chan_id} ({len(grp_items)} files)",
            callback_data=f"c_view:{chan_id}",
        )])

    buttons.append([InlineKeyboardButton(text="🖼️ Manage Thumbnail", callback_data="thumb_menu")])

    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    if hasattr(target, "message") and target.message:
        await target.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await target.reply(text, reply_markup=markup, parse_mode="Markdown")


async def _render_channel_view(callback: CallbackQuery, state: FSMContext, chan_id: str) -> None:
    """Render items inside a selected channel with selection checkmarks and action buttons."""
    items: List[MediaItem] = []
    async for db in get_db_session():
        stmt = select(MediaItem).where(
            MediaItem.raw_channel_id == int(chan_id),
            MediaItem.status.in_([PipelineStatus.SCRAPED, PipelineStatus.ENRICHED]),
            MediaItem.shadow_message_id.is_(None),
        ).order_by(MediaItem.created_at.desc())
        res = await db.execute(stmt)
        items = list(res.scalars().all())
        break

    data = await state.get_data()
    selected_items: list[int] = data.get("selected_items", [])

    text = (
        f"📁 **Channel `{chan_id}` Pending Files**\n\n"
        f"Selected: **{len(selected_items)}** / {len(items)}\n"
        f"Tap an item to toggle selection, or use actions below:\n\n"
    )

    buttons = []
    for item in items:
        is_sel = item.id in selected_items
        mark = "✅" if is_sel else "⬜"
        s_e = f"S{item.season_num or 1:02d}E{item.episode_num or 1:02d}"
        q = f"[{item.quality_tag}]" if item.quality_tag else ""
        button_text = f"{mark} {item.parsed_title} {s_e} {q}".strip()
        buttons.append([InlineKeyboardButton(
            text=button_text[:60],
            callback_data=f"toggle:{item.id}:{chan_id}",
        )])

    # Action row
    action_row = []
    if selected_items:
        action_row.append(InlineKeyboardButton(text=f"👁️ Preview ({len(selected_items)})", callback_data=f"preview:{chan_id}"))
        action_row.append(InlineKeyboardButton(text="✏️ Rename", callback_data=f"rename_sel:{chan_id}"))
    buttons.append(action_row)

    # Navigation row
    buttons.append([
        InlineKeyboardButton(text="🔄 Select All", callback_data=f"sel_all:{chan_id}"),
        InlineKeyboardButton(text="🔙 Back to Channels", callback_data="c_back"),
    ])

    markup = InlineKeyboardMarkup(inline_keyboard=[row for row in buttons if row])
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e

    await callback.answer()


# ==============================================================================
# /start Command (Deep Link Delivery for Users, Admin Panel for Admins)
# ==============================================================================

@router.message(Command("start"))
async def on_start_command(message: Message) -> None:
    """Entry point for /start.
    - Deep-link (/start <token>): Delivers batch files to ANY user (Admins & Regular Subscribers).
    - Bare /start: Shows admin dashboard for admins, or public welcome for regular users.
    """
    text = (message.text or "").strip()
    parts = text.split(" ")

    # Case 1: Deep-Link Parameter Provided (/start b_... or /start s_... or /start f_...)
    if len(parts) >= 2:
        param = parts[1].strip()
        logger.info(f"User {message.chat.id} requested batch delivery with param '{param}'")

        status_msg = await message.reply("⏳ **Fetching your media files...**", parse_mode="Markdown")

        delivered, err = await NativeBatchEngine.handle_start_parameter(message.bot, message.chat.id, param)

        try:
            await status_msg.delete()
        except Exception:
            pass

        if delivered == 0:
            if err:
                await message.reply(f"❌ **Delivery Notice:**\n{err}", parse_mode="Markdown")
            else:
                await message.reply(
                    "❌ **Media Not Found**: This download link may have expired or been removed.",
                    parse_mode="Markdown",
                )
        return

    # Case 2: Bare /start without parameters
    if is_admin_user(message.from_user):
        await send_channel_list(message)
    else:
        # Regular subscriber public greeting
        welcome_text = (
            "👋 **Welcome to Media Hub Bot!**\n\n"
            "This bot automatically delivers high-definition movies and episodes directly to your chat.\n\n"
            "📥 **How to get media files:**\n"
            "1. Visit our official channel.\n"
            "2. Find any movie or series you like and tap the **STREAM / DOWNLOAD** link.\n"
            "3. The files will be sent to you right here instantly!"
        )
        buttons = []
        if settings.MAIN_CHANNEL_ID:
            chan_str = str(settings.MAIN_CHANNEL_ID).replace("-100", "")
            buttons.append([InlineKeyboardButton(text="📢 Visit Main Channel", url=f"https://t.me/c/{chan_str}/1")])
        markup = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
        await message.reply(welcome_text, reply_markup=markup, parse_mode="Markdown")


# ==============================================================================
# Admin Commands (Protected by is_admin_user)
# ==============================================================================

@router.message(Command("files", "list", "manage"))
async def on_files_command(message: Message) -> None:
    """Open channel library file manager (Admin only)."""
    if not is_admin_user(message.from_user):
        await message.reply("⛔ **Access Denied**: Only administrators can access media management.", parse_mode="Markdown")
        return
    await send_channel_list(message)


# ==============================================================================
# Admin Callback Queries (Protected by is_admin_user)
# ==============================================================================

@router.callback_query(F.data.startswith("c_view:"))
async def on_channel_view(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_user(callback.from_user):
        await callback.answer("⛔ Access Denied: Admin only.", show_alert=True)
        return
    chan_id = callback.data.split(":")[1]
    await _render_channel_view(callback, state, chan_id)


@router.callback_query(F.data == "c_back")
async def on_c_back(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_user(callback.from_user):
        await callback.answer("⛔ Access Denied: Admin only.", show_alert=True)
        return
    await state.update_data(selected_items=[])
    if callback.message and callback.message.photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await send_channel_list(callback.message)
    else:
        await send_channel_list(callback)
    await callback.answer()


@router.callback_query(F.data.startswith("toggle:"))
async def on_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_user(callback.from_user):
        await callback.answer("⛔ Access Denied: Admin only.", show_alert=True)
        return
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


@router.callback_query(F.data.startswith("sel_all:"))
async def on_select_all(callback: CallbackQuery, state: FSMContext) -> None:
    """Select all pending files in the current channel."""
    if not is_admin_user(callback.from_user):
        await callback.answer("⛔ Access Denied: Admin only.", show_alert=True)
        return
    chan_id = callback.data.split(":")[1]

    items: List[MediaItem] = []
    async for db in get_db_session():
        stmt = select(MediaItem).where(
            MediaItem.raw_channel_id == int(chan_id),
            MediaItem.status.in_([PipelineStatus.SCRAPED, PipelineStatus.ENRICHED]),
            MediaItem.shadow_message_id.is_(None),
        )
        res = await db.execute(stmt)
        items = list(res.scalars().all())
        break

    data = await state.get_data()
    current_selected = set(data.get("selected_items", []))
    all_ids = {i.id for i in items}

    # If already all selected, deselect all; otherwise select all
    if current_selected == all_ids:
        await state.update_data(selected_items=[])
    else:
        await state.update_data(selected_items=list(all_ids))

    await _render_channel_view(callback, state, chan_id)


# ==============================================================================
# Admin Interactive Renaming Flow
# ==============================================================================

@router.callback_query(F.data.startswith("rename_sel:"))
async def on_rename_selected_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt admin to enter a new clean title for selected item(s)."""
    if not is_admin_user(callback.from_user):
        await callback.answer("⛔ Access Denied: Admin only.", show_alert=True)
        return

    chan_id = callback.data.split(":")[1]
    data = await state.get_data()
    selected_items: list[int] = data.get("selected_items", [])

    if not selected_items:
        await callback.answer("⚠️ No files selected to rename!", show_alert=True)
        return

    items: List[MediaItem] = []
    async for db in get_db_session():
        stmt = select(MediaItem).where(MediaItem.id.in_(selected_items))
        res = await db.execute(stmt)
        items = list(res.scalars().all())
        break

    current_title = items[0].parsed_title if items else "Unknown"

    await state.update_data(rename_chan_id=chan_id, rename_item_ids=selected_items)
    await state.set_state(EditMediaState.waiting_for_batch_rename)

    prompt = (
        f"✏️ **Rename Media Title ({len(selected_items)} item(s) selected)**\n\n"
        f"Current Title: `{current_title}`\n\n"
        f"Please send the new Title as a message (or type `/cancel` to abort):"
    )
    await callback.message.reply(prompt, parse_mode="Markdown")
    await callback.answer()


@router.message(EditMediaState.waiting_for_batch_rename)
async def on_batch_rename_received(message: Message, state: FSMContext) -> None:
    """Apply new title to all selected items in database."""
    if not is_admin_user(message.from_user):
        await message.reply("⛔ Access Denied.")
        return

    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        await message.reply("❌ Renaming cancelled.")
        return

    data = await state.get_data()
    item_ids: list[int] = data.get("rename_item_ids", [])

    if not item_ids:
        await state.clear()
        await message.reply("⚠️ Session expired. Please try again.")
        return

    async for db in get_db_session():
        stmt = select(MediaItem).where(MediaItem.id.in_(item_ids))
        res = await db.execute(stmt)
        items = list(res.scalars().all())
        for item in items:
            item.parsed_title = text
            item.clean_file_name = RegexEngine.format_custom_name(item, settings.RENAME_FORMAT)
        await db.commit()
        break

    await state.clear()
    await message.reply(
        f"✅ **Title updated for {len(item_ids)} file(s)!**\n\n"
        f"New Title: `{text}`\n"
        f"Use `/files` to review or process the batch.",
        parse_mode="Markdown",
    )


# ==============================================================================
# Single-Item Review Card Actions (Approve / Edit / Reject)
# ==============================================================================

@router.callback_query(F.data.startswith("approve:"))
async def on_card_approve(callback: CallbackQuery) -> None:
    """Admin approves a single media item for immediate download & upload."""
    if not is_admin_user(callback.from_user):
        await callback.answer("⛔ Access Denied: Admin only.", show_alert=True)
        return

    item_id = callback.data.split(":")[1]
    async for db in get_db_session():
        stmt = select(MediaItem).where(MediaItem.id == int(item_id))
        res = await db.execute(stmt)
        item = res.scalar_one_or_none()
        if item:
            item.status = PipelineStatus.CONFIRMED
            await db.commit()
        break

    # Enqueue ARQ task
    from arq import create_pool
    from arq.connections import RedisSettings
    redis_pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    await redis_pool.enqueue_job("process_media_io_task", item_id)
    await redis_pool.aclose()

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply(f"🚀 **Approved!** Item ID `{item_id}` queued for download & upload.", parse_mode="Markdown")
    await callback.answer("✅ Item approved!")


@router.callback_query(F.data.startswith("edit:"))
async def on_card_edit_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    """Admin clicks edit on a single confirmation card."""
    if not is_admin_user(callback.from_user):
        await callback.answer("⛔ Access Denied: Admin only.", show_alert=True)
        return

    item_id = callback.data.split(":")[1]
    await state.update_data(edit_card_item_id=int(item_id))
    await state.set_state(EditMediaState.waiting_for_card_title)
    await callback.message.reply(
        f"✏️ **Edit Item ID `{item_id}`**\n\nSend the new title as a message (or `/cancel` to abort):",
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(EditMediaState.waiting_for_card_title)
async def on_card_title_received(message: Message, state: FSMContext) -> None:
    """Save updated title from single card edit."""
    if not is_admin_user(message.from_user):
        return

    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        await message.reply("Cancelled.")
        return

    data = await state.get_data()
    item_id = data.get("edit_card_item_id")
    if not item_id:
        await state.clear()
        await message.reply("Session expired.")
        return

    async for db in get_db_session():
        stmt = select(MediaItem).where(MediaItem.id == item_id)
        res = await db.execute(stmt)
        item = res.scalar_one_or_none()
        if item:
            item.parsed_title = text
            item.clean_file_name = RegexEngine.format_custom_name(item, settings.RENAME_FORMAT)
            await db.commit()
        break

    await state.clear()
    await message.reply(
        f"✅ **Title updated for Item ID `{item_id}`!**\nNew Title: `{text}`\n\nUse `/files` to review or approve.",
        parse_mode="Markdown",
    )


@router.callback_query(F.data.startswith("reject:"))
async def on_card_reject(callback: CallbackQuery) -> None:
    """Admin rejects an item from the pipeline."""
    if not is_admin_user(callback.from_user):
        await callback.answer("⛔ Access Denied: Admin only.", show_alert=True)
        return

    item_id = callback.data.split(":")[1]
    async for db in get_db_session():
        stmt = select(MediaItem).where(MediaItem.id == int(item_id))
        res = await db.execute(stmt)
        item = res.scalar_one_or_none()
        if item:
            item.status = PipelineStatus.REJECTED
            await db.commit()
        break

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply(f"❌ **Rejected!** Item ID `{item_id}` cancelled.", parse_mode="Markdown")
    await callback.answer("Item rejected.")


# ==============================================================================
# Batch Processing & Preview Handlers
# ==============================================================================

@router.callback_query(F.data.startswith("preview:"))
async def on_preview(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_user(callback.from_user):
        await callback.answer("⛔ Access Denied: Admin only.", show_alert=True)
        return

    chan_id = callback.data.split(":")[1]
    data = await state.get_data()
    selected_items: list[int] = data.get("selected_items", [])

    if not selected_items:
        await callback.answer("⚠️ No files selected!", show_alert=True)
        return

    items: List[MediaItem] = []
    async for db in get_db_session():
        stmt = select(MediaItem).where(MediaItem.id.in_(selected_items)).order_by(MediaItem.episode_num)
        res = await db.execute(stmt)
        items = list(res.scalars().all())
        break

    if not items:
        await callback.answer("Items not found.", show_alert=True)
        return

    first_item = items[0]
    pres_text = RegexEngine.format_presentation(first_item)

    preview_text = f"👁️ **BATCH PREVIEW ({len(items)} file(s))**\n\n**Presentation Template:**\n```\n{pres_text}\n```\n\n**Renamed Output Files:**\n"
    for item in items:
        preview_text += f"- `{RegexEngine.format_custom_name(item, settings.RENAME_FORMAT)}`\n"

    buttons = [
        [InlineKeyboardButton(text="🚀 Confirm & Process Batch", callback_data=f"process_batch:{chan_id}")],
        [InlineKeyboardButton(text="🔙 Back to Selection", callback_data=f"c_view:{chan_id}")],
    ]
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(preview_text, reply_markup=markup, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("process_batch:"))
async def on_process_batch(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_user(callback.from_user):
        await callback.answer("⛔ Access Denied: Admin only.", show_alert=True)
        return

    data = await state.get_data()
    selected_items: list[int] = data.get("selected_items", [])

    if not selected_items:
        await callback.answer("No items selected.", show_alert=True)
        return

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
    await callback.message.edit_text(
        f"🚀 **Batch Queued for Processing!**\n\n`{len(selected_items)}` file(s) sent.",
        parse_mode="Markdown",
    )
    await callback.answer()


# ==============================================================================
# Admin Dynamic Format Configurations (/setformat, /setcaption, /setpresentation)
# ==============================================================================

@router.message(Command("setformat"))
async def on_setformat_command(message: Message, state: FSMContext) -> None:
    if not is_admin_user(message.from_user):
        await message.reply("⛔ Access Denied: Admin only.")
        return
    await state.set_state(ConfigState.waiting_for_rename_format)
    await message.reply(
        "🛠️ **Set Rename Format**\n\n"
        "Available tags: `{title}`, `{season}`, `{episode}`, `{quality}`, `{ext}`\n"
        f"Current: `{settings.RENAME_FORMAT}`\n\n"
        "Send the new format, or `/cancel` to abort.",
        parse_mode="Markdown",
    )


@router.message(ConfigState.waiting_for_rename_format)
async def on_rename_format_received(message: Message, state: FSMContext) -> None:
    if not is_admin_user(message.from_user):
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
    if not is_admin_user(message.from_user):
        await message.reply("⛔ Access Denied: Admin only.")
        return
    await state.set_state(ConfigState.waiting_for_caption_format)
    await message.reply(
        "📝 **Set Caption Format**\n\n"
        "Available tags: `{title}`, `{season}`, `{episode}`, `{quality}`, `{filename}`\n"
        f"Current:\n`{settings.CAPTION_FORMAT}`\n\n"
        "Send the new format, or `/cancel` to abort.",
        parse_mode="Markdown",
    )


@router.message(ConfigState.waiting_for_caption_format)
async def on_caption_format_received(message: Message, state: FSMContext) -> None:
    if not is_admin_user(message.from_user):
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
    if not is_admin_user(message.from_user):
        await message.reply("⛔ Access Denied: Admin only.")
        return
    await state.set_state(ConfigState.waiting_for_presentation_format)
    await message.reply(
        "📰 **Set Presentation Format**\n\n"
        "Available tags: `{title}`, `{year}`, `{quality}`, `{audio}`\n"
        f"Current:\n`{settings.PRESENTATION_FORMAT}`\n\n"
        "Send the new format, or `/cancel` to abort.",
        parse_mode="Markdown",
    )


@router.message(ConfigState.waiting_for_presentation_format)
async def on_presentation_format_received(message: Message, state: FSMContext) -> None:
    if not is_admin_user(message.from_user):
        return
    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.reply("Cancelled.")
        return
    settings.PRESENTATION_FORMAT = message.text.strip()
    settings.save_dynamic_config()
    await state.clear()
    await message.reply(f"✅ **Presentation format saved!**\n\nNew format:\n`{settings.PRESENTATION_FORMAT}`", parse_mode="Markdown")


# ==============================================================================
# Admin Persistent Global Thumbnail Menu (/thumbnail)
# ==============================================================================

async def send_thumbnail_menu(target) -> None:
    """Render the persistent global thumbnail management menu with live preview and actions."""
    thumb_path = settings.get_thumbnail_path()
    has_thumb = thumb_path is not None and thumb_path.exists()

    if has_thumb:
        size_kb = round(thumb_path.stat().st_size / 1024, 1)
        dim_str = ""
        try:
            from PIL import Image
            with Image.open(thumb_path) as im:
                dim_str = f" ({im.width}×{im.height})"
        except Exception:
            pass

        text = (
            "🖼️ **Global Thumbnail Management**\n\n"
            "📌 **Status:** Active & Saved Permanently ✅\n"
            f"📁 **File:** `{thumb_path.name}`{dim_str}, `{size_kb} KB`\n\n"
            "This thumbnail is **permanently active** and will automatically be attached "
            "to all renamed files uploaded to the channel until you change or delete it.\n\n"
            "👇 Tap below to upload a new one or remove it."
        )
        buttons = [
            [InlineKeyboardButton(text="📸 Upload New Thumbnail", callback_data="thumb_upload")],
            [InlineKeyboardButton(text="🗑️ Delete Thumbnail", callback_data="thumb_delete")],
            [InlineKeyboardButton(text="🔙 Back to Channels", callback_data="c_back")],
        ]
        markup = InlineKeyboardMarkup(inline_keyboard=buttons)

        if isinstance(target, CallbackQuery):
            if target.message and target.message.photo:
                try:
                    await target.message.edit_caption(caption=text, reply_markup=markup, parse_mode="Markdown")
                    return
                except Exception:
                    pass
            if target.message:
                try:
                    await target.message.delete()
                except Exception:
                    pass
                await target.message.answer_photo(
                    photo=FSInputFile(str(thumb_path)),
                    caption=text,
                    reply_markup=markup,
                    parse_mode="Markdown",
                )
        else:
            await target.reply_photo(
                photo=FSInputFile(str(thumb_path)),
                caption=text,
                reply_markup=markup,
                parse_mode="Markdown",
            )
    else:
        text = (
            "🖼️ **Global Thumbnail Management**\n\n"
            "📌 **Status:** No Custom Thumbnail Set ❌\n\n"
            "Files are currently uploaded without a custom thumbnail.\n"
            "Upload an image here to set a **permanent global thumbnail** that will "
            "always be attached to all renamed files uploaded to the channel until changed or removed.\n\n"
            "👇 Tap below to upload a thumbnail."
        )
        buttons = [
            [InlineKeyboardButton(text="📸 Upload Thumbnail", callback_data="thumb_upload")],
            [InlineKeyboardButton(text="🔙 Back to Channels", callback_data="c_back")],
        ]
        markup = InlineKeyboardMarkup(inline_keyboard=buttons)

        if isinstance(target, CallbackQuery):
            if target.message and target.message.photo:
                try:
                    await target.message.delete()
                except Exception:
                    pass
                if target.message:
                    await target.message.answer(text, reply_markup=markup, parse_mode="Markdown")
            else:
                if target.message:
                    await target.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        else:
            await target.reply(text, reply_markup=markup, parse_mode="Markdown")


@router.message(Command("thumbnail", "thumb", "setthumb"))
async def on_thumbnail_command(message: Message, state: FSMContext) -> None:
    """Open thumbnail menu via command (Admin only)."""
    if not is_admin_user(message.from_user):
        await message.reply("⛔ Access Denied: Admin only.")
        return
    await send_thumbnail_menu(message)


@router.callback_query(F.data == "thumb_menu")
async def on_thumb_menu_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """Open thumbnail menu via inline button (Admin only)."""
    if not is_admin_user(callback.from_user):
        await callback.answer("⛔ Access Denied: Admin only.", show_alert=True)
        return
    await send_thumbnail_menu(callback)
    await callback.answer()


@router.callback_query(F.data == "thumb_upload")
async def on_thumb_upload_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt admin to upload an image file or photo."""
    if not is_admin_user(callback.from_user):
        await callback.answer("⛔ Access Denied: Admin only.", show_alert=True)
        return
    await state.set_state(ConfigState.waiting_for_thumbnail)
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Cancel", callback_data="thumb_cancel")]
    ])
    await callback.message.reply(
        "📸 **Please send the new thumbnail image now.**\n\n"
        "You can send it as a photo or an uncompressed image file (JPEG/PNG/WEBP).\n"
        "Once uploaded, it will **persist permanently** across all processes until you change or remove it.\n\n"
        "Type /cancel to abort.",
        reply_markup=cancel_kb,
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "thumb_cancel")
async def on_thumb_cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel thumbnail upload prompt."""
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("Cancelled.")


@router.callback_query(F.data == "thumb_delete")
async def on_thumb_delete_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """Delete permanent global thumbnail and update config.json."""
    if not is_admin_user(callback.from_user):
        await callback.answer("⛔ Access Denied: Admin only.", show_alert=True)
        return
    thumb_path = settings.get_thumbnail_path()
    if thumb_path and thumb_path.exists():
        try:
            thumb_path.unlink()
        except Exception as e:
            logger.warning(f"Could not delete thumbnail file {thumb_path}: {e}")
    settings.GLOBAL_THUMBNAIL_PATH = ""
    settings.save_dynamic_config()

    await callback.answer("Thumbnail removed successfully.", show_alert=True)
    await send_thumbnail_menu(callback)


@router.message(ConfigState.waiting_for_thumbnail, F.photo | F.document)
async def on_thumbnail_received(message: Message, state: FSMContext) -> None:
    """Receive and permanently store the new global thumbnail."""
    if not is_admin_user(message.from_user):
        return

    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document:
        mime = (message.document.mime_type or "").lower()
        name = (message.document.file_name or "").lower()
        if mime.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp")):
            file_id = message.document.file_id
        else:
            await message.reply(
                "⚠️ **Invalid File**: Please send an image file (JPG, PNG, WEBP) or send it directly as a photo.\n"
                "Type /cancel to abort.",
                parse_mode="Markdown",
            )
            return

    if not file_id:
        await message.reply("⚠️ Please send a valid image photo. Type /cancel to abort.", parse_mode="Markdown")
        return

    status_msg = await message.reply("⏳ **Saving permanent global thumbnail...**", parse_mode="Markdown")

    assets_dir = settings.BASE_DIR / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    dest_file = assets_dir / "global_thumb.jpg"

    try:
        await message.bot.download(file=file_id, destination=dest_file)
        # Normalize to standard RGB JPEG (max 320x320) for Telegram MTProto thumbnail compliance
        try:
            from PIL import Image
            with Image.open(dest_file) as im:
                rgb_im = im.convert("RGB")
                rgb_im.thumbnail((320, 320), Image.Resampling.LANCZOS)
                rgb_im.save(dest_file, "JPEG", quality=95, optimize=True)
        except Exception as pil_err:
            logger.debug(f"PIL normalization skipped/failed for uploaded thumbnail: {pil_err}")

        settings.GLOBAL_THUMBNAIL_PATH = str(dest_file)
        settings.save_dynamic_config()
        await state.clear()
        try:
            await status_msg.delete()
        except Exception:
            pass

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🖼️ Thumbnail Settings", callback_data="thumb_menu")],
            [InlineKeyboardButton(text="📁 View Channels", callback_data="c_back")],
        ])
        await message.reply_photo(
            photo=FSInputFile(str(dest_file)),
            caption=(
                "✅ **Global Thumbnail Saved Permanently!**\n\n"
                f"📁 Saved to: `assets/global_thumb.jpg`\n\n"
                "This thumbnail is now permanently stored and will automatically be attached "
                "to all renamed files until you change or remove it."
            ),
            reply_markup=kb,
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Failed to download and save thumbnail: {e}", exc_info=True)
        await message.reply(f"❌ **Failed to save thumbnail:** {e}")


@router.message(ConfigState.waiting_for_thumbnail)
async def on_thumbnail_invalid_input(message: Message) -> None:
    """Handle text input when bot is expecting an image."""
    if message.text and message.text.strip().startswith("/"):
        return
    await message.reply(
        "⚠️ **Please send an image or photo** for the thumbnail.\n\n"
        "Type /cancel to abort.",
        parse_mode="Markdown"
    )


# ==============================================================================
# Help & Cancel Commands
# ==============================================================================

@router.message(Command("help"))
async def on_help_command(message: Message) -> None:
    """Show functioning commands and workflow guide based on user role."""
    if is_admin_user(message.from_user):
        help_text = (
            "🛠️ **Admin Command Reference**\n\n"
            "• `/start` or `/files` — Open media library, review pending files, and queue batches.\n"
            "• `/thumbnail` — Manage permanent global thumbnail (view, upload new, or delete).\n"
            "• `/setformat` — Set template for clean video renaming (`{title}`, `{season}`, `{episode}`, `{quality}`, `{ext}`).\n"
            "• `/setcaption` — Set caption format for uploaded videos in the shadow channel.\n"
            "• `/setpresentation` — Set presentation post format published to the main channel.\n"
            "• `/cancel` — Cancel any active text prompt, thumbnail upload, or rename operation.\n\n"
            "📥 **Pipeline Workflow:**\n"
            "1. Drop media into your configured Raw Channel.\n"
            "2. Scraper detects media and enriches metadata.\n"
            "3. Use `/files` to review, rename titles, and click **Confirm & Process**.\n"
            "4. Files are processed and uploaded to the Shadow Channel with batch links posted to the Main Channel."
        )
    else:
        help_text = (
            "📖 **How to Use This Bot**\n\n"
            "1. Visit our official channel.\n"
            "2. Find any movie or episode you want to watch.\n"
            "3. Tap the **STREAM / DOWNLOAD** link on the post.\n"
            "4. The bot will automatically send the video files directly to this chat!\n\n"
            "💡 *No special commands needed — simply tap the download links in the channel.*"
        )
    await message.reply(help_text, parse_mode="Markdown")


@router.message(Command("cancel"))
async def on_cancel_command(message: Message, state: FSMContext) -> None:
    """Cancel any active FSM state or prompt."""
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        await message.reply("❌ Current operation cancelled.", parse_mode="Markdown")
    else:
        await message.reply("ℹ️ No active operation to cancel.", parse_mode="Markdown")

