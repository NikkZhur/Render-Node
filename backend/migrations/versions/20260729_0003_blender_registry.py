"""Add Blender runtimes and operations.

Revision ID: 20260729_0003
Revises: 20260729_0002
Create Date: 2026-07-29 00:00:02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_0003"
down_revision: str | None = "20260729_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "blender_runtimes",
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column(
            "source",
            sa.Enum("bundled", "official", "manual", name="runtime_source", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.Enum(
                "available",
                "downloading",
                "downloaded",
                "installing",
                "installed",
                "failed",
                name="runtime_state",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("supported", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("archive_path", sa.String(length=1024), nullable=True),
        sa.Column("official_filename", sa.String(length=255), nullable=True),
        sa.Column("expected_sha256", sa.String(length=64), nullable=True),
        sa.Column("verified_sha256", sa.String(length=64), nullable=True),
        sa.Column("operation_id", sa.Uuid(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("version"),
    )
    op.create_index("ix_blender_runtimes_state", "blender_runtimes", ["state"])
    op.create_index("ix_blender_runtimes_active", "blender_runtimes", ["active"])
    op.create_table(
        "blender_operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("download", "upload", "install", name="operation_kind", native_enum=False),
            nullable=False,
        ),
        sa.Column("version", sa.String(length=32), nullable=True),
        sa.Column(
            "state",
            sa.Enum(
                "pending",
                "running",
                "completed",
                "failed",
                name="operation_state",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("bytes_processed", sa.Integer(), nullable=False),
        sa.Column("bytes_total", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 1", name="ck_blender_operations_progress"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_blender_operations_version", "blender_operations", ["version"])
    op.create_index("ix_blender_operations_state", "blender_operations", ["state"])


def downgrade() -> None:
    op.drop_index("ix_blender_operations_state", table_name="blender_operations")
    op.drop_index("ix_blender_operations_version", table_name="blender_operations")
    op.drop_table("blender_operations")
    op.drop_index("ix_blender_runtimes_active", table_name="blender_runtimes")
    op.drop_index("ix_blender_runtimes_state", table_name="blender_runtimes")
    op.drop_table("blender_runtimes")
