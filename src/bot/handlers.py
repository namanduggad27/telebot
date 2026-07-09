import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config.settings import settings
from src.db.models import PipelineStatus
from src.services.state_machine import StateMachine

logger = logging.getLogger("bot.handlers")
router = Router()


class EditTitleState(StatesGroup):
    """FSM states for interactive admin title editing."""
    waiting_for_new_title = State()


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

    # Update message text/markup to reflect approval
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.reply(f"✅ **ID `{item_id}` Approved.** Queued for high-speed download & upload to Shadow DB.", parse_mode="Markdown")
        except Exception as e:
            logger.debug(f"Could not edit reply markup: {e}")

    await callback.answer("✅ Approved! Queued for Phase 3 I/O processing.")

    # Push the confirmed item ID to ARQ for high-speed Phase 3 I/O processing
    try:
        from arq import create_pool
        from arq.connections import RedisSettings
        redis_pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        await redis_pool.enqueue_job("process_media_io_task", item_id)
        await redis_pool.aclose()
        logger.info(f"Enqueued process_media_io_task for ID={item_id} on ARQ Redis queue.")
    except Exception as q_err:
        logger.warning(f"Could not enqueue process_media_io_task for ID={item_id}: {q_err}")


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
