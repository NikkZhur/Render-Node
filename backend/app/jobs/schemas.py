"""Pydantic contracts for Job endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.jobs.types import ComputeDevice, FrameMode, JobStatus, RenderEngine


class JobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    blender_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    engine: RenderEngine
    device: ComputeDevice
    gpu_ids: list[int] = Field(default_factory=list, max_length=16)
    frame_mode: FrameMode
    frame_start: int | None = Field(default=None, ge=1, le=10_000_000)
    frame_end: int | None = Field(default=None, ge=1, le=10_000_000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(ord(character) < 32 for character in cleaned):
            raise ValueError("name must contain printable characters")
        return cleaned

    @field_validator("gpu_ids")
    @classmethod
    def validate_gpu_ids(cls, value: list[int]) -> list[int]:
        if any(gpu_id < 0 for gpu_id in value):
            raise ValueError("gpu_ids cannot contain negative values")
        if len(set(value)) != len(value):
            raise ValueError("gpu_ids cannot contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_render_selection(self) -> Self:
        if self.device is ComputeDevice.CPU and self.gpu_ids:
            raise ValueError("CPU jobs cannot reserve GPU ids")
        if self.device is not ComputeDevice.CPU and not self.gpu_ids:
            raise ValueError("CUDA and OPTIX jobs require at least one GPU id")

        if self.frame_mode is FrameMode.SINGLE:
            if self.frame_start is None or self.frame_end is not None:
                raise ValueError("SINGLE requires frame_start and no frame_end")
        elif self.frame_mode is FrameMode.RANGE:
            if self.frame_start is None or self.frame_end is None:
                raise ValueError("RANGE requires frame_start and frame_end")
            if self.frame_end < self.frame_start:
                raise ValueError("frame_end must be greater than or equal to frame_start")
        elif self.frame_start is not None or self.frame_end is not None:
            raise ValueError("ALL does not accept frame_start or frame_end")
        return self


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    source_filename: str | None
    status: JobStatus
    blender_version: str
    engine: RenderEngine
    device: ComputeDevice
    gpu_ids: list[int]
    frame_mode: FrameMode
    frame_start: int | None
    frame_end: int | None
    current_frame: int | None
    progress: float = Field(ge=0, le=1)
    process_pid: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    exit_code: int | None
    error: str | None
