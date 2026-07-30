"""Persistent Blender runtimes and long-running operations."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.blender.types import OperationKind, OperationState, RuntimeSource, RuntimeState
from app.jobs.models import UTCDateTime, enum_column, utc_now
from app.storage.database import Base


class BlenderRuntime(Base):
    __tablename__ = "blender_runtimes"

    version: Mapped[str] = mapped_column(String(32), primary_key=True)
    source: Mapped[RuntimeSource] = mapped_column(enum_column(RuntimeSource, "runtime_source"))
    state: Mapped[RuntimeState] = mapped_column(
        enum_column(RuntimeState, "runtime_state"), index=True
    )
    supported: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    archive_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    official_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expected_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class BlenderOperation(Base):
    __tablename__ = "blender_operations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    kind: Mapped[OperationKind] = mapped_column(enum_column(OperationKind, "operation_kind"))
    version: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    state: Mapped[OperationState] = mapped_column(
        enum_column(OperationState, "operation_state"), index=True
    )
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    bytes_processed: Mapped[int] = mapped_column(Integer, default=0)
    bytes_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
