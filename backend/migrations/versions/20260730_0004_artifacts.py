"""Add persistent render artifacts.

Revision ID: 20260730_0004
Revises: 20260729_0003
Create Date: 2026-07-30 00:00:03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0004"
down_revision: str | None = "20260729_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "frame_original",
                "frame_preview",
                "blender_log",
                "frames_zip",
                name="artifact_kind",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("relative_path", sa.String(length=1024), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("frame", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "kind", "frame", name="uq_artifacts_job_kind_frame"),
    )
    op.create_index("ix_artifacts_job_id", "artifacts", ["job_id"])
    op.create_index("ix_artifacts_kind", "artifacts", ["kind"])
    op.create_index("ix_artifacts_frame", "artifacts", ["frame"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_frame", table_name="artifacts")
    op.drop_index("ix_artifacts_kind", table_name="artifacts")
    op.drop_index("ix_artifacts_job_id", table_name="artifacts")
    op.drop_table("artifacts")
