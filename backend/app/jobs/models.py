"""SQLAlchemy Job model."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, Integer, String, Text
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.jobs.types import (
    AnalysisStatus,
    ComputeDevice,
    FrameMode,
    GpuRenderMode,
    JobStatus,
    RenderEngine,
)
from app.storage.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Store UTC in SQLite and restore an aware datetime at the boundary."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


def enum_column(enum_type: type[Any], name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120))
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scene_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    analysis_id: Mapped[UUID | None] = mapped_column(nullable=True)
    analysis_status: Mapped[AnalysisStatus] = mapped_column(
        enum_column(AnalysisStatus, "analysis_status"),
        default=AnalysisStatus.NONE,
        server_default="none",
    )
    analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    scene_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    auto_configured: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    status: Mapped[JobStatus] = mapped_column(
        enum_column(JobStatus, "job_status"), default=JobStatus.CREATED, index=True
    )
    blender_version: Mapped[str] = mapped_column(String(32))
    engine: Mapped[RenderEngine] = mapped_column(enum_column(RenderEngine, "render_engine"))
    device: Mapped[ComputeDevice] = mapped_column(enum_column(ComputeDevice, "compute_device"))
    gpu_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    gpu_render_mode: Mapped[GpuRenderMode] = mapped_column(
        enum_column(GpuRenderMode, "gpu_render_mode"),
        default=GpuRenderMode.ALL_GPUS_PER_FRAME,
        server_default=GpuRenderMode.ALL_GPUS_PER_FRAME.value,
    )
    frame_mode: Mapped[FrameMode] = mapped_column(enum_column(FrameMode, "frame_mode"))
    frame_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frame_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_frame: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    process_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
