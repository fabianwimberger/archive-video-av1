"""Job database model."""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, Text, DateTime, Index
from app.database import Base


class Job(Base):
    """Conversion job model."""

    __tablename__ = "jobs"

    # Primary key
    id = Column(Integer, primary_key=True, index=True)

    # File paths
    source_file = Column(String, nullable=False)
    output_file = Column(String, nullable=False)

    # Conversion settings
    mode = Column(String, nullable=False, default="default")  # 'default', 'anime' or 'grainy'
    settings = Column(Text, default="{}")  # JSON string with CRF, preset, etc.

    # Status tracking
    status = Column(String, nullable=False, default="pending")  # pending, processing, completed, failed
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

    # Indexes
    __table_args__ = (
        Index('idx_jobs_status', 'status'),
        Index('idx_jobs_created_at', 'created_at'),
    )
