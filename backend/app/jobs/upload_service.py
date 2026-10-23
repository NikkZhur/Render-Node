"""Coordinate validated uploads with the Job state machine."""

from uuid import UUID, uuid4

from fastapi import UploadFile

from app.events.hub import EventHub
from app.jobs.analysis import SceneAnalysisService
from app.jobs.exceptions import JobConflictError, JobNotFoundError
from app.jobs.locks import JobLocks
from app.jobs.models import Job
from app.jobs.repository import JobRepository
from app.jobs.state_machine import transition_job
from app.jobs.types import ANALYSIS_ACTIVE, AnalysisStatus, JobStatus
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
        analysis: SceneAnalysisService | None = None,
    ) -> None:
        self._database = database
        self._storage = storage
        self._job_storage = job_storage
        self._locks = locks
        self._events = event_hub
        self._analysis = analysis

    async def recover_incomplete_uploads(self) -> None:
        async with self._database.session_factory() as session:
            jobs = await JobRepository(session).list_by_status(JobStatus.CREATED)
        for job in jobs:
            await self._job_storage.delete_input(job.id)

    async def upload(self, job_id: UUID, file: UploadFile, *, auto_configure: bool = False) -> Job:
        lock = await self._locks.get(job_id)
        async with lock:
            previous = await self._require_editable(job_id)
            if self._analysis is not None:
                await self._analysis.cancel(job_id)
            try:
                stored = await self._storage.store(job_id, file)
            except Exception:
                # Validation leaves the old input intact. Resume its analysis if
                # replacement was rejected after cancelling the previous reader.
                if (
                    self._analysis is not None
                    and previous.analysis_status in ANALYSIS_ACTIVE
                    and previous.analysis_id is not None
                ):
                    self._analysis.schedule(job_id, previous.analysis_id)
                raise
            try:
                async with self._database.session_factory() as session, session.begin():
                    repository = JobRepository(session)
                    job = await repository.get(job_id)
                    if job is None:
                        raise JobNotFoundError
                    if job.status is JobStatus.CREATED:
                        job.status = transition_job(job.status, JobStatus.READY)
                    job.source_filename = stored.source_filename
                    job.scene_path = stored.scene_path
                    job.analysis_id = uuid4() if auto_configure else None
                    job.analysis_status = (
                        AnalysisStatus.PENDING if auto_configure else AnalysisStatus.NONE
                    )
                    job.analysis_error = None
                    job.scene_metadata = None
                    job.auto_configured = False
                    if auto_configure and self._analysis is None:
                        job.analysis_status = AnalysisStatus.FAILED
                        job.analysis_error = (
                            "Scene analysis is unavailable; adjust settings manually"
                        )
                if auto_configure and self._analysis is not None and job.analysis_id is not None:
                    self._analysis.schedule(job_id, job.analysis_id)
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

    async def _require_editable(self, job_id: UUID) -> Job:
        async with self._database.session_factory() as session:
            job = await JobRepository(session).get(job_id)
            if job is None:
                raise JobNotFoundError
            if job.status not in {JobStatus.CREATED, JobStatus.READY}:
                raise JobConflictError(
                    "job_upload_locked", "A scene cannot be replaced after rendering has started"
                )
            return job
