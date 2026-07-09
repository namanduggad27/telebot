import pytest
from unittest.mock import AsyncMock, MagicMock
from src.services.links_bot_client import LinksBotClient


@pytest.mark.asyncio
async def test_get_batch_link_fallback():
    """Verify clean fallback URL when no specific links bot username is set."""
    # When LINKS_BOT_USERNAME is not set or default '@YourLinksBot'
    url = await LinksBotClient.get_batch_link(client=None, shadow_message_id=555, shadow_channel_id=-1009876543210)
    assert url == "https://t.me/c/9876543210/555"


def test_url_pattern_regex():
    """Verify extraction of start parameter batch links from reply text."""
    text = "Here is your batch link:\n👉 https://t.me/MyAwesomeLinksBot?start=batch_99887766\nEnjoy!"
    match = LinksBotClient.URL_PATTERN.search(text)
    assert match is not None
    assert match.group(1) == "https://t.me/MyAwesomeLinksBot?start=batch_99887766"
