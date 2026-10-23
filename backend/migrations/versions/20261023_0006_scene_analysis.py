"""Persist scene inspection and automatic setup independently of render state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261023_0006"
down_revision: str | None = "20260818_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("analysis_id", sa.Uuid(), nullable=True))
    op.add_column(
        "jobs",
        sa.Column(
            "analysis_status",
            sa.Enum(
                "none",
                "pending",
                "running",
                "completed",
                "failed",
                name="analysis_status",
                native_enum=False,
            ),
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column("jobs", sa.Column("analysis_error", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("scene_metadata", sa.JSON(), nullable=True))
    op.add_column(
        "jobs", sa.Column("auto_configured", sa.Boolean(), nullable=False, server_default="0")
    )


def downgrade() -> None:
    for name in (
        "auto_configured",
        "scene_metadata",
        "analysis_error",
        "analysis_status",
        "analysis_id",
    ):
        op.drop_column("jobs", name)
