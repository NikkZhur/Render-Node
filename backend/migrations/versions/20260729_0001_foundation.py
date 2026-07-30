"""Establish the phase 1 schema baseline.

Revision ID: 20260729_0001
Revises:
Create Date: 2026-07-29 00:00:00
"""

from collections.abc import Sequence

revision: str = "20260729_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No domain tables are introduced before phase 2."""


def downgrade() -> None:
    """The baseline has no domain schema to remove."""
