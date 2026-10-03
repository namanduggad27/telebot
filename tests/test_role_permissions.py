import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message, User, Chat, CallbackQuery

from config.settings import settings
from src.bot.handlers import is_admin_user, on_start_command, on_files_command


def test_is_admin_user_check():
    """Verify is_admin_user correctly detects admins based on settings."""
    admin_id = 1170258624
    admin_id_2 = 1626129666
    normal_id = 999999999

    admin_user = User(id=admin_id, is_bot=False, first_name="Naman")
    admin_user_2 = User(id=admin_id_2, is_bot=False, first_name="Admin2")
    normal_user = User(id=normal_id, is_bot=False, first_name="NormalUser")

    assert is_admin_user(admin_user) is True
    assert is_admin_user(admin_user_2) is True
    assert is_admin_user(normal_user) is False
    assert is_admin_user(None) is False


@pytest.mark.asyncio
async def test_normal_user_bare_start_shows_welcome():
    """Verify regular user sending bare /start gets the public welcome message."""
    message = MagicMock(spec=Message)
    message.text = "/start"
    message.chat = MagicMock(spec=Chat, id=888888)
    message.from_user = User(id=888888, is_bot=False, first_name="RegularSubscriber")
    message.reply = AsyncMock()

    await on_start_command(message)

    message.reply.assert_called_once()
    args, kwargs = message.reply.call_args
    assert "Welcome to Media Hub Bot" in args[0]
    assert kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_normal_user_denied_admin_files_command():
    """Verify regular user is denied access to /files."""
    message = MagicMock(spec=Message)
    message.text = "/files"
    message.chat = MagicMock(spec=Chat, id=888888)
    message.from_user = User(id=888888, is_bot=False, first_name="RegularSubscriber")
    message.reply = AsyncMock()

    await on_files_command(message)

    message.reply.assert_called_once()
    args, _ = message.reply.call_args
    assert "Access Denied" in args[0]


@pytest.mark.asyncio
async def test_batch_link_delivers_to_anyone():
    """Verify deep-link start parameter executes delivery for any user."""
    message = MagicMock(spec=Message)
    message.text = "/start b_testtoken123"
    message.chat = MagicMock(spec=Chat, id=888888)
    message.from_user = User(id=888888, is_bot=False, first_name="RegularSubscriber")
    status_mock = MagicMock()
    status_mock.delete = AsyncMock()
    message.reply = AsyncMock(return_value=status_mock)
    message.bot = MagicMock()

    with patch("src.services.native_batch_engine.NativeBatchEngine.handle_start_parameter", new_callable=AsyncMock) as mock_handle:
        mock_handle.return_value = (3, "")

        await on_start_command(message)

        mock_handle.assert_called_once_with(message.bot, 888888, "b_testtoken123")
        # Transient status message was created and deleted; no redundant delivered files success text is sent
        status_mock.delete.assert_called_once()
        assert message.reply.call_count == 1
