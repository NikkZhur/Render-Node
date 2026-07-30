"""Add jobs.

Revision ID: 20260729_0002
Revises: 20260729_0001
Create Date: 2026-07-29 00:00:01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_0002"
down_revision: str | None = "20260729_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

job_status = sa.Enum(
    "created",
    "ready",
    "queued",
    "rendering",
    "completed",
    "failed",
    "cancelled",
    name="job_status",
    native_enum=False,
)
render_engine = sa.Enum(
    "CYCLES",
    "BLENDER_EEVEE",
    "BLENDER_WORKBENCH",
    name="render_engine",
    native_enum=False,
)
compute_device = sa.Enum("CPU", "CUDA", "OPTIX", name="compute_device", native_enum=False)
frame_mode = sa.Enum("SINGLE", "RANGE", "ALL", name="frame_mode", native_enum=False)


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("scene_path", sa.String(length=1024), nullable=True),
        sa.Column("status", job_status, nullable=False),
        sa.Column("blender_version", sa.String(length=32), nullable=False),
        sa.Column("engine", render_engine, nullable=False),
        sa.Column("device", compute_device, nullable=False),
        sa.Column("gpu_ids", sa.JSON(), nullable=False),
        sa.Column("frame_mode", frame_mode, nullable=False),
        sa.Column("frame_start", sa.Integer(), nullable=True),
        sa.Column("frame_end", sa.Integer(), nullable=True),
        sa.Column("current_frame", sa.Integer(), nullable=True),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("process_pid", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint("progress >= 0 AND progress <= 1", name="ck_jobs_progress"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])
    op.create_index("ix_jobs_status", "jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_created_at", table_name="jobs")
    op.drop_table("jobs")
