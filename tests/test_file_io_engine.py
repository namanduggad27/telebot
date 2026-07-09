import pytest
from unittest.mock import AsyncMock, patch
from hydrogram.errors import FloodWait
from src.services.file_io_engine import FileIOEngine


@pytest.mark.asyncio
async def test_safe_mtproto_action_retry_on_floodwait():
    """Verify that _safe_mtproto_action catches FloodWait and retries automatically without failing."""
    mock_coro = AsyncMock()
    # First invocation raises FloodWait(value=1), second invocation succeeds returning "SUCCESS"
    flood_exc = FloodWait("FloodWait")
    flood_exc.value = 0.05  # Sleep 50ms for test speed
    mock_coro.side_effect = [flood_exc, "SUCCESS"]

    result = await FileIOEngine._safe_mtproto_action(mock_coro)
    assert result == "SUCCESS"
    assert mock_coro.call_count == 2
