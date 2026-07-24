import asyncio
import logging
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
                thumb_path = Path(settings.GLOBAL_THUMBNAIL_PATH)
                if thumb_path.exists():
                    logger.info(f"Using global thumbnail for ID={item_id}: {thumb_path}")
                    is_global_thumb = True
                else:
                    logger.warning(f"Global thumbnail not found at {thumb_path}. Sending without thumbnail.")
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
                try:
                    caption = settings.CAPTION_FORMAT.format(
                        title=item.parsed_title or 'Unknown Title',
                        season=item.season_num if item.season_num is not None else 0,
                        episode=item.episode_num if item.episode_num is not None else 0,
                        quality=item.quality_tag or 'Unknown',
                        filename=clean_name
                    )
                except KeyError:
                    # Fallback if format string is malformed
                    season_str = f"S{item.season_num:02d}" if item.season_num is not None else "N/A"
                    episode_str = f"E{item.episode_num:02d}" if item.episode_num is not None else "N/A"
                    caption = (
                        f"🎬 **{item.parsed_title or 'Unknown Title'}**\n"
                        f"📺 **{season_str} / {episode_str}** | `{item.quality_tag or 'HD'}`\n"
                        f"📂 `{clean_name}`\n"
                        f"🆔 `{item.file_unique_id}`"
                    )

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
                    thumb=str(thumb_path) if thumb_path and thumb_path.exists() else None,
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

            except Exception as e:
                logger.error(f"Fatal error during MTProto I/O processing for ID={item_id}: {e}", exc_info=True)
                await StateMachine.transition_item(item_id, PipelineStatus.FAILED)
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
            
            thumb_path = Path(settings.GLOBAL_THUMBNAIL_PATH)
            photo_file = str(thumb_path) if thumb_path.exists() else None
            
            pres_text = settings.PRESENTATION_FORMAT.format(
                title=first_item.parsed_title or "Unknown",
                year="2024",
                quality=first_item.quality_tag or "1080p",
                audio="Hindi / English"
            )
            
            logger.info("Sending presentation photo to shadow channel...")
            if photo_file:
                await cls._safe_mtproto_action(
                    client.send_photo,
                    chat_id=dest_channel,
                    photo=photo_file,
                    caption=pres_text,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await cls._safe_mtproto_action(
                    client.send_message,
                    chat_id=dest_channel,
                    text=pres_text,
                    parse_mode=ParseMode.MARKDOWN
                )
                
            for item in items:
                await StateMachine.transition_item(item.id, PipelineStatus.DOWNLOADING)
                
                raw_msg = await cls._safe_mtproto_action(client.get_messages, item.raw_channel_id, item.raw_message_id)
                media_obj = raw_msg.video or raw_msg.document if raw_msg else None
                if not media_obj:
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
                
                await StateMachine.transition_item(item.id, PipelineStatus.UPLOADING_SHADOW)
                
                # Use empty caption for actual files in shadow channel!
                upload_tracker = ProgressTracker("UPLOAD", str(item.id), clean_name, telegram_message_id=download_tracker.telegram_message_id)
                sent_msg = await cls._safe_mtproto_action(
                    client.send_document,
                    chat_id=dest_channel,
                    document=str(scratch_path),
                    caption="",
                    parse_mode=ParseMode.MARKDOWN,
                    file_name=clean_name,
                    thumb=photo_file,
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

            logger.info("Batch uploaded to shadow. Generating batch link...")
            batch_url = await NativeBatchEngine.generate_custom_batch_url(item_ids)
            
            main_channel = settings.MAIN_CHANNEL_ID
            if main_channel:
                logger.info(f"Posting batch link to Main Channel ({main_channel})...")
                post_text = f"{pres_text}\n\n🔗 **[BATCH LINK]({batch_url})**"
                if photo_file:
                    try:
                        from aiogram import Bot
                        from aiogram.client.default import DefaultBotProperties
                        bot = Bot(token=settings.ADMIN_BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
                        from aiogram.types import FSInputFile
                        await bot.send_photo(chat_id=main_channel, photo=FSInputFile(photo_file), caption=post_text)
                        await bot.session.close()
                    except Exception as e:
                        logger.error(f"Failed to post to main channel via Bot: {e}")
                else:
                    await cls._safe_mtproto_action(
                        client.send_message,
                        chat_id=main_channel,
                        text=post_text,
                        parse_mode=ParseMode.MARKDOWN
                    )
            
            return True
            
        except Exception as e:
            logger.error(f"Fatal error during batch I/O: {e}", exc_info=True)
            return False
        finally:
            if client and client.is_connected:
                await client.stop()

