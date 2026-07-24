import logging
from typing import Optional, List
from aiogram import Bot
from sqlalchemy import select

from config.settings import settings
from src.db.models import MediaItem, PipelineStatus, BatchLink
from src.db.session import get_db_session
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
    async def handle_start_parameter(cls, bot: Bot, chat_id: int, param: str) -> int:
        """Process `/start <param>` deep link: fetch matching items from DB and deliver them to user via `copy_message`."""
        param = param.strip()
        delivered_count = 0

        async for db in get_db_session():
            items_to_send: List[MediaItem] = []

            # Case A: Season Batch (`s_{omdb_id}_{season_num}`)
            if param.startswith("s_"):
                parts = param.split("_")
                if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                    omdb_id = int(parts[1])
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

            # Case B: Single File (`f_{clean_uuid}`)
            elif param.startswith("f_"):
                clean_uuid = param[2:]
                logger.info(f"Delivering Single File to user {chat_id}: clean_uuid={clean_uuid}")
                stmt = select(MediaItem).where(MediaItem.shadow_message_id.is_not(None))
                res = await db.execute(stmt)
                all_items = res.scalars().all()
                for i in all_items:
                    if str(i.id).replace("-", "") == clean_uuid:
                        items_to_send = [i]
                        break

            # Case C: Custom Batch (`b_{token}`)
            elif param.startswith("b_"):
                token = param[2:]
                logger.info(f"Delivering Custom Batch to user {chat_id}: token={token}")
                from src.db.models import BatchLink
                stmt = select(BatchLink).where(BatchLink.token == token)
                res = await db.execute(stmt)
                batch_link = res.scalar_one_or_none()
                if batch_link:
                    # Fetch all items in order
                    stmt2 = select(MediaItem).where(MediaItem.id.in_(batch_link.item_ids))
                    res2 = await db.execute(stmt2)
                    fetched_items = {i.id: i for i in res2.scalars().all()}
                    items_to_send = [fetched_items[i_id] for i_id in batch_link.item_ids if i_id in fetched_items]

            # Fallback check if user passed full UUID or ID string
            if not items_to_send and param.isdigit():
                stmt = select(MediaItem).where(MediaItem.id == int(param))
                res = await db.execute(stmt)
                item = res.scalar_one_or_none()
                if item and item.shadow_message_id:
                    items_to_send = [item]

            if not items_to_send:
                logger.warning(f"No media items found for start parameter '{param}'")
                return 0

            # Deliver each matched item using fast internal Telegram copy_message
            for item in items_to_send:
                if not item.shadow_message_id or not settings.SHADOW_CHANNEL_ID:
                    continue
                try:
                    await bot.copy_message(
                        chat_id=chat_id,
                        from_chat_id=settings.SHADOW_CHANNEL_ID,
                        message_id=item.shadow_message_id,
                    )
                    delivered_count += 1
                except Exception as e:
                    logger.warning(f"copy_message failed for ID={item.id} (shadow_msg={item.shadow_message_id}): {e}. Trying send_video/document...")
                    # Fallback to send_video / send_document using shadow_file_id
                    try:
                        if item.shadow_file_id:
                            s_num = item.season_num if item.season_num is not None else 1
                            e_num = item.episode_num if item.episode_num is not None else 1
                            caption = f"📌 `{item.parsed_title}` S{s_num:02d}E{e_num:02d}"
                            try:
                                await bot.send_document(chat_id=chat_id, document=item.shadow_file_id, caption=caption, parse_mode="Markdown")
                                delivered_count += 1
                            except Exception:
                                await bot.send_video(chat_id=chat_id, video=item.shadow_file_id, caption=caption, parse_mode="Markdown")
                                delivered_count += 1
                    except Exception as fallback_err:
                        logger.error(f"Fallback delivery failed for ID={item.id}: {fallback_err}")

            break

        return delivered_count
