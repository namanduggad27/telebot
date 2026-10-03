import asyncio
import logging
from pathlib import Path
from typing import Optional, List, Tuple
from aiogram import Bot
from sqlalchemy import select

from config.settings import settings
from src.db.models import MediaItem, PipelineStatus, BatchLink
from src.db.session import get_db_session
from src.scrapers.regex_engine import RegexEngine
import uuid

logger = logging.getLogger("services.native_batch_engine")


class NativeBatchEngine:
    """Self-contained native Batch / Shareable Link Engine powered by our own Control Bot (`copy_message`)."""

    _bot_username_cache: Optional[str] = None

    @classmethod
    async def get_bot_username(cls, bot: Optional[Bot] = None) -> str:
        """Fetch and cache our Bot's username from Telegram (`get_me()`)."""
        if cls._bot_username_cache:
            return cls._bot_username_cache

        if bot:
            try:
                me = await bot.get_me()
                if me and me.username:
                    cls._bot_username_cache = me.username
                    return cls._bot_username_cache
            except Exception as e:
                logger.debug(f"Could not get_me from bot instance: {e}")

        # Try initializing temporary bot if token exists
        if settings.ADMIN_BOT_TOKEN:
            try:
                temp_bot = Bot(token=settings.ADMIN_BOT_TOKEN)
                me = await temp_bot.get_me()
                await temp_bot.session.close()
                if me and me.username:
                    cls._bot_username_cache = me.username
                    return cls._bot_username_cache
            except Exception as e:
                logger.debug(f"Could not get_me with ADMIN_BOT_TOKEN: {e}")

        return "YourMediaBot"

    @classmethod
    async def generate_shareable_url(cls, item: MediaItem, prefer_season_batch: bool = True) -> str:
        """Generate a clean deep-link start parameter URL (`https://t.me/BotUsername?start=...`) for our own bot."""
        bot_username = await cls.get_bot_username()

        # If it belongs to a known TV season, generate a grouped season parameter (`s_{omdb_id}_{season_num}`)
        if prefer_season_batch and item.omdb_id and item.season_num is not None:
            return f"https://t.me/{bot_username}?start=s_{item.omdb_id}_{item.season_num}"

        # Otherwise generate a single item parameter (`f_{clean_uuid}`)
        clean_uuid = str(item.id).replace("-", "")
        return f"https://t.me/{bot_username}?start=f_{clean_uuid}"

    @classmethod
    async def generate_custom_batch_url(cls, item_ids: List[int]) -> str:
        """Generate a custom batch URL mapping to specific items."""
        bot_username = await cls.get_bot_username()
        token = uuid.uuid4().hex[:12]
        
        async for db in get_db_session():
            batch_link = BatchLink(token=token, item_ids=item_ids)
            db.add(batch_link)
            await db.commit()
            break
            
        return f"https://t.me/{bot_username}?start=b_{token}"

    @classmethod
    async def handle_start_parameter(cls, bot: Bot, chat_id: int, param: str) -> Tuple[int, str]:
        """Process `/start <param>` deep link: fetch matching items from DB and deliver them to user via `copy_message`.
        
        Returns:
            Tuple[int, str]: (delivered_count, error_message)
        """
        param = param.strip()
        delivered_count = 0
        error_message = ""

        async for db in get_db_session():
            items_to_send: List[MediaItem] = []

            # Case A: Season Batch (`s_{omdb_id}_{season_num}`)
            if param.startswith("s_"):
                parts = param.split("_", 2)
                if len(parts) >= 3 and parts[2].isdigit():
                    omdb_id = parts[1]
                    season_num = int(parts[2])
                    logger.info(f"Delivering Season Batch to user {chat_id}: omdb_id={omdb_id}, season={season_num}")
                    stmt = (
                        select(MediaItem)
                        .where(
                            MediaItem.omdb_id == omdb_id,
                            MediaItem.season_num == season_num,
                            MediaItem.shadow_message_id.is_not(None),
                        )
                        .order_by(MediaItem.episode_num)
                    )
                    res = await db.execute(stmt)
                    items_to_send = list(res.scalars().all())

            # Case B: Single File (`f_{id_or_uuid}`)
            elif param.startswith("f_"):
                file_token = param[2:]
                logger.info(f"Delivering Single File to user {chat_id}: file_token={file_token}")
                if file_token.isdigit():
                    stmt = select(MediaItem).where(
                        MediaItem.id == int(file_token),
                        MediaItem.shadow_message_id.is_not(None)
                    )
                    res = await db.execute(stmt)
                    item = res.scalar_one_or_none()
                    if item:
                        items_to_send = [item]
                else:
                    stmt = select(MediaItem).where(MediaItem.shadow_message_id.is_not(None))
                    res = await db.execute(stmt)
                    for i in res.scalars().all():
                        if str(i.id).replace("-", "") == file_token:
                            items_to_send = [i]
                            break

            # Case C: Custom Batch (`b_{token}`)
            elif param.startswith("b_"):
                token = param[2:]
                logger.info(f"Delivering Custom Batch to user {chat_id}: token={token}")
                stmt = select(BatchLink).where(BatchLink.token == token)
                res = await db.execute(stmt)
                batch_link = res.scalar_one_or_none()
                if batch_link and batch_link.item_ids:
                    stmt2 = select(MediaItem).where(MediaItem.id.in_(batch_link.item_ids))
                    res2 = await db.execute(stmt2)
                    fetched_items = {i.id: i for i in res2.scalars().all()}
                    items_to_send = [fetched_items[i_id] for i_id in batch_link.item_ids if i_id in fetched_items]

            # Fallback check if user passed a raw integer ID
            if not items_to_send and param.isdigit():
                stmt = select(MediaItem).where(MediaItem.id == int(param), MediaItem.shadow_message_id.is_not(None))
                res = await db.execute(stmt)
                item = res.scalar_one_or_none()
                if item:
                    items_to_send = [item]

            if not items_to_send:
                logger.warning(f"No media items found for start parameter '{param}'")
                return 0, "No media found matching this link. It may have expired or been removed."

            # 1. Send the shadow description about the files (text only; thumbnail is attached to the files)
            first_item = items_to_send[0]
            desc_text = RegexEngine.format_shadow_description(first_item)
            try:
                await bot.send_message(chat_id=chat_id, text=desc_text, parse_mode="Markdown")
            except Exception as e_msg:
                logger.warning(f"Failed to send presentation message to user {chat_id}: {e_msg}")

            # 2. Deliver each matched item using fast Telegram copy_message
            for idx, item in enumerate(items_to_send):
                if not item.shadow_message_id or not settings.SHADOW_CHANNEL_ID:
                    logger.warning(f"Item ID={item.id} is missing shadow_message_id or SHADOW_CHANNEL_ID is not set.")
                    continue
                try:
                    await bot.copy_message(
                        chat_id=chat_id,
                        from_chat_id=settings.SHADOW_CHANNEL_ID,
                        message_id=item.shadow_message_id,
                    )
                    delivered_count += 1
                    if len(items_to_send) > 1 and idx < len(items_to_send) - 1:
                        await asyncio.sleep(0.3)
                except Exception as e:
                    err_str = str(e)
                    logger.warning(
                        f"copy_message failed for ID={item.id} (shadow_msg={item.shadow_message_id}) to user {chat_id}: {e}"
                    )
                    if "message cannot be copied" in err_str.lower():
                        error_message = (
                            "Telegram error: Media cannot be copied. If you are an administrator, "
                            "please disable 'Restrict saving content' on your Shadow Channel."
                        )
                    elif "blocked" in err_str.lower() or "chat not found" in err_str.lower():
                        error_message = "Cannot send files: User has blocked the bot or chat is inaccessible."
                    else:
                        error_message = f"Telegram error: {err_str}"

            break

        return delivered_count, error_message
