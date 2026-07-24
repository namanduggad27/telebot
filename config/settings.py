import os
from pathlib import Path
from pydantic import Field, field_validator
import json
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Core application configuration loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application details
    APP_NAME: str = "TelegramMediaPipeline"
    ENVIRONMENT: str = Field(default="development", description="development / production")
    LOG_LEVEL: str = Field(default="INFO", description="DEBUG, INFO, WARNING, ERROR")

    # Telegram API & Userbot settings (Hydrogram / MTProto)
    TG_API_ID: int = Field(default=0, description="Telegram API ID from my.telegram.org")
    TG_API_HASH: str = Field(default="", description="Telegram API Hash")
    TG_USERBOT_SESSION: str = Field(
        default="pipeline_userbot",
        description="Session name or session string for Hydrogram Userbot",
    )

    # Telegram Channel IDs
    RAW_CHANNEL_ID: int = Field(
        default=0, description="ID of the Raw Channel monitoring unstructured drops"
    )
    SHADOW_CHANNEL_ID: int = Field(
        default=0, description="ID of the Shadow Database Channel for clean archived media"
    )
    MAIN_CHANNEL_ID: int = Field(
        default=0, description="ID of the Main Channel for presentation posts"
    )

    @field_validator("RAW_CHANNEL_ID", "SHADOW_CHANNEL_ID", "MAIN_CHANNEL_ID", mode="before")
    @classmethod
    def normalize_channel_ids(cls, v: int | str) -> int:
        """Automatically prefix -100 to positive channel ID integers if omitted."""
        if not v or int(v) == 0:
            return 0
        v_int = int(v)
        if v_int > 0:
            return int(f"-100{v_int}")
        return v_int

    # Admin Control Bot & HITL
    ADMIN_BOT_TOKEN: str = Field(default="", description="Bot token for Control Bot")
    ADMIN_USER_ID: int = Field(
        default=0, description="Telegram User ID authorized to press PROCEED/ABORT"
    )

    # External APIs
    OMDB_API_KEY: str = Field(default="", description="OMDB API Key")
    LINKS_BOT_USERNAME: str = Field(default="@YourLinksBot", description="Username of Links Bot")

    # Storage & Scratch paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    SCRATCH_DIR: Path = Field(
        default_factory=lambda: Path(os.environ.get("SCRATCH_DIR", Path(__file__).resolve().parent.parent / "scratch")),
        description="Directory for temporary video chunk downloads before upload",
    )
    POSTER_CACHE_DIR: Path = Field(
        default_factory=lambda: Path(os.environ.get("POSTER_CACHE_DIR", Path(__file__).resolve().parent.parent / "cache" / "posters")),
        description="Local directory for cached posters",
    )
    GLOBAL_THUMBNAIL_PATH: str = Field(
        default="assets/default_thumb.jpg", description="Path to the global fallback thumbnail image"
    )
    
    # Custom Formats
    RENAME_FORMAT: str = Field(
        default="{title} - S{season:02d}E{episode:02d} - [{quality}].{ext}",
        description="Format string for the final cleaned filename",
    )
    CAPTION_FORMAT: str = Field(
        default="🎬 **{title}**\n📺 **S{season:02d}E{episode:02d}** | {quality}\n📂 {filename}",
        description="Format string for the Telegram message caption",
    )
    PRESENTATION_FORMAT: str = Field(
        default="🍿 Title: {title}\n📆 Year: {year}\n📦 Quality: {quality}\n🔊 Audio : {audio}\n💬 English Subtitles 👍",
        description="Format string for the photo presentation message",
    )

    # Database & Redis settings
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://pipeline:pipeline@localhost:5432/telegram_pipeline",
        description="Async SQLAlchemy database connection URI",
    )
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0", description="Redis connection URL for ARQ and state"
    )

    # Concurrency and Worker Limits
    MAX_CONCURRENT_TRANSFERS: int = Field(
        default=3,
        description="Maximum concurrent high-speed file transfers per MTProto session (FloodWait safety)",
    )
    STATE_TTL_SECONDS: int = Field(
        default=259200, description="TTL for transient scraped items in Redis (default 72 hours)"
    )

    def ensure_directories(self) -> None:
        """Create necessary scratch and cache directories if they do not exist."""
        self.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
        self.POSTER_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def load_dynamic_config(self) -> None:
        """Load dynamic formats from config.json if it exists."""
        config_path = self.BASE_DIR / "config.json"
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "RENAME_FORMAT" in data:
                        self.RENAME_FORMAT = data["RENAME_FORMAT"]
                    if "CAPTION_FORMAT" in data:
                        self.CAPTION_FORMAT = data["CAPTION_FORMAT"]
                    if "PRESENTATION_FORMAT" in data:
                        self.PRESENTATION_FORMAT = data["PRESENTATION_FORMAT"]
            except Exception as e:
                print(f"Error loading config.json: {e}")

    def save_dynamic_config(self) -> None:
        """Save dynamic formats to config.json."""
        config_path = self.BASE_DIR / "config.json"
        data = {
            "RENAME_FORMAT": self.RENAME_FORMAT,
            "CAPTION_FORMAT": self.CAPTION_FORMAT,
            "PRESENTATION_FORMAT": getattr(self, "PRESENTATION_FORMAT", ""),
        }
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Error saving config.json: {e}")


settings = Settings()
settings.load_dynamic_config()

