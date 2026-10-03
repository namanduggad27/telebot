import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message, User, Chat, CallbackQuery, PhotoSize

from config.settings import settings, Settings
from src.bot.handlers import (
    on_thumbnail_command,
    on_thumb_menu_callback,
    on_thumb_delete_callback,
    on_thumbnail_received,
    ConfigState,
)


@pytest.mark.asyncio
async def test_thumbnail_command_denied_for_normal_user():
    """Verify regular subscriber is denied access to /thumbnail command."""
    message = MagicMock(spec=Message)
    message.text = "/thumbnail"
    message.chat = MagicMock(spec=Chat, id=888888)
    message.from_user = User(id=888888, is_bot=False, first_name="RegularSubscriber")
    message.reply = AsyncMock()

    await on_thumbnail_command(message, state=MagicMock())

    message.reply.assert_called_once()
    assert "Access Denied" in message.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_thumbnail_command_allowed_for_admin_empty(tmp_path):
    """Verify admin gets the thumbnail menu when no custom thumbnail is set."""
    message = MagicMock(spec=Message)
    message.text = "/thumbnail"
    message.chat = MagicMock(spec=Chat, id=1170258624)
    message.from_user = User(id=1170258624, is_bot=False, first_name="Naman")
    message.reply = AsyncMock()

    with patch.object(Settings, "get_thumbnail_path", return_value=None):
        await on_thumbnail_command(message, state=MagicMock())

    message.reply.assert_called_once()
    assert "Global Thumbnail Management" in message.reply.call_args[0][0]
    assert "No Custom Thumbnail Set" in message.reply.call_args[0][0]
    markup = message.reply.call_args[1]["reply_markup"]
    assert any("Upload Thumbnail" in btn.text for row in markup.inline_keyboard for btn in row)


@pytest.mark.asyncio
async def test_thumbnail_command_allowed_for_admin_active(tmp_path):
    """Verify admin gets the thumbnail menu with photo preview when thumbnail exists."""
    thumb_file = tmp_path / "global_thumb.jpg"
    thumb_file.write_bytes(b"dummy image bytes")

    message = MagicMock(spec=Message)
    message.text = "/thumbnail"
    message.chat = MagicMock(spec=Chat, id=1170258624)
    message.from_user = User(id=1170258624, is_bot=False, first_name="Naman")
    message.reply_photo = AsyncMock()

    with patch.object(Settings, "get_thumbnail_path", return_value=thumb_file):
        await on_thumbnail_command(message, state=MagicMock())

    message.reply_photo.assert_called_once()
    assert "Global Thumbnail Management" in message.reply_photo.call_args[1]["caption"]
    assert "Active & Saved Permanently" in message.reply_photo.call_args[1]["caption"]
    markup = message.reply_photo.call_args[1]["reply_markup"]
    assert any("Delete Thumbnail" in btn.text for row in markup.inline_keyboard for btn in row)


@pytest.mark.asyncio
async def test_thumb_delete_callback_clears_setting(tmp_path):
    """Verify thumb_delete callback clears setting and persists removal to config.json."""
    thumb_file = tmp_path / "global_thumb.jpg"
    thumb_file.write_bytes(b"dummy image bytes")

    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = User(id=1170258624, is_bot=False, first_name="Naman")
    callback.data = "thumb_delete"
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.photo = False
    callback.message.edit_text = AsyncMock()

    with patch.object(Settings, "get_thumbnail_path", return_value=thumb_file), \
         patch.object(Settings, "save_dynamic_config") as mock_save:
        await on_thumb_delete_callback(callback, state=MagicMock())

    assert not thumb_file.exists()
    assert settings.GLOBAL_THUMBNAIL_PATH == ""
    mock_save.assert_called_once()
    callback.answer.assert_called_once_with("Thumbnail removed successfully.", show_alert=True)


@pytest.mark.asyncio
async def test_thumbnail_received_saves_and_persists(tmp_path):
    """Verify receiving a photo saves to assets/global_thumb.jpg and calls save_dynamic_config."""
    message = MagicMock(spec=Message)
    message.from_user = User(id=1170258624, is_bot=False, first_name="Naman")
    photo_size = MagicMock(spec=PhotoSize, file_id="mock_file_id_123")
    message.photo = [photo_size]
    message.document = None
    status_mock = MagicMock()
    status_mock.delete = AsyncMock()
    message.reply = AsyncMock(return_value=status_mock)
    message.reply_photo = AsyncMock()

    mock_bot = MagicMock()
    async def fake_download(file, destination):
        destination.write_bytes(b"downloaded image content")
    mock_bot.download = AsyncMock(side_effect=fake_download)
    message.bot = mock_bot

    state = MagicMock()
    state.clear = AsyncMock()

    orig_base = settings.BASE_DIR
    settings.BASE_DIR = tmp_path
    try:
        with patch.object(Settings, "save_dynamic_config") as mock_save:
            await on_thumbnail_received(message, state)

            expected_dest = tmp_path / "assets" / "global_thumb.jpg"
            assert expected_dest.exists()
            assert settings.GLOBAL_THUMBNAIL_PATH == str(expected_dest)
            mock_save.assert_called_once()
            state.clear.assert_called_once()
            message.reply_photo.assert_called_once()
            assert "Global Thumbnail Saved Permanently" in message.reply_photo.call_args[1]["caption"]
    finally:
        settings.BASE_DIR = orig_base


def test_settings_load_dynamic_config_thumbnail(tmp_path):
    """Verify load_dynamic_config reads GLOBAL_THUMBNAIL_PATH from config.json."""
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "RENAME_FORMAT": "test_rename",
        "CAPTION_FORMAT": "test_caption",
        "GLOBAL_THUMBNAIL_PATH": "assets/custom_thumb.jpg"
    }))

    orig_base = settings.BASE_DIR
    settings.BASE_DIR = tmp_path
    try:
        settings.load_dynamic_config()
        assert settings.GLOBAL_THUMBNAIL_PATH == "assets/custom_thumb.jpg"
    finally:
        settings.BASE_DIR = orig_base
