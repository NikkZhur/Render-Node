"""Coordinate validated uploads with the Job state machine."""

from uuid import UUID

from fastapi import UploadFile

from app.events.hub import EventHub
from app.jobs.exceptions import JobConflictError, JobNotFoundError
from app.jobs.locks import JobLocks
from app.jobs.models import Job
from app.jobs.repository import JobRepository
from app.jobs.state_machine import transition_job
from app.jobs.types import JobStatus
from app.storage.database import Database
from app.storage.jobs import JobStorage
from app.storage.uploads import UploadStorage


class JobUploadService:
    def __init__(
        self,
        database: Database,
        storage: UploadStorage,
        job_storage: JobStorage,
        locks: JobLocks,
        event_hub: EventHub,
    ) -> None:
        self._database = database
        self._storage = storage
        self._job_storage = job_storage
        self._locks = locks
        self._events = event_hub

    async def upload(self, job_id: UUID, file: UploadFile) -> Job:
        lock = await self._locks.get(job_id)
        async with lock:
            await self._require_created(job_id)
            stored = await self._storage.store(job_id, file)
            try:
                async with self._database.session_factory() as session, session.begin():
                    repository = JobRepository(session)
                    job = await repository.get(job_id)
                    if job is None:
                        raise JobNotFoundError
                    job.status = transition_job(job.status, JobStatus.READY)
                    job.source_filename = stored.source_filename
                    job.scene_path = stored.scene_path
                await self._events.publish(
                    "job.status_changed",
                    job_id=str(job.id),
                    status=job.status.value,
                    progress=job.progress,
                    current_frame=job.current_frame,
                )
                return job
            except Exception:
                await self._job_storage.delete_input(job_id)
                raise

    async def _require_created(self, job_id: UUID) -> None:
        async with self._database.session_factory() as session:
            job = await JobRepository(session).get(job_id)
            if job is None:
                raise JobNotFoundError
            if job.status is not JobStatus.CREATED:
                raise JobConflictError(
                    "job_already_uploaded", "Only created jobs can accept an upload"
                )
