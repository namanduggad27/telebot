import asyncio
import logging
import re
from typing import Optional
from hydrogram import Client

from config.settings import settings

logger = logging.getLogger("services.links_bot_client")


class LinksBotClient:
    """Interacts with external Telegram Batch Links bots via Userbot to generate grouped/batch URLs."""

    URL_PATTERN = re.compile(r"(https://t\.me/\w+\?start=\w+)", re.IGNORECASE)

    @classmethod
    async def get_batch_link(
        cls, client: Client, shadow_message_id: int, shadow_channel_id: int
    ) -> Optional[str]:
        """Request a batch shareable link for a media message (or fallback to private channel post URL)."""
        if not settings.LINKS_BOT_USERNAME or settings.LINKS_BOT_USERNAME == "@YourLinksBot":
            # Fallback to standard private/public Telegram post link if no links bot is configured
            clean_chat_id = str(shadow_channel_id).replace("-100", "")
            fallback_url = f"https://t.me/c/{clean_chat_id}/{shadow_message_id}"
            logger.info(f"No specific LINKS_BOT_USERNAME configured. Using direct post URL: {fallback_url}")
            return fallback_url

        try:
            logger.info(f"Sending batch link request to {settings.LINKS_BOT_USERNAME} for msg={shadow_message_id}...")
            # Forward the shadow message or send the /batch command to the links bot
            await client.send_message(
                chat_id=settings.LINKS_BOT_USERNAME,
                text=f"/batch {shadow_channel_id} {shadow_message_id}"
            )

            # Poll/wait up to 12 seconds for the bot's response message containing the shareable URL
            for _ in range(6):
                await asyncio.sleep(2.0)
                history = await client.get_history(chat_id=settings.LINKS_BOT_USERNAME, limit=3)
                for msg in history:
                    if msg.text or msg.caption:
                        text = msg.text or msg.caption or ""
                        match = cls.URL_PATTERN.search(text)
                        if match:
                            url = match.group(1)
                            logger.info(f"Successfully obtained batch link: {url}")
                            return url

            logger.warning(f"Timeout waiting for reply from {settings.LINKS_BOT_USERNAME}. Falling back to direct URL.")
        except Exception as e:
            logger.error(f"Error interacting with Links Bot {settings.LINKS_BOT_USERNAME}: {e}")

        # Fallback URL if bot failed
        clean_chat_id = str(shadow_channel_id).replace("-100", "")
        return f"https://t.me/c/{clean_chat_id}/{shadow_message_id}"
