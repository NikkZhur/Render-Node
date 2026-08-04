"""SQLAlchemy repository for Job persistence."""

from __future__ import annotations

import builtins
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.models import Job
from app.jobs.types import JobStatus


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, job: Job) -> Job:
        self._session.add(job)
        await self._session.flush()
        return job

    async def get(self, job_id: UUID) -> Job | None:
        return await self._session.get(Job, job_id)

    async def list(self) -> list[Job]:
        result = await self._session.scalars(select(Job).order_by(Job.created_at.desc()))
        return list(result)

    async def page(self, *, offset: int, limit: int) -> tuple[builtins.list[Job], int]:
        total = int(await self._session.scalar(select(func.count()).select_from(Job)) or 0)
        result = await self._session.scalars(
            select(Job).order_by(Job.created_at.desc()).offset(offset).limit(limit)
        )
        return list(result), total

    async def oldest_queued(self) -> Job | None:
        job: Job | None = await self._session.scalar(
            select(Job)
            .where(Job.status == JobStatus.QUEUED)
            .order_by(Job.created_at.asc())
            .limit(1)
        )
        return job

    async def list_by_status(self, status: JobStatus) -> builtins.list[Job]:
        result = await self._session.scalars(
            select(Job).where(Job.status == status).order_by(Job.created_at.asc())
        )
        return list(result)

    async def delete(self, job: Job) -> None:
        await self._session.delete(job)

    async def active_count(self, *, blender_version: str | None = None) -> int:
        statement = (
            select(func.count())
            .select_from(Job)
            .where(Job.status.in_([JobStatus.QUEUED, JobStatus.RENDERING]))
        )
        if blender_version is not None:
            statement = statement.where(Job.blender_version == blender_version)
        return int(await self._session.scalar(statement) or 0)

    async def reference_count(self, blender_version: str) -> int:
        statement = (
            select(func.count()).select_from(Job).where(Job.blender_version == blender_version)
        )
        return int(await self._session.scalar(statement) or 0)
