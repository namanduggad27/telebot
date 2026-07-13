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


async def send_grouped_media_library(target: Message | CallbackQuery) -> None:
    """Query database for pending items, group them by show/title, and send interactive management menu."""
    from sqlalchemy import select
    from src.db.session import get_db_session
    from src.db.models import MediaItem
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
        text = (
            "📂 **No Pending Media Files Found**\n\n"
            "Drop video/document files into your raw channel first. They will be detected, stored safely in the database, and grouped right here whenever you start the bot or send `/files`."
        )
        if isinstance(target, CallbackQuery):
            if target.message:
                await target.message.edit_text(text, parse_mode="Markdown")
        else:
            await target.reply(text, parse_mode="Markdown")
        return

    # Group items by parsed_title
    groups = {}
    for item in items:
        title = item.parsed_title or "Unknown Title"
        groups.setdefault(title, []).append(item)

    text = (
        f"📦 **Grouped Media Library (`{len(items)}` total files waiting)**\n"
        "Select a show or movie below to view episodes, rename filenames, attach custom thumbnails, or upload to shadow:\n\n"
    )
    buttons = []
    for title, grp_items in groups.items():
        text += f"▪️ **{title}** — `{len(grp_items)}` file(s)\n"
        buttons.append([InlineKeyboardButton(
            text=f"📺 {title[:25]} ({len(grp_items)})",
            callback_data=f"g_view:{title[:40]}"
        )])

    buttons.append([InlineKeyboardButton(
        text=f"🚀 Upload ALL Pending Files ({len(items)})",
        callback_data="g_upload_all"
    )])

    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await target.reply(text, reply_markup=markup, parse_mode="Markdown")


@router.message(Command("start"))
async def on_start_command(message: Message) -> None:
    """Handle /start command: intercept start parameters (`file_xxx` / `season_xxx`) or show grouped media library."""
    text = (message.text or "").strip()
    parts = text.split(" ")

    # Case 1: Deep Link Start Parameter (`/start f_1234abcd` or `/start s_95557_1`)
    if len(parts) >= 2:
        param = parts[1].strip()
        logger.info(f"Received /start deep link parameter '{param}' from User={message.from_user.id if message.from_user else 'Unknown'}")
        delivered = await NativeBatchEngine.handle_start_parameter(message.bot, message.chat.id, param)
        if delivered > 0:
            await message.reply(
                f"✨ **Delivered {delivered} file(s)!**\n"
                f"Enjoy your media! Powered by TelegramMediaPipeline.",
                parse_mode="Markdown",
            )
        else:
            await message.reply(
                "❌ **Media Not Found or Expired**\n"
                "The requested file or season batch could not be found in our database.",
                parse_mode="Markdown",
            )
        return

    # Case 2: Standard `/start` with no parameters
    if settings.ADMIN_USER_ID and message.from_user and message.from_user.id == settings.ADMIN_USER_ID:
        await send_grouped_media_library(message)
    else:
        await message.reply(
            "👋 **Welcome to Telegram Media Bot!**\n\n"
            "Click a **STREAM / DOWNLOAD** link from our presentation channel to receive your high-definition movies and episodes directly here!",
            parse_mode="Markdown",
        )


@router.message(Command("files", "list", "manage"))
async def on_files_command(message: Message) -> None:
    """Handle /files command for Admin to view and manage grouped pending media library."""
    if settings.ADMIN_USER_ID and message.from_user and message.from_user.id != settings.ADMIN_USER_ID:
        return
    await send_grouped_media_library(message)


@router.callback_query(F.data.startswith("approve:"))
async def on_approve_clicked(callback: CallbackQuery) -> None:
    """Handle Admin clicking 'Approve & Download' on a review card."""
    if settings.ADMIN_USER_ID and callback.from_user.id != settings.ADMIN_USER_ID:
        await callback.answer("Unauthorized.", show_alert=True)
        return

    item_id = callback.data.split(":")[1]
    logger.info(f"Admin approved ID={item_id}")

    success = await StateMachine.transition_item(item_id, PipelineStatus.CONFIRMED)
    if not success:
        await callback.answer("Failed to transition state. Check logs.", show_alert=True)
        return

    # Push the confirmed item ID to ARQ for high-speed Phase 3 I/O processing FIRST
    try:
        from arq import create_pool
        from arq.connections import RedisSettings
        redis_pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        await redis_pool.enqueue_job("process_media_io_task", item_id)
        await redis_pool.aclose()
        logger.info(f"Enqueued process_media_io_task for ID={item_id} on ARQ Redis queue.")
    except Exception as q_err:
        logger.warning(f"Could not enqueue process_media_io_task for ID={item_id}: {q_err}")

    # Update message text/markup to reflect approval
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.reply(f"✅ **ID `{item_id}` Approved.** Queued for high-speed download & upload to Shadow DB.", parse_mode="Markdown")
        except Exception as e:
            logger.debug(f"Could not edit reply markup: {e}")

    try:
        await callback.answer("✅ Approved! Queued for Phase 3 I/O processing.")
    except Exception as e:
        logger.debug(f"Could not answer callback query (likely too old): {e}")


