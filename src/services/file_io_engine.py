import asyncio
import logging
import re
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple, Any, Dict
import httpx
from PIL import Image
from hydrogram import Client
from hydrogram.errors import FloodWait
from hydrogram.enums import ParseMode
from sqlalchemy import select

from config.settings import settings
from src.db.models import MediaItem, PipelineStatus
from src.db.session import get_db_session
from src.services.state_machine import StateMachine
from src.services.progress_tracker import ProgressTracker
from src.scrapers.regex_engine import RegexEngine

logger = logging.getLogger("services.file_io_engine")


class FileIOEngine:
    """Orchestrates MTProto streaming download, local renaming, thumbnail preparation, and Shadow DB channel upload."""

    # Concurrency semaphore to prevent saturating MTProto connections and triggering Telegram FloodWait
    _semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_TRANSFERS)



    @classmethod
    async def _safe_mtproto_action(cls, coro_fn, *args, **kwargs) -> Any:
        """Execute a Hydrogram MTProto action with automatic retry on Telegram FloodWait exceptions."""
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            try:
                return await coro_fn(*args, **kwargs)
            except FloodWait as e:
                sleep_sec = getattr(e, "value", 30) + 2
                logger.warning(f"Telegram FloodWait triggered! Sleeping {sleep_sec} seconds (Attempt {attempt}/{max_retries})...")
                await asyncio.sleep(sleep_sec)
            except Exception as ex:
                logger.error(f"MTProto I/O action failed: {ex}")
                raise
        raise RuntimeError(f"Exceeded {max_retries} retries due to repeated FloodWaits.")

    @classmethod
    def prepare_thumbnail(cls, thumb_path: Optional[Path]) -> Optional[str]:
        """Ensure thumbnail exists, is converted to standard RGB JPEG <= 320x320 for MTProto compliance."""
        if not thumb_path or not thumb_path.exists():
            return None
        try:
            from PIL import Image
            with Image.open(thumb_path) as im:
                if im.format == "JPEG" and im.mode == "RGB" and max(im.size) <= 320:
                    return str(thumb_path)

                settings.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
                norm_thumb = settings.SCRATCH_DIR / f"thumb_{thumb_path.stem}.jpg"
                rgb_im = im.convert("RGB")
                rgb_im.thumbnail((320, 320), Image.Resampling.LANCZOS)
                rgb_im.save(norm_thumb, "JPEG", quality=90, optimize=True)
                return str(norm_thumb)
        except Exception as e:
            logger.debug(f"Thumbnail normalization skipped or failed for {thumb_path}: {e}")
            return str(thumb_path)

    @classmethod
    async def process_item_io(cls, item_id: str) -> bool:
        """Execute full Phase 3 I/O lifecycle: Download -> Rename -> Thumbnail -> Shadow Upload -> Cleanup -> Hand off to Phase 4."""
        logger.info(f"Acquiring transfer semaphore for item ID={item_id}...")
        async with cls._semaphore:
            logger.info(f"Transfer semaphore acquired. Starting I/O processing for ID={item_id}")

            # 1. Fetch item from DB
            item = None
            async for db in get_db_session():
                stmt = select(MediaItem).where(MediaItem.id == int(item_id))
                result = await db.execute(stmt)
                item = result.scalar_one_or_none()
                break

            if not item:
                logger.error(f"MediaItem ID={item_id} not found for I/O processing.")
                return False

            if item.status not in (PipelineStatus.CONFIRMED, PipelineStatus.QUEUED_FOR_IO):
                logger.warning(
                    f"Item ID={item_id} in state {item.status.value}, expected CONFIRMED/QUEUED_FOR_IO. Aborting I/O."
                )
                return False

            # 2. Transition to DOWNLOADING
            await StateMachine.transition_item(item_id, PipelineStatus.DOWNLOADING)

            # 3. Prepare paths and thumbnail
            settings.ensure_directories()
            clean_name = item.clean_file_name or f"{item.parsed_title}.mkv"
            scratch_path = settings.SCRATCH_DIR / f"{item.id}_{clean_name}"
            
            cached_state = await StateMachine.get_cached_state(item_id)
            custom_thumb = item.custom_thumbnail_path or (cached_state.get("custom_thumbnail_path") if cached_state else None)
            
            is_global_thumb = False
            if custom_thumb and Path(custom_thumb).exists():
                thumb_path = Path(custom_thumb)
                logger.info(f"Using custom thumbnail for ID={item_id}: {thumb_path}")
            else:
                thumb_path = settings.get_thumbnail_path()
                if thumb_path:
                    logger.info(f"Using global thumbnail for ID={item_id}: {thumb_path}")
                    is_global_thumb = True
                else:
                    logger.info(f"No global thumbnail found. Sending without thumbnail.")
                    thumb_path = None

            # 4. Initialize MTProto client session
            if not settings.TG_API_ID or not settings.TG_API_HASH:
                logger.error("TG_API_ID or TG_API_HASH missing in settings. Cannot run MTProto I/O transfer.")
                await StateMachine.transition_item(item_id, PipelineStatus.FAILED)
                return False

            # Automatically sync main session auth key to IO session so the background worker never prompts for phone login
            main_sess = settings.BASE_DIR / f"{settings.TG_USERBOT_SESSION}.session"
            io_sess = settings.BASE_DIR / f"{settings.TG_USERBOT_SESSION}_io.session"
            if main_sess.exists() and (not io_sess.exists() or io_sess.stat().st_size < 1000):
                try:
                    import shutil
                    shutil.copy2(main_sess, io_sess)
                    logger.info("Synchronized auth key from main session to IO session.")
                except Exception as sync_err:
                    logger.debug(f"Could not copy session file: {sync_err}")

            client = Client(
                name=f"{settings.TG_USERBOT_SESSION}_io",
                api_id=settings.TG_API_ID,
                api_hash=settings.TG_API_HASH,
                workdir=str(settings.BASE_DIR),
            )

            try:
                await client.start()
                logger.info("MTProto I/O Client connected successfully.")

                # Fetch source message from Raw Channel
                raw_msg = await cls._safe_mtproto_action(client.get_messages, item.raw_channel_id, item.raw_message_id)
                media_obj = raw_msg.video or raw_msg.document if raw_msg else None
                if not media_obj:
                    logger.error(f"Could not locate media object in raw channel {item.raw_channel_id} message {item.raw_message_id}")
                    await StateMachine.transition_item(item_id, PipelineStatus.FAILED)
                    return False

                # 5. Download media chunk-by-chunk to local scratch file with real-time progress bar
                logger.info(f"Downloading raw media ID={item_id} ({round(item.file_size_bytes/(1024*1024), 2)} MB) to {scratch_path}...")
                download_tracker = ProgressTracker("DOWNLOAD", str(item_id), clean_name)
                downloaded_file = await cls._safe_mtproto_action(
                    client.download_media,
                    media_obj,
                    file_name=str(scratch_path),
                    progress=download_tracker.on_progress,
                )
                if not downloaded_file or not Path(downloaded_file).exists():
                    logger.error(f"Download failed for ID={item_id}: file not created at {scratch_path}")
                    await StateMachine.transition_item(item_id, PipelineStatus.FAILED)
                    return False

                logger.info(f"Download complete for ID={item_id}. Transitioning to UPLOADING_SHADOW...")
                await StateMachine.transition_item(item_id, PipelineStatus.UPLOADING_SHADOW)

                # 6. Upload renamed file + thumbnail to Shadow Database Channel with real-time progress bar
                caption = RegexEngine.format_caption(item, settings.CAPTION_FORMAT)

                logger.info(f"Uploading {clean_name} to Shadow Channel ID={settings.SHADOW_CHANNEL_ID}...")
                if not settings.SHADOW_CHANNEL_ID:
                    logger.warning("SHADOW_CHANNEL_ID not set! Using RAW_CHANNEL_ID as fallback destination for testing.")
                dest_channel = settings.SHADOW_CHANNEL_ID or item.raw_channel_id

                upload_tracker = ProgressTracker("UPLOAD", str(item_id), clean_name, telegram_message_id=download_tracker.telegram_message_id)
                sent_msg = await cls._safe_mtproto_action(
                    client.send_document,
                    chat_id=dest_channel,
                    document=str(scratch_path),
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN,
                    file_name=clean_name,
                    thumb=cls.prepare_thumbnail(thumb_path),
                    progress=upload_tracker.on_progress,
                )

                if not sent_msg:
                    logger.error(f"Shadow channel upload returned None for ID={item_id}")
                    await StateMachine.transition_item(item_id, PipelineStatus.FAILED)
                    return False

                shadow_media = sent_msg.video or sent_msg.document
                shadow_file_id = shadow_media.file_id if shadow_media else str(sent_msg.id)

                logger.info(f"Upload complete! Shadow Message ID={sent_msg.id}, File ID='{shadow_file_id[:15]}...'")

                # 7. Update PostgreSQL record with shadow destination IDs
                async for db in get_db_session():
                    stmt = select(MediaItem).where(MediaItem.id == int(item_id))
                    res = await db.execute(stmt)
                    db_item = res.scalar_one_or_none()
                    if db_item:
                        db_item.shadow_message_id = sent_msg.id
                        db_item.shadow_file_id = shadow_file_id
                        await db.commit()
                    break

                # 8. Clean up local scratch files
                try:
                    if scratch_path.exists():
                        scratch_path.unlink()
                        logger.info(f"Cleaned up scratch file: {scratch_path}")
                    if thumb_path and thumb_path.exists() and not is_global_thumb:
                        thumb_path.unlink()
                except Exception as cleanup_err:
                    logger.warning(f"Error cleaning up scratch files for ID={item_id}: {cleanup_err}")

                # 9. Transition to SHADOW_ARCHIVED and trigger Phase 4
                await StateMachine.transition_item(item_id, PipelineStatus.SHADOW_ARCHIVED)
                
                try:
                    from arq import create_pool
                    from arq.connections import RedisSettings
                    redis_pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
                    await redis_pool.enqueue_job("batch_link_and_post_task", item_id)
                    await redis_pool.aclose()
                    logger.info(f"Handed off item ID={item_id} to Phase 4 ARQ worker (`batch_link_and_post_task`).")
                except Exception as q_err:
                    logger.warning(f"Could not enqueue Phase 4 task for ID={item_id}: {q_err}")

                return True

            except (Exception, asyncio.CancelledError) as e:
                logger.error(f"Fatal error or timeout during MTProto I/O processing for ID={item_id}: {e}", exc_info=True)
                
                # Delete item from DB so it can be processed again
                async for db in get_db_session():
                    from sqlalchemy import delete
                    stmt = delete(MediaItem).where(MediaItem.id == int(item_id))
                    await db.execute(stmt)
                    await db.commit()
                    break
                    
                # Notify Admin
                try:
                    from aiogram import Bot
                    from aiogram.client.default import DefaultBotProperties
                    bot = Bot(token=settings.ADMIN_BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
                    err_msg = str(e) or "Task timed out or was cancelled."
                    error_text = f"❌ **I/O Processing Failed!**\n\nError: `{err_msg}`\n\nThe file has been removed from the database. Please forward it to the raw channel again to retry."
                    await bot.send_message(chat_id=settings.ADMIN_USER_ID, text=error_text)
                    await bot.session.close()
                except Exception as notify_err:
                    logger.error(f"Failed to send failure notification to admin: {notify_err}")

                if isinstance(e, asyncio.CancelledError):
                    raise
                return False
            finally:
                if client and client.is_connected:
                    await client.stop()
                    logger.info("MTProto I/O Client disconnected cleanly.")


    @classmethod
    async def process_batch_io(cls, item_ids: list[int]) -> bool:
        """Execute full Phase 3 & 4 I/O lifecycle for a batch of files."""
        from src.services.native_batch_engine import NativeBatchEngine
        from src.scrapers.regex_engine import RegexEngine
        
        logger.info(f"Starting batch I/O processing for IDs={item_ids}")
        
        items = []
        async for db in get_db_session():
            stmt = select(MediaItem).where(MediaItem.id.in_(item_ids)).order_by(MediaItem.episode_num)
            res = await db.execute(stmt)
            items = list(res.scalars().all())
            break
            
        if not items:
            logger.error("No items found for batch.")
            return False
            
        first_item = items[0]
        settings.ensure_directories()
        
        if not settings.TG_API_ID or not settings.TG_API_HASH:
            return False
            
        main_sess = settings.BASE_DIR / f"{settings.TG_USERBOT_SESSION}.session"
        io_sess = settings.BASE_DIR / f"{settings.TG_USERBOT_SESSION}_io.session"
        if main_sess.exists() and (not io_sess.exists() or io_sess.stat().st_size < 1000):
            import shutil
            shutil.copy2(main_sess, io_sess)
            
        client = Client(
            name=f"{settings.TG_USERBOT_SESSION}_io",
            api_id=settings.TG_API_ID,
            api_hash=settings.TG_API_HASH,
            workdir=str(settings.BASE_DIR),
        )
        
        try:
            await client.start()
            dest_channel = settings.SHADOW_CHANNEL_ID or first_item.raw_channel_id
            
            thumb_path = settings.get_thumbnail_path()
            photo_file = str(thumb_path) if thumb_path else None
            
            shadow_desc_text = RegexEngine.format_shadow_description(first_item)
            
            # 1. Download all items in batch first
            downloaded_entries = []
            for item in items:
                await StateMachine.transition_item(item.id, PipelineStatus.DOWNLOADING)
                
                raw_msg = await cls._safe_mtproto_action(client.get_messages, item.raw_channel_id, item.raw_message_id)
                media_obj = raw_msg.video or raw_msg.document if raw_msg else None
                if not media_obj:
                    logger.warning(f"Could not retrieve raw media object for item ID={item.id}")
                    continue
                    
                clean_name = RegexEngine.format_custom_name(item, settings.RENAME_FORMAT)
                item.clean_file_name = clean_name
                scratch_path = settings.SCRATCH_DIR / f"{item.id}_{clean_name}"
                
                download_tracker = ProgressTracker("DOWNLOAD", str(item.id), clean_name)
                downloaded_file = await cls._safe_mtproto_action(
                    client.download_media,
                    media_obj,
                    file_name=str(scratch_path),
                    progress=download_tracker.on_progress,
                )
                
                if scratch_path.exists():
                    downloaded_entries.append((item, scratch_path, clean_name, download_tracker))

            if not downloaded_entries:
                logger.error("No files were successfully downloaded in this batch.")
                return False

            # 2. Generate batch link once files are ready
            logger.info("Batch downloaded. Generating batch link...")
            batch_url = await NativeBatchEngine.generate_custom_batch_url(item_ids)

            # 3. Send presentation description message to Shadow Channel immediately before file uploads (text only, shadow format)
            logger.info("Sending presentation description message to Shadow Channel together with files...")
            await cls._safe_mtproto_action(
                client.send_message,
                chat_id=dest_channel,
                text=shadow_desc_text,
                parse_mode=ParseMode.MARKDOWN
            )

            # 4. Upload each file into the Shadow Channel with formatted caption, clean filename, and thumbnail
            for item, scratch_path, clean_name, download_tracker in downloaded_entries:
                await StateMachine.transition_item(item.id, PipelineStatus.UPLOADING_SHADOW)
                
                raw_thumb = (
                    Path(item.custom_thumbnail_path)
                    if item.custom_thumbnail_path and Path(item.custom_thumbnail_path).exists()
                    else thumb_path
                )
                item_thumb = cls.prepare_thumbnail(raw_thumb)
                
                caption = RegexEngine.format_caption(item, settings.CAPTION_FORMAT)
                upload_tracker = ProgressTracker("UPLOAD", str(item.id), clean_name, telegram_message_id=download_tracker.telegram_message_id)
                sent_msg = await cls._safe_mtproto_action(
                    client.send_document,
                    chat_id=dest_channel,
                    document=str(scratch_path),
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN,
                    file_name=clean_name,
                    thumb=item_thumb,
                    progress=upload_tracker.on_progress,
                )
                
                if sent_msg:
                    shadow_media = sent_msg.video or sent_msg.document
                    shadow_file_id = shadow_media.file_id if shadow_media else str(sent_msg.id)
                    
                    async for db in get_db_session():
                        stmt = select(MediaItem).where(MediaItem.id == item.id)
                        res = await db.execute(stmt)
                        db_item = res.scalar_one_or_none()
                        if db_item:
                            db_item.shadow_message_id = sent_msg.id
                            db_item.shadow_file_id = shadow_file_id
                            await db.commit()
                        break
                        
                if scratch_path.exists():
                    scratch_path.unlink()
                    
                await StateMachine.transition_item(item.id, PipelineStatus.SHADOW_ARCHIVED)
            
            main_channel = settings.MAIN_CHANNEL_ID
            if main_channel:
                logger.info(f"Posting batch link to Main Channel ({main_channel})...")
                rtg_text = RegexEngine.format_rtg_message(first_item, batch_items=items)
                post_text = f"{rtg_text}\n\n🔗 **[BATCH LINK]({batch_url})**"

                # Fetch official poster from TMDB for the RTG message
                poster_path = None
                try:
                    from src.services.tmdb_client import TMDBClient
                    tmdb_cli = TMDBClient()
                    year_val = None
                    m_year = re.search(r"\b(19\d\d|20\d\d)\b", f"{first_item.clean_file_name} {first_item.parsed_title}")
                    if m_year:
                        year_val = int(m_year.group(1))

                    tmdb_res = await tmdb_cli.search_media(first_item.parsed_title, year=year_val)
                    if tmdb_res and tmdb_res.poster_url:
                        scratch_poster = settings.SCRATCH_DIR / f"tmdb_poster_{first_item.id}.jpg"
                        if await tmdb_cli.download_poster(tmdb_res.poster_url, scratch_poster):
                            poster_path = scratch_poster
                            logger.info(f"Retrieved TMDB poster for RTG message: {tmdb_res.poster_url}")
                except Exception as tmdb_err:
                    logger.warning(f"Could not retrieve TMDB poster for RTG message: {tmdb_err}")

                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                reply_markup = InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="🚀 Get Files / Batch Link", url=batch_url)]]
                )

                if poster_path and poster_path.exists():
                    try:
                        from aiogram import Bot
                        from aiogram.client.default import DefaultBotProperties
                        bot = Bot(token=settings.ADMIN_BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
                        from aiogram.types import FSInputFile
                        await bot.send_photo(
                            chat_id=main_channel,
                            photo=FSInputFile(str(poster_path)),
                            caption=post_text,
                            reply_markup=reply_markup,
                        )
                        await bot.session.close()
                    except Exception as e:
                        logger.error(f"Failed to post TMDB poster to main channel via Bot: {e}")
                    finally:
                        try:
                            if poster_path.exists():
                                poster_path.unlink()
                        except Exception:
                            pass
                else:
                    # If there is no poster from TMDB side, show NO image, only text message
                    logger.info("No TMDB poster available. Posting text-only RTG message to Main Channel.")
                    try:
                        from aiogram import Bot
                        from aiogram.client.default import DefaultBotProperties
                        bot = Bot(token=settings.ADMIN_BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
                        await bot.send_message(
                            chat_id=main_channel,
                            text=post_text,
                            parse_mode="Markdown",
                            reply_markup=reply_markup,
                        )
                        await bot.session.close()
                    except Exception as e:
                        logger.warning(f"Failed to post text RTG message via Bot: {e}, falling back to MTProto client")
                        await cls._safe_mtproto_action(
                            client.send_message,
                            chat_id=main_channel,
                            text=post_text,
                            parse_mode=ParseMode.MARKDOWN
                        )
            
            return True
            
        except (Exception, asyncio.CancelledError) as e:
            logger.error(f"Fatal error or timeout during batch I/O: {e}", exc_info=True)
            
            # Delete items from DB so they can be processed again
            async for db in get_db_session():
                from sqlalchemy import delete
                stmt = delete(MediaItem).where(MediaItem.id.in_(item_ids))
                await db.execute(stmt)
                await db.commit()
                break
                
            # Notify Admin
            try:
                from aiogram import Bot
                from aiogram.client.default import DefaultBotProperties
                bot = Bot(token=settings.ADMIN_BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
                err_msg = str(e) or "Task timed out or was cancelled."
                error_text = f"❌ **Batch Processing Failed!**\n\nError: `{err_msg}`\n\nThe files have been removed from the database. Please forward them to the raw channel again to retry."
                await bot.send_message(chat_id=settings.ADMIN_USER_ID, text=error_text)
                await bot.session.close()
            except Exception as notify_err:
                logger.error(f"Failed to send failure notification to admin: {notify_err}")

            if isinstance(e, asyncio.CancelledError):
                raise
            return False
        finally:
            if client and client.is_connected:
                await client.stop()

