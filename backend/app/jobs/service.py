"""Job application service; HTTP handlers contain no transition logic."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.artifacts.repository import ArtifactRepository
from app.artifacts.service import ArtifactService
from app.blender.repository import BlenderRepository
from app.events.hub import EventHub
from app.jobs.exceptions import JobConflictError, JobNotFoundError
from app.jobs.locks import JobLocks
from app.jobs.models import Job, utc_now
from app.jobs.repository import JobRepository
from app.jobs.schemas import JobCreate
from app.jobs.state_machine import transition_job
from app.jobs.types import JobStatus
from app.storage.database import Database
from app.storage.jobs import JobStorage

if TYPE_CHECKING:
    from app.jobs.manager import JobManager


class JobService:
    def __init__(
        self,
        database: Database,
        storage: JobStorage,
        locks: JobLocks,
        event_hub: EventHub,
        artifact_service: ArtifactService,
        manager: JobManager | None = None,
    ) -> None:
        self._database = database
        self._storage = storage
        self._locks = locks
        self._events = event_hub
        self._artifacts = artifact_service
        self._manager = manager

    async def create(self, payload: JobCreate) -> Job:
        values = payload.model_dump()
        storage_created = False
        try:
            async with self._database.session_factory() as session, session.begin():
                active_runtime = await BlenderRepository(session).active_runtime()
                if active_runtime is None:
                    raise JobConflictError(
                        "active_blender_missing", "No active Blender version is configured"
                    )
                values["blender_version"] = active_runtime.version
                job = Job(**values)
                await JobRepository(session).add(job)
                await self._storage.create_job(job.id)
                storage_created = True
        except Exception:
            if storage_created:
                await self._storage.delete_job(job.id)
            raise
        await self._events.publish("job.created", job_id=str(job.id), status=job.status.value)
        return job

    async def list(self) -> list[Job]:
        async with self._database.session_factory() as session:
            return await JobRepository(session).list()

    async def get(self, job_id: UUID) -> Job:
        async with self._database.session_factory() as session:
            job = await JobRepository(session).get(job_id)
            if job is None:
                raise JobNotFoundError
            return job

    async def delete(self, job_id: UUID) -> None:
        lock = await self._locks.get(job_id)
        async with lock:
            async with self._database.session_factory() as session, session.begin():
                repository = JobRepository(session)
                job = await self._require(repository, job_id)
                if job.status in {JobStatus.QUEUED, JobStatus.RENDERING}:
                    raise JobConflictError(
                        "active_job_cannot_be_deleted",
                        "Queued or rendering jobs cannot be deleted",
                    )
                await ArtifactRepository(session).delete_for_job(job_id)
                await repository.delete(job)
            await self._storage.delete_job(job_id)
        await self._locks.discard(job_id)
        self._artifacts.forget_job(job_id)
        await self._events.publish("job.deleted", job_id=str(job_id))

    async def start(self, job_id: UUID) -> Job:
        job = await self._transition(job_id, JobStatus.QUEUED)
        if self._manager is not None:
            self._manager.notify_queued()
        await self._publish_status(job)
        return job

    async def cancel(self, job_id: UUID) -> Job:
        if self._manager is not None:
            return await self._manager.cancel(job_id)
        lock = await self._locks.get(job_id)
        async with lock:
            async with self._database.session_factory() as session, session.begin():
                job = await self._require(JobRepository(session), job_id)
                job.status = transition_job(job.status, JobStatus.CANCELLED)
                job.finished_at = utc_now()
            await self._publish_status(job)
            return job

    async def retry(self, job_id: UUID) -> Job:
        lock = await self._locks.get(job_id)
        async with lock:
            async with self._database.session_factory() as session:
                job = await self._require(JobRepository(session), job_id)
                transition_job(job.status, JobStatus.QUEUED)
                active_runtime = await BlenderRepository(session).active_runtime()
                if active_runtime is None or active_runtime.version != job.blender_version:
                    raise JobConflictError(
                        "job_blender_not_active",
                        "The Blender version recorded for this job is not active",
                    )
                scene_path = job.scene_path
            if not await self._storage.scene_exists(job_id, scene_path):
                raise JobConflictError(
                    "job_scene_unavailable", "The uploaded Blender scene is unavailable"
                )
            await self._storage.reset_runtime(job_id)
            async with self._database.session_factory() as session, session.begin():
                job = await self._require(JobRepository(session), job_id)
                active_runtime = await BlenderRepository(session).active_runtime()
                if active_runtime is None or active_runtime.version != job.blender_version:
                    raise JobConflictError(
                        "job_blender_not_active",
                        "The Blender version recorded for this job is not active",
                    )
                await ArtifactRepository(session).delete_for_job(job_id)
                job.status = transition_job(job.status, JobStatus.QUEUED)
                job.current_frame = None
                job.progress = 0.0
                job.process_pid = None
                job.started_at = None
                job.finished_at = None
                job.exit_code = None
                job.error = None
            self._artifacts.forget_job(job_id)
            if self._manager is not None:
                self._manager.notify_queued()
            await self._publish_status(job)
            return job

    async def _transition(self, job_id: UUID, target: JobStatus) -> Job:
        lock = await self._locks.get(job_id)
        async with lock:
            async with self._database.session_factory() as session, session.begin():
                repository = JobRepository(session)
                job = await self._require(repository, job_id)
                next_status = transition_job(job.status, target)
                if target is JobStatus.QUEUED:
                    active_runtime = await BlenderRepository(session).active_runtime()
                    if active_runtime is None or active_runtime.version != job.blender_version:
                        raise JobConflictError(
                            "job_blender_not_active",
                            "The Blender version recorded for this job is not active",
                        )
                    if not await self._storage.scene_exists(job_id, job.scene_path):
                        raise JobConflictError(
                            "job_scene_unavailable", "The uploaded Blender scene is unavailable"
                        )
                job.status = next_status
            return job

    @staticmethod
    async def _require(repository: JobRepository, job_id: UUID) -> Job:
        job = await repository.get(job_id)
        if job is None:
            raise JobNotFoundError
        return job

    async def _publish_status(self, job: Job) -> None:
        await self._events.publish(
            "job.status_changed",
            job_id=str(job.id),
            status=job.status.value,
            progress=job.progress,
            current_frame=job.current_frame,
            exit_code=job.exit_code,
            error=job.error,
        )
