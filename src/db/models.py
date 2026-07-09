import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Enum,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base class for all ORM models."""
    pass


class PipelineStatus(str, enum.Enum):
    """Enumeration of lifecycle statuses for a media item."""
    SCRAPED = "SCRAPED"
    METADATA_ENRICHED = "METADATA_ENRICHED"
    ENRICHED = "METADATA_ENRICHED"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    QUEUED_FOR_IO = "QUEUED_FOR_IO"
    DOWNLOADING = "DOWNLOADING"
    UPLOADING_SHADOW = "UPLOADING_SHADOW"
    SHADOW_ARCHIVED = "SHADOW_ARCHIVED"
    SHADOW_UPLOADED = "SHADOW_ARCHIVED"
    BATCH_LINKING = "BATCH_LINKING"
    BATCH_LINKED = "BATCH_LINKED"
    PUBLISHED = "PUBLISHED"
    FINAL_POSTED = "PUBLISHED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class MediaItem(Base):
    """ORM model representing a tracked media file across Raw, Shadow, and Main channels."""

    __tablename__ = "media_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Raw Channel attributes
    raw_message_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    raw_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    raw_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    file_unique_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Extracted & Verified Metadata
    parsed_title: Mapped[str] = mapped_column(String(255), nullable=False)
    tmdb_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    season_num: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    episode_num: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    quality_tag: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    codec_tag: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    clean_file_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Pipeline status & destination channel links
    status: Mapped[PipelineStatus] = mapped_column(
        Enum(PipelineStatus), default=PipelineStatus.SCRAPED, nullable=False, index=True
    )
    shadow_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    shadow_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    main_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    batch_link_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "tmdb_id",
            "season_num",
            "episode_num",
            "quality_tag",
            name="uq_tmdb_season_episode_quality",
        ),
        Index("idx_media_status_tmdb", "status", "tmdb_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<MediaItem(id={self.id}, unique_id='{self.file_unique_id}', "
            f"clean_name='{self.clean_file_name}', status={self.status.value})>"
        )
