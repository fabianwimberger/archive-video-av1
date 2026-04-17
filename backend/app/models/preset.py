"""Preset database model."""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Index
from app.database import Base


class Preset(Base):
    """Conversion preset model."""

    __tablename__ = "presets"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    is_builtin = Column(Boolean, nullable=False, default=False)
    crf = Column(Integer, nullable=False)
    encoder_preset = Column(Integer, nullable=False)
    svt_params = Column(String, nullable=False)
    audio_bitrate = Column(String, nullable=False)
    skip_crop_detect = Column(Boolean, nullable=False, default=False)
    max_resolution = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (Index("idx_presets_name", "name", unique=True),)
