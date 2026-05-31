"""Add replicated queue metadata

Revision ID: 0003_queue_replication
Revises: 0002_distributed_jobs
Create Date: 2026-05-23 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op  # type: ignore
import sqlalchemy as sa

revision: str = "0003_queue_replication"
down_revision: Union[str, None] = "0002_distributed_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("cluster_job_id", sa.String(), nullable=True))
    op.add_column(
        "jobs", sa.Column("cluster_origin_node_id", sa.String(), nullable=True)
    )
    op.add_column(
        "jobs", sa.Column("cluster_origin_job_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "jobs",
        sa.Column(
            "is_cluster_replica",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index("idx_jobs_cluster_job_id", "jobs", ["cluster_job_id"], unique=True)
    op.create_index(
        "idx_jobs_cluster_replica_origin",
        "jobs",
        ["is_cluster_replica", "cluster_origin_node_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_jobs_cluster_replica_origin", table_name="jobs")
    op.drop_index("idx_jobs_cluster_job_id", table_name="jobs")
    op.drop_column("jobs", "is_cluster_replica")
    op.drop_column("jobs", "cluster_origin_job_id")
    op.drop_column("jobs", "cluster_origin_node_id")
    op.drop_column("jobs", "cluster_job_id")
