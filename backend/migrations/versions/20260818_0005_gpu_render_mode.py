"""Add the persisted GPU render strategy.

Revision ID: 20260818_0005
Revises: 20260730_0004
Create Date: 2026-08-18 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260818_0005"
down_revision: str | None = "20260730_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

gpu_render_mode = sa.Enum(
    "FRAME_PER_GPU",
    "ALL_GPUS_PER_FRAME",
    name="gpu_render_mode",
    native_enum=False,
)


def upgrade() -> None:
    # Existing jobs used one Blender process with every selected GPU, so the
    # migration default must preserve that behavior independently of new-job defaults.
    op.add_column(
        "jobs",
        sa.Column(
            "gpu_render_mode",
            gpu_render_mode,
            nullable=False,
            server_default="ALL_GPUS_PER_FRAME",
        ),
    )


def downgrade() -> None:
    op.drop_column("jobs", "gpu_render_mode")
