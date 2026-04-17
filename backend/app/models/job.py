"""Job database model."""

from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Text,
    DateTime,
    Index,
    BigInteger,
    ForeignKey,
)
from app.database import Base


class Job(Base):
    """Conversion job model."""

    __tablename__ = "jobs"

    # Primary key
    id = Column(Integer, primary_key=True, index=True)

    # File paths
    source_file = Column(String, nullable=False)
    output_file = Column(String, nullable=False)

    # Conversion settings snapshot
    preset_id = Column(
        Integer, ForeignKey("presets.id", ondelete="SET NULL"), nullable=True
    )
    preset_name_snapshot = Column(String, nullable=True)
    settings = Column(Text, default="{}")  # JSON string with CRF, preset, etc.
    notes = Column(Text, nullable=True)
    queue_position = Column(Integer, nullable=True)

    # Status tracking
    status = Column(
        String, nullable=False, default="pending"
    )  # pending, processing, completed, failed, cancelled
    progress_percent = Column(Float, default=0.0)
    eta_seconds = Column(Integer, nullable=True)
    current_fps = Column(Float, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Error handling
    error_message = Column(Text, nullable=True)
    log = Column(Text, default="")

    # File size tracking (for completed jobs)
    source_size_bytes = Column(BigInteger, nullable=True)
    output_size_bytes = Column(BigInteger, nullable=True)

    # Indexes
    __table_args__ = (
        Index("idx_jobs_status_completed_at", "status", "completed_at"),
        Index("idx_jobs_source_file", "source_file"),
        Index("idx_jobs_status_queue_position", "status", "queue_position"),
    )
