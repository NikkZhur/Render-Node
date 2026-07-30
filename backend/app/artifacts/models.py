"""Persistent metadata for files created by a render job."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.artifacts.types import ArtifactKind
from app.jobs.models import UTCDateTime, enum_column, utc_now
from app.storage.database import Base


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("job_id", "kind", "frame", name="uq_artifacts_job_kind_frame"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[ArtifactKind] = mapped_column(
        enum_column(ArtifactKind, "artifact_kind"), index=True
    )
    relative_path: Mapped[str] = mapped_column(String(1024))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    frame: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