@router.callback_query(F.data.startswith("reject:"))
async def on_reject_clicked(callback: CallbackQuery) -> None:
    """Handle Admin clicking 'Reject' on a review card."""
    if settings.ADMIN_USER_ID and callback.from_user.id != settings.ADMIN_USER_ID:
        await callback.answer("Unauthorized.", show_alert=True)
        return

    item_id = callback.data.split(":")[1]
    logger.info(f"Admin rejected ID={item_id}")

    success = await StateMachine.transition_item(item_id, PipelineStatus.REJECTED)
    if not success:
        await callback.answer("Failed to transition state to REJECTED.", show_alert=True)
        return

    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.reply(f"❌ **ID `{item_id}` Rejected.** Pipeline processing halted for this item.", parse_mode="Markdown")
        except Exception as e:
            logger.debug(f"Could not edit reply markup: {e}")

    await callback.answer("❌ Item rejected.")


@router.callback_query(F.data.startswith("edit:"))
async def on_edit_clicked(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle Admin clicking 'Edit Title/Season' on a review card to start interactive edit prompt."""
    if settings.ADMIN_USER_ID and callback.from_user.id != settings.ADMIN_USER_ID:
        await callback.answer("Unauthorized.", show_alert=True)
        return

    item_id = callback.data.split(":")[1]
    await state.set_state(EditTitleState.waiting_for_new_title)
    await state.update_data(item_id=item_id)

    if callback.message:
        await callback.message.reply(
            f"✏️ **Editing ID:** `{item_id}`\n\n"
            "Please send the corrected clean title and optional season/episode in format:\n"
            "`Correct Show Name | S01E02`\n"
            "Or send `/cancel` to abort editing.",
            parse_mode="Markdown",
        )
    await callback.answer()


@router.message(EditTitleState.waiting_for_new_title)
async def on_new_title_received(message: Message, state: FSMContext) -> None:
    """Process new title/season/episode submitted by Admin during interactive edit."""
    if settings.ADMIN_USER_ID and message.from_user and message.from_user.id != settings.ADMIN_USER_ID:
        return

    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.reply("Edit cancelled.")
        return

    data = await state.get_data()
    item_id = data.get("item_id")
    if not item_id or not message.text:
        await state.clear()
        return

    raw_text = message.text.strip()
    new_title = raw_text
    season_num = None
    episode_num = None

    if "|" in raw_text:
        parts = [p.strip() for p in raw_text.split("|")]
        new_title = parts[0]
        # Parse S01E02 from second part if provided
        se_part = parts[1].upper()
        if "S" in se_part and "E" in se_part:
            try:
                s_str, e_str = se_part.split("S")[1].split("E")
                season_num = int(s_str)
                episode_num = int(e_str)
            except Exception:
                pass

    extra_meta = {"parsed_title": new_title}
    if season_num is not None:
        extra_meta["season_num"] = season_num
    if episode_num is not None:
        extra_meta["episode_num"] = episode_num

    # Update clean file name based on edits
    cached_state = await StateMachine.get_cached_state(item_id)
    quality = cached_state.get("quality_tag", "") if cached_state else ""
    s_tag = f" - S{season_num:02d}E{episode_num:02d}" if season_num and episode_num else ""
    q_tag = f" - [{quality}]" if quality else ""
    extra_meta["clean_file_name"] = f"{new_title}{s_tag}{q_tag}.mkv"

    await StateMachine.transition_item(item_id, PipelineStatus.ENRICHED, extra_metadata=extra_meta)
    await state.clear()

    await message.reply(
        f"✅ **Metadata Updated for ID `{item_id}`:**\n"
        f"📌 Title: `{new_title}`\n"
        f"📺 Season/Ep: `{season_num} / {episode_num}`\n"
        f"📂 Clean File: `{extra_meta['clean_file_name']}`\n\n"
        "You can now press **Approve & Download** on the review card.",
        parse_mode="Markdown",
    )


@router.callback_query(F.data.startswith("g_view:"))
async def on_group_view(callback: CallbackQuery) -> None:
    """Show details and options for a selected group/show."""
    if settings.ADMIN_USER_ID and callback.from_user.id != settings.ADMIN_USER_ID:
        await callback.answer("Unauthorized.", show_alert=True)
        return

    group_prefix = callback.data.split("g_view:")[1]
    from sqlalchemy import select
    from src.db.session import get_db_session
    from src.db.models import MediaItem
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    items = []
    async for db in get_db_session():
        stmt = select(MediaItem).where(
            MediaItem.status.in_([PipelineStatus.SCRAPED, PipelineStatus.ENRICHED]),
            MediaItem.shadow_message_id.is_(None)
        )
        res = await db.execute(stmt)
        all_pending = list(res.scalars().all())
        items = [i for i in all_pending if (i.parsed_title or "Unknown Title").startswith(group_prefix)]
        break

    if not items:
        await callback.answer("No pending items found in this group.", show_alert=True)
        await send_grouped_media_library(callback)
        return

    items.sort(key=lambda x: (x.season_num or 0, x.episode_num or 0, x.id))
    show_title = items[0].parsed_title or group_prefix

    text = f"📺 **Group: {show_title}**\nTotal Files Waiting: `{len(items)}`\n\n"
    for item in items:
        s_str = f"S{item.season_num:02d}" if item.season_num is not None else ""
        e_str = f"E{item.episode_num:02d}" if item.episode_num is not None else ""
        se_tag = f"`{s_str}{e_str}` | " if (s_str or e_str) else ""
        thumb_status = "✅ Set" if item.custom_thumbnail_path else "❌ None (TMDB/Default)"
        text += f"▪️ {se_tag}ID: `#{item.id}`\n   Filename: `{item.clean_file_name}`\n   Thumbnail: {thumb_status}\n\n"

    buttons = [
        [InlineKeyboardButton(text="🏷️ Rename Show Title (All Episodes)", callback_data=f"g_rshow:{group_prefix}")],
        [InlineKeyboardButton(text="📄 Rename Specific Episode Filename", callback_data=f"g_rfile:{group_prefix}")],
        [InlineKeyboardButton(text="🖼️ Set Custom Thumbnail (All Episodes)", callback_data=f"g_thumb:{group_prefix}")],
        [InlineKeyboardButton(text=f"🚀 Upload Group to Shadow ({len(items)} files)", callback_data=f"g_process:{group_prefix}")],
        [InlineKeyboardButton(text="🔙 Back to Groups List", callback_data="g_list")]
    ]
    if callback.message:
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "g_list")
async def on_group_list_clicked(callback: CallbackQuery) -> None:
    """Return to main grouped library menu."""
    if settings.ADMIN_USER_ID and callback.from_user.id != settings.ADMIN_USER_ID:
        return
    await send_grouped_media_library(callback)
    await callback.answer()


@router.callback_query(F.data.startswith("g_rshow:"))
async def on_group_rshow(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt admin to rename show title across all pending items in group."""
    if settings.ADMIN_USER_ID and callback.from_user.id != settings.ADMIN_USER_ID:
        return
    group_prefix = callback.data.split("g_rshow:")[1]
    await state.set_state(GroupManageState.waiting_for_show_rename)
    await state.update_data(group_prefix=group_prefix)
    if callback.message:
        await callback.message.reply(
            f"🏷️ **Rename Show Title for `{group_prefix}`**\n\n"
            "Enter the new show title (e.g. `Game of Thrones`). All pending episodes in this group will be updated and their filenames re-generated automatically.\n\n"
            "Send `/cancel` to abort.",
            parse_mode="Markdown"
        )
    await callback.answer()


@router.message(GroupManageState.waiting_for_show_rename)
async def on_show_rename_received(message: Message, state: FSMContext) -> None:
    """Process submitted new show title and update all group items."""
    if settings.ADMIN_USER_ID and message.from_user and message.from_user.id != settings.ADMIN_USER_ID:
        return
    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.reply("Show rename cancelled.")
        return

    data = await state.get_data()
    group_prefix = data.get("group_prefix")
    new_title = (message.text or "").strip()
    if not group_prefix or not new_title:
        await state.clear()
        return

    from sqlalchemy import select
    from src.db.session import get_db_session
    from src.db.models import MediaItem

    count = 0
    async for db in get_db_session():
        stmt = select(MediaItem).where(
            MediaItem.status.in_([PipelineStatus.SCRAPED, PipelineStatus.ENRICHED]),
            MediaItem.shadow_message_id.is_(None)
        )
        res = await db.execute(stmt)
        all_pending = list(res.scalars().all())
        for item in all_pending:
            if (item.parsed_title or "Unknown Title").startswith(group_prefix):
                item.parsed_title = new_title
                s_num = item.season_num
                e_num = item.episode_num
                quality = item.quality_tag or "HD"
                if s_num is not None and e_num is not None:
                    item.clean_file_name = f"{new_title}.S{s_num:02d}E{e_num:02d}.{quality}.mkv"
                else:
                    item.clean_file_name = f"{new_title}.{quality}.mkv"
                count += 1
        await db.commit()
        break

    await state.clear()
    await message.reply(f"✅ **Updated Show Title to `{new_title}` for `{count}` files!**", parse_mode="Markdown")
    await send_grouped_media_library(message)


@router.callback_query(F.data.startswith("g_rfile:"))
async def on_group_rfile(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt admin to rename specific episode filename by ID."""
    if settings.ADMIN_USER_ID and callback.from_user.id != settings.ADMIN_USER_ID:
        return
    group_prefix = callback.data.split("g_rfile:")[1]
    await state.set_state(GroupManageState.waiting_for_file_rename)
    await state.update_data(group_prefix=group_prefix)
    if callback.message:
        await callback.message.reply(
            "📄 **Rename Specific Episode Filename**\n\n"
            "Reply with the exact **ID** and **New Filename** separated by `|`:\n\n"
            "Format: `<ID> | <New Filename>`\n"
            "Example: `10 | House.of.the.Dragon.S01E01.REPACK.1080p.mkv`\n\n"
            "Send `/cancel` to abort.",
            parse_mode="Markdown"
        )
    await callback.answer()


@router.message(GroupManageState.waiting_for_file_rename)
async def on_file_rename_received(message: Message, state: FSMContext) -> None:
    """Process submitted episode ID + new filename."""
    if settings.ADMIN_USER_ID and message.from_user and message.from_user.id != settings.ADMIN_USER_ID:
        return
    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.reply("File rename cancelled.")
        return

    parts = (message.text or "").split("|")
    if len(parts) < 2 or not parts[0].strip().isdigit():
        await message.reply("❌ Invalid format. Please use: `<ID> | <New Filename>`, e.g. `10 | My.Movie.mkv`", parse_mode="Markdown")
        return

    item_id = int(parts[0].strip())
    new_name = parts[1].strip()

    from sqlalchemy import select
    from src.db.session import get_db_session
    from src.db.models import MediaItem

    updated = False
    async for db in get_db_session():
        stmt = select(MediaItem).where(MediaItem.id == item_id)
        res = await db.execute(stmt)
        item = res.scalar_one_or_none()
        if item:
            item.clean_file_name = new_name
            await db.commit()
            updated = True
        break

    await state.clear()
    if updated:
        await message.reply(f"✅ **Updated filename for ID `#{item_id}` to `{new_name}`!**", parse_mode="Markdown")
    else:
        await message.reply(f"❌ Could not find MediaItem with ID `#{item_id}`.", parse_mode="Markdown")
    await send_grouped_media_library(message)


@router.callback_query(F.data.startswith("g_thumb:"))
async def on_group_thumb(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt admin to send custom thumbnail image for all group files."""
    if settings.ADMIN_USER_ID and callback.from_user.id != settings.ADMIN_USER_ID:
        return
    group_prefix = callback.data.split("g_thumb:")[1]
    await state.set_state(GroupManageState.waiting_for_thumbnail)
    await state.update_data(group_prefix=group_prefix)
    if callback.message:
        await callback.message.reply(
            f"🖼️ **Set Custom Thumbnail for `{group_prefix}`**\n\n"
            "Please send/upload the image (`photo`) you want to use as the thumbnail for all episodes in this group.\n\n"
            "Send `/cancel` to abort.",
            parse_mode="Markdown"
        )
    await callback.answer()


@router.message(GroupManageState.waiting_for_thumbnail, F.photo)
async def on_thumbnail_received(message: Message, state: FSMContext) -> None:
    """Process and save submitted photo as custom thumbnail for group files."""
    if settings.ADMIN_USER_ID and message.from_user and message.from_user.id != settings.ADMIN_USER_ID:
        return
    data = await state.get_data()
    group_prefix = data.get("group_prefix")
    if not group_prefix or not message.photo:
        await state.clear()
        return

    settings.ensure_directories()
    thumb_dir = settings.BASE_DIR / "cache" / "custom_thumbs"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    photo_obj = message.photo[-1]
    clean_title = "".join(c if c.isalnum() else "_" for c in group_prefix[:20])
    thumb_file_path = thumb_dir / f"thumb_{clean_title}_{photo_obj.file_unique_id}.jpg"
    await message.bot.download(photo_obj, destination=thumb_file_path)

    from sqlalchemy import select
    from src.db.session import get_db_session
    from src.db.models import MediaItem

    count = 0
    async for db in get_db_session():
        stmt = select(MediaItem).where(
            MediaItem.status.in_([PipelineStatus.SCRAPED, PipelineStatus.ENRICHED]),
            MediaItem.shadow_message_id.is_(None)
        )
        res = await db.execute(stmt)
        all_pending = list(res.scalars().all())
        for item in all_pending:
            if (item.parsed_title or "Unknown Title").startswith(group_prefix):
                item.custom_thumbnail_path = str(thumb_file_path)
                count += 1
        await db.commit()
        break

    await state.clear()
    await message.reply(f"✅ **Custom Thumbnail Saved & Attached to `{count}` files in `{group_prefix}`!**", parse_mode="Markdown")
    await send_grouped_media_library(message)


@router.callback_query(F.data.startswith("g_process:"))
async def on_group_process(callback: CallbackQuery) -> None:
    """Enqueue all pending files in the selected group for Phase 3 I/O shadow upload."""
    if settings.ADMIN_USER_ID and callback.from_user.id != settings.ADMIN_USER_ID:
        return
    group_prefix = callback.data.split("g_process:")[1]
    from sqlalchemy import select
    from src.db.session import get_db_session
    from src.db.models import MediaItem
    from arq import create_pool
    from arq.connections import RedisSettings

    items = []
    async for db in get_db_session():
        stmt = select(MediaItem).where(
            MediaItem.status.in_([PipelineStatus.SCRAPED, PipelineStatus.ENRICHED]),
            MediaItem.shadow_message_id.is_(None)
        )
        res = await db.execute(stmt)
        all_pending = list(res.scalars().all())
        items = [i for i in all_pending if (i.parsed_title or "Unknown Title").startswith(group_prefix)]
        for i in items:
            i.status = PipelineStatus.CONFIRMED
        await db.commit()
        break

    redis_pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    enqueued = 0
    for item in items:
        try:
            await redis_pool.enqueue_job("process_media_io_task", str(item.id))
            enqueued += 1
        except Exception as q_err:
            logger.warning(f"Could not enqueue process_media_io_task for ID={item.id}: {q_err}")
    await redis_pool.aclose()

    if callback.message:
        await callback.message.edit_text(
            f"🚀 **Started Processing for `{group_prefix}`!**\n"
            f"Enqueued `{enqueued}` / `{len(items)}` file(s) for high-speed download, renaming, and shadow channel document upload.",
            parse_mode="Markdown"
        )
    await callback.answer("🚀 Processing enqueued!")


@router.callback_query(F.data == "g_upload_all")
async def on_group_upload_all(callback: CallbackQuery) -> None:
    """Enqueue ALL pending files across all groups for Phase 3 I/O shadow upload."""
    if settings.ADMIN_USER_ID and callback.from_user.id != settings.ADMIN_USER_ID:
        return
    from sqlalchemy import select
    from src.db.session import get_db_session
    from src.db.models import MediaItem
    from arq import create_pool
    from arq.connections import RedisSettings

    items = []
    async for db in get_db_session():
        stmt = select(MediaItem).where(
            MediaItem.status.in_([PipelineStatus.SCRAPED, PipelineStatus.ENRICHED]),
            MediaItem.shadow_message_id.is_(None)
        )
        res = await db.execute(stmt)
        items = list(res.scalars().all())
        for i in items:
            i.status = PipelineStatus.CONFIRMED
        await db.commit()
        break

    redis_pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    enqueued = 0
    for item in items:
        try:
            await redis_pool.enqueue_job("process_media_io_task", str(item.id))
            enqueued += 1
        except Exception as e:
            logger.warning(f"Could not enqueue ID={item.id}: {e}")
    await redis_pool.aclose()

    if callback.message:
        await callback.message.edit_text(
            f"🚀 **Started Processing ALL Pending Media (`{enqueued}` files)!**",
            parse_mode="Markdown"
        )
    await callback.answer("🚀 All items enqueued!")
