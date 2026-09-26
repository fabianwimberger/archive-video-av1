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
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)

    source_file = Column(String, nullable=False)
    output_file = Column(String, nullable=False)

    preset_id = Column(
        Integer, ForeignKey("presets.id", ondelete="SET NULL"), nullable=True
    )
    preset_name_snapshot = Column(String, nullable=True)
    settings = Column(Text, default="{}")  # JSON string with CRF, preset, etc.
    notes = Column(Text, nullable=True)
    queue_position = Column(Integer, nullable=True)

    status = Column(
        String, nullable=False, default="pending"
    )  # pending, processing, completed, failed, cancelled
    assigned_worker_id = Column(String, nullable=True)
    assigned_worker_name = Column(String, nullable=True)
    assigned_worker_url = Column(String, nullable=True)
    remote_job_id = Column(Integer, nullable=True)
    cluster_job_id = Column(String, nullable=True)
    cluster_origin_node_id = Column(String, nullable=True)
    cluster_origin_job_id = Column(Integer, nullable=True)
    requeue_count = Column(Integer, nullable=False, default=0, server_default="0")
    progress_percent = Column(Float, default=0.0)
    eta_seconds = Column(Integer, nullable=True)
    current_fps = Column(Float, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    error_message = Column(Text, nullable=True)
    log = Column(Text, default="")

    source_size_bytes = Column(BigInteger, nullable=True)
    output_size_bytes = Column(BigInteger, nullable=True)

    __table_args__ = (
        Index("idx_jobs_status_completed_at", "status", "completed_at"),
        Index("idx_jobs_source_file", "source_file"),
        Index("idx_jobs_status_queue_position", "status", "queue_position"),
        Index("idx_jobs_remote_job_id", "remote_job_id"),
        Index("idx_jobs_cluster_job_id", "cluster_job_id", unique=True),
    )
