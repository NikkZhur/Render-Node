from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.artifacts.models import Artifact
from app.artifacts.types import ArtifactKind


class ArtifactRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, artifact: Artifact) -> Artifact:
        self._session.add(artifact)
        await self._session.flush()
        return artifact

    async def get(self, artifact_id: UUID) -> Artifact | None:
        return await self._session.get(Artifact, artifact_id)

    async def get_by_kind_frame(
        self, job_id: UUID, kind: ArtifactKind, frame: int | None
    ) -> Artifact | None:
        statement = select(Artifact).where(
            Artifact.job_id == job_id,
            Artifact.kind == kind,
            Artifact.frame.is_(None) if frame is None else Artifact.frame == frame,
        )
        result: Artifact | None = await self._session.scalar(statement)
        return result

    async def list_for_job(
        self, job_id: UUID, *, kind: ArtifactKind | None = None
    ) -> list[Artifact]:
        statement = select(Artifact).where(Artifact.job_id == job_id)
        if kind is not None:
            statement = statement.where(Artifact.kind == kind)
        result = await self._session.scalars(statement.order_by(Artifact.created_at.asc()))
        return list(result)

    async def list_frames(
        self, job_id: UUID, kind: ArtifactKind, *, offset: int, limit: int
    ) -> list[Artifact]:
        result = await self._session.scalars(
            select(Artifact)
            .where(Artifact.job_id == job_id, Artifact.kind == kind, Artifact.frame.is_not(None))
            .order_by(Artifact.frame.asc(), Artifact.id.asc())
            .offset(offset)
            .limit(limit)
        )
        return list(result)

    async def count_frames(self, job_id: UUID, kind: ArtifactKind) -> int:
        total = await self._session.scalar(
            select(func.count())
            .select_from(Artifact)
            .where(Artifact.job_id == job_id, Artifact.kind == kind, Artifact.frame.is_not(None))
        )
        return total or 0

    async def list_by_frames(
        self, job_id: UUID, kind: ArtifactKind, frames: list[int]
    ) -> list[Artifact]:
        if not frames:
            return []
        result = await self._session.scalars(
            select(Artifact).where(
                Artifact.job_id == job_id, Artifact.kind == kind, Artifact.frame.in_(frames)
            )
        )
        return list(result)

    async def list_all_frames(self, job_id: UUID, kind: ArtifactKind) -> list[Artifact]:
        result = await self._session.scalars(
            select(Artifact)
            .where(Artifact.job_id == job_id, Artifact.kind == kind)
            .order_by(Artifact.frame.asc())
        )
        return list(result)

    async def delete(self, artifact: Artifact) -> None:
        await self._session.delete(artifact)

    async def delete_for_job(self, job_id: UUID) -> None:
        await self._session.execute(delete(Artifact).where(Artifact.job_id == job_id))
