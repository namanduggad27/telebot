import logging
import sys
import time
from typing import Any, Optional
import httpx
import redis.asyncio as aioredis

from config.settings import settings

logger = logging.getLogger("services.progress_tracker")


class ProgressTracker:
    """Tracks and formats live progress, speed, and ETA for MTProto streaming downloads and uploads, updating Console, Terminal stdout, Redis, and Telegram."""

    def __init__(
        self,
        action_name: str,
        item_id: str,
        clean_name: str,
        log_interval: float = 3.0,
        redis_pool: Optional[aioredis.Redis] = None,
        telegram_message_id: Optional[int] = None,
    ) -> None:
        self.action_name = action_name.upper()
        self.item_id = str(item_id)
        self.clean_name = clean_name
        self.log_interval = log_interval
        self.redis_pool = redis_pool
        self.telegram_message_id = telegram_message_id

        self.start_time = time.time()
        self.last_log_time = 0.0
        self.last_telegram_time = 0.0
        self.last_terminal_time = 0.0
        self.telegram_interval = 3.8  # Telegram rate limit safety buffer
        self.terminal_interval = 0.5  # Dynamic in-place terminal updates
        self.last_speed_time = 0.0
        self.last_speed_bytes = 0
        self.last_bytes = 0
        self._is_completed = False

    async def _update_telegram_message(self, text: str) -> None:
        """Send or edit a live progress card to the Admin User ID via Telegram Bot API."""
        if not settings.ADMIN_BOT_TOKEN or not settings.ADMIN_USER_ID:
            return

        url_base = f"https://api.telegram.org/bot{settings.ADMIN_BOT_TOKEN}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                if not self.telegram_message_id:
                    resp = await client.post(
                        f"{url_base}/sendMessage",
                        json={
                            "chat_id": settings.ADMIN_USER_ID,
                            "text": text,
                            "parse_mode": "HTML",
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        self.telegram_message_id = data.get("result", {}).get("message_id")
                else:
                    await client.post(
                        f"{url_base}/editMessageText",
                        json={
                            "chat_id": settings.ADMIN_USER_ID,
                            "message_id": self.telegram_message_id,
                            "text": text,
                            "parse_mode": "HTML",
                        },
                    )
            except Exception as e:
                logger.debug(f"Telegram live progress update failed for ID={self.item_id}: {e}")

    async def on_progress(self, current: int, total: int, *args: Any) -> None:
        """Async callback passed to Hydrogram `download_media`, `send_video`, or `send_document`."""
        if total <= 0:
            return

        now = time.time()
        is_finish = current >= total and not self._is_completed

        # Check intervals
        can_log = is_finish or (self.last_log_time == 0.0) or (now - self.last_log_time >= self.log_interval)
        can_telegram = is_finish or (self.last_telegram_time == 0.0) or (now - self.last_telegram_time >= self.telegram_interval)
        can_terminal = is_finish or (self.last_terminal_time == 0.0) or (now - self.last_terminal_time >= self.terminal_interval)

        if not can_log and not can_telegram and not can_terminal:
            return

        if current >= total:
            self._is_completed = True

        elapsed_since_start = now - self.start_time
        if elapsed_since_start <= 0:
            elapsed_since_start = 0.001

        # Calculate speed (bytes per second)
        interval_time = now - self.last_speed_time
        if self.last_speed_time > 0 and interval_time > 0:
            interval_bytes = current - self.last_speed_bytes
            speed_bps = interval_bytes / interval_time
        else:
            speed_bps = current / elapsed_since_start

        if can_log or can_telegram or can_terminal:
            self.last_bytes = current

        if can_log:
            self.last_log_time = now
        if can_telegram:
            self.last_telegram_time = now
        if can_terminal:
            self.last_terminal_time = now
            self.last_speed_time = now
            self.last_speed_bytes = current

        # Calculate percentage
        percentage = min(100.0, (current / total) * 100.0)

        # Format visual ASCII progress bar
        bar_length = 20
        filled_length = int(bar_length * current // total)
        bar = "█" * filled_length + "░" * (bar_length - filled_length)

        # Convert sizes to MB
        current_mb = current / (1024 * 1024)
        total_mb = total / (1024 * 1024)
        speed_mb = speed_bps / (1024 * 1024)

        # Calculate ETA
        if speed_bps > 0 and current < total:
            eta_seconds = int((total - current) / speed_bps)
            if eta_seconds < 60:
                eta_str = f"{eta_seconds}s"
            elif eta_seconds < 3600:
                eta_str = f"{eta_seconds // 60}m {eta_seconds % 60}s"
            else:
                eta_str = f"{eta_seconds // 3600}h {(eta_seconds % 3600) // 60}m"
        else:
            eta_str = "0s"

        progress_msg = (
            f"[{self.action_name}] ID={self.item_id} ('{self.clean_name}') | "
            f"[{bar}] {percentage:.1f}% | "
            f"{current_mb:.2f} MB / {total_mb:.2f} MB | "
            f"Speed: {speed_mb:.2f} MB/s | ETA: {eta_str}"
        )

        if can_terminal:
            end_char = "\n" if is_finish else ""
            try:
                sys.stdout.write(f"\r\033[K{progress_msg}{end_char}")
                sys.stdout.flush()
            except Exception:
                pass

        if can_log:
            logger.info(progress_msg)

            # Update Redis Hash cache so dashboards/APIs can read live progress
            try:
                if self.redis_pool is None:
                    from src.services.state_machine import StateMachine
                    self.redis_pool = StateMachine.get_redis()

                if self.redis_pool:
                    redis_key = f"pipeline:item:{self.item_id}"
                    await self.redis_pool.hset(
                        redis_key,
                        mapping={
                            "progress_action": self.action_name,
                            "progress_percent": f"{percentage:.1f}%",
                            "progress_bar": f"[{bar}] {percentage:.1f}%",
                            "progress_bytes": f"{current_mb:.2f} MB / {total_mb:.2f} MB",
                            "progress_speed": f"{speed_mb:.2f} MB/s",
                            "progress_eta": eta_str,
                            "progress_updated_at": str(now),
                        },
                    )
            except Exception as e:
                logger.debug(f"Could not update Redis progress cache for ID={self.item_id}: {e}")

        if can_telegram:
            icon = "📥" if self.action_name == "DOWNLOAD" else "📤"
            title_text = "DOWNLOADING FROM RAW CHANNEL" if self.action_name == "DOWNLOAD" else "UPLOADING TO SHADOW CHANNEL"
            tg_text = (
                f"{icon} <b>{title_text}</b>\n\n"
                f"📌 <b>File:</b> <code>{self.clean_name}</code>\n"
                f"🆔 <b>Item ID:</b> #{self.item_id}\n\n"
                f"<code>[{bar}] {percentage:.1f}%</code>\n"
                f"💾 <b>Progress:</b> {current_mb:.2f} MB / {total_mb:.2f} MB\n"
                f"⚡ <b>Speed:</b> {speed_mb:.2f} MB/s | ⏱ <b>ETA:</b> {eta_str}"
            )
            if is_finish:
                tg_text = (
                    f"✅ <b>{self.action_name} COMPLETE</b>\n\n"
                    f"📌 <b>File:</b> <code>{self.clean_name}</code>\n"
                    f"🆔 <b>Item ID:</b> #{self.item_id}\n\n"
                    f"<code>[{bar}] 100.0%</code>\n"
                    f"💾 <b>Total Size:</b> {total_mb:.2f} MB\n"
                    f"⚡ <b>Avg Speed:</b> {speed_mb:.2f} MB/s"
                )
            await self._update_telegram_message(tg_text)
