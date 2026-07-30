from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.artifacts.types import ArtifactKind


class ArtifactResponse(BaseModel):
    id: UUID
    kind: ArtifactKind
    filename: str
    content_type: str
    size_bytes: int = Field(ge=0)
    frame: int | None
    created_at: datetime
    download_url: str


class FrameResponse(BaseModel):
    frame: int = Field(ge=1)
    filename: str
    size_bytes: int = Field(ge=0)
    original_artifact_id: UUID
    original_url: str
    preview_artifact_id: UUID | None
    preview_url: str | None


class FramePageResponse(BaseModel):
    items: list[FrameResponse]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=50)
    total: int = Field(ge=0)
    pages: int = Field(ge=0)


class LogTailResponse(BaseModel):
    lines: list[str]
