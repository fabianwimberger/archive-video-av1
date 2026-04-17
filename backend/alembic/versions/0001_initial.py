"""Initial schema with presets, jobs, and app_state

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-17 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op  # type: ignore
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create presets table
    presets_table = op.create_table(
        "presets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, default=False),
        sa.Column("crf", sa.Integer(), nullable=False),
        sa.Column("encoder_preset", sa.Integer(), nullable=False),
        sa.Column("svt_params", sa.String(), nullable=False),
        sa.Column("audio_bitrate", sa.String(), nullable=False),
        sa.Column("skip_crop_detect", sa.Boolean(), nullable=False, default=False),
        sa.Column("max_resolution", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("idx_presets_name", "presets", ["name"], unique=True)

    # Create app_state table
    op.create_table(
        "app_state",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )

    # Create jobs table
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("source_file", sa.String(), nullable=False),
        sa.Column("output_file", sa.String(), nullable=False),
        sa.Column(
            "preset_id",
            sa.Integer(),
            sa.ForeignKey("presets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("preset_name_snapshot", sa.String(), nullable=True),
        sa.Column("settings", sa.Text(), nullable=False, default="{}"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("queue_position", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, default="pending"),
        sa.Column("progress_percent", sa.Float(), default=0.0),
        sa.Column("eta_seconds", sa.Integer(), nullable=True),
        sa.Column("current_fps", sa.Float(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("log", sa.Text(), default=""),
        sa.Column("source_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("output_size_bytes", sa.BigInteger(), nullable=True),
    )
    op.create_index("idx_jobs_status_completed_at", "jobs", ["status", "completed_at"])
    op.create_index("idx_jobs_source_file", "jobs", ["source_file"])
    op.create_index(
        "idx_jobs_status_queue_position", "jobs", ["status", "queue_position"]
    )

    # Insert built-in presets
    op.bulk_insert(
        presets_table,
        [
            {
                "name": "Default",
                "description": "General purpose preset",
                "is_builtin": True,
                "crf": 26,
                "encoder_preset": 4,
                "svt_params": "tune=0:film-grain=8",
                "audio_bitrate": "96k",
                "skip_crop_detect": False,
                "max_resolution": 1080,
            },
            {
                "name": "Animated",
                "description": "Optimized for animated content",
                "is_builtin": True,
                "crf": 35,
                "encoder_preset": 4,
                "svt_params": "tune=0:enable-qm=1:max-tx-size=32",
                "audio_bitrate": "96k",
                "skip_crop_detect": False,
                "max_resolution": 1080,
            },
            {
                "name": "Grainy",
                "description": "Optimized for grainy film content",
                "is_builtin": True,
                "crf": 26,
                "encoder_preset": 4,
                "svt_params": "tune=0:film-grain=16:film-grain-denoise=1",
                "audio_bitrate": "96k",
                "skip_crop_detect": False,
                "max_resolution": 1080,
            },
        ],
    )

    # Set default_preset_id to Default preset (id=1 because it's first)
    op.execute("INSERT INTO app_state (key, value) VALUES ('default_preset_id', '1')")
    op.execute("INSERT INTO app_state (key, value) VALUES ('queue_paused', 'false')")


def downgrade() -> None:
    op.drop_index("idx_jobs_status_queue_position", table_name="jobs")
    op.drop_index("idx_jobs_source_file", table_name="jobs")
    op.drop_index("idx_jobs_status_completed_at", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("app_state")
    op.drop_index("idx_presets_name", table_name="presets")
    op.drop_table("presets")
