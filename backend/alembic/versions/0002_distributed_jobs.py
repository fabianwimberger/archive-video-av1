"""Add distributed job assignment metadata

Revision ID: 0002_distributed_jobs
Revises: 0001_initial
Create Date: 2026-05-23 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op  # type: ignore
import sqlalchemy as sa

revision: str = "0002_distributed_jobs"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("assigned_worker_id", sa.String(), nullable=True))
    op.add_column("jobs", sa.Column("assigned_worker_name", sa.String(), nullable=True))
    op.add_column("jobs", sa.Column("assigned_worker_url", sa.String(), nullable=True))
    op.add_column("jobs", sa.Column("remote_job_id", sa.Integer(), nullable=True))
    op.create_index("idx_jobs_remote_job_id", "jobs", ["remote_job_id"])


def downgrade() -> None:
    op.drop_index("idx_jobs_remote_job_id", table_name="jobs")
    op.drop_column("jobs", "remote_job_id")
    op.drop_column("jobs", "assigned_worker_url")
    op.drop_column("jobs", "assigned_worker_name")
    op.drop_column("jobs", "assigned_worker_id")
