"""Move queue failover to the shared ledger and count requeues

Revision ID: 0004_requeue_count
Revises: 0003_queue_replication
Create Date: 2026-09-24 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op  # type: ignore
import sqlalchemy as sa

revision: str = "0004_requeue_count"
down_revision: Union[str, None] = "0003_queue_replication"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("requeue_count", sa.Integer(), nullable=False, server_default="0"),
    )
    # Follower copies of the leader's queue are superseded by the shared ledger.
    op.execute("DELETE FROM jobs WHERE is_cluster_replica")
    op.drop_index("idx_jobs_cluster_replica_origin", table_name="jobs")
    op.drop_column("jobs", "is_cluster_replica")


def downgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "is_cluster_replica",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "idx_jobs_cluster_replica_origin",
        "jobs",
        ["is_cluster_replica", "cluster_origin_node_id"],
    )
    op.drop_column("jobs", "requeue_count")
