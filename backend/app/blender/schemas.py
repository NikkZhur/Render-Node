"""HTTP contracts for Blender runtime management."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.blender.types import OperationKind, OperationState, RuntimeSource, RuntimeState


class RuntimeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: str
    source: RuntimeSource
    state: RuntimeState
    supported: bool
    active: bool
    archive_filename: str | None = None
    expected_sha256: str | None
    verified_sha256: str | None
    operation_id: UUID | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class ReleaseResponse(BaseModel):
    version: str
    filename: str
    channel: str
    supported: bool
    downloaded: bool
    installed: bool
    active: bool
    source: RuntimeSource | None


class OperationAccepted(BaseModel):
    operation_id: UUID


class OperationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: OperationKind
    version: str | None
    state: OperationState
    progress: float = Field(ge=0, le=1)
    bytes_processed: int = Field(ge=0)
    bytes_total: int | None = Field(default=None, ge=0)
    error: str | None
    created_at: datetime
    finished_at: datetime | None
