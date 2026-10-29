"""Own automatic scene setup tasks, with persistence and per-job serialization."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy import select, update

from app.blender.command import BlenderExecutableResolver
from app.blender.inspection import InspectionError, SceneInspector
from app.blender.repository import BlenderRepository
from app.blender.types import RuntimeState
from app.events.hub import EventHub
from app.jobs.exceptions import JobConflictError
from app.jobs.locks import JobLocks
from app.jobs.models import Job
from app.jobs.repository import JobRepository
from app.jobs.types import ANALYSIS_ACTIVE, AnalysisStatus, JobStatus
from app.storage.database import Database
from app.storage.jobs import JobStorage

logger = logging.getLogger(__name__)


class SceneAnalysisService:
    def __init__(
        self,
        database: Database,
        storage: JobStorage,
        locks: JobLocks,
        events: EventHub,
        inspector: SceneInspector,
        resolver: BlenderExecutableResolver,
    ) -> None:
        self._database = database
        self._storage = storage
        self._locks = locks
        self._events = events
        self._inspector = inspector
        self._resolver = resolver
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._slot = asyncio.Semaphore(1)

    async def initialize(self) -> None:
        await self._inspector.initialize()
        async with self._database.session_factory() as session, session.begin():
            await session.execute(
                update(Job)
                .where(Job.analysis_status.in_(ANALYSIS_ACTIVE))
                .values(
                    analysis_status=AnalysisStatus.FAILED,
                    analysis_error=(
                        "Scene analysis was interrupted by a restart. "
                        "Adjust settings manually or upload again."
                    ),
                    auto_configured=False,
                )
            )

    def schedule(self, job_id: UUID, analysis_id: UUID) -> None:
        task = asyncio.create_task(self._analyze(job_id, analysis_id))
        self._tasks[job_id] = task

        def finished(completed: asyncio.Task[None]) -> None:
            error = None if completed.cancelled() else completed.exception()
            stop_failed = completed.cancelling() > 0 and error is not None
            # Retain failed cancellation so a repeated request cannot mutate
            # input whose reader was not confirmed to have stopped safely.
            if self._tasks.get(job_id) is completed and not stop_failed:
                self._tasks.pop(job_id, None)
            if error is not None:
                logger.error("Scene analysis task failed for job %s", job_id, exc_info=error)

        task.add_done_callback(finished)

    async def cancel(self, job_id: UUID) -> None:
        # Call under the shared job lock. Failed cleanup must propagate without
        # attempting to reacquire that lock or allowing the caller to alter input.
        task = self._tasks.get(job_id)
        if task is not None:
            task.cancel()
            (result,) = await asyncio.gather(task, return_exceptions=True)
            if isinstance(result, Exception):
                raise JobConflictError(
                    "scene_analysis_stop_failed",
                    "Scene analysis cleanup failed; the original input was preserved. "
                    "Check the service logs and restart the service before retrying.",
                ) from result

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _analyze(self, job_id: UUID, analysis_id: UUID) -> None:
        lock = await self._locks.get(job_id)
        try:
            async with self._slot:
                async with lock:
                    async with self._database.session_factory() as session, session.begin():
                        job = await JobRepository(session).get(job_id)
                        if job is None or not self._current(job, analysis_id):
                            return
                        runtime = await BlenderRepository(session).get_runtime(job.blender_version)
                        if runtime is None or runtime.state is not RuntimeState.INSTALLED:
                            raise InspectionError("The job's Blender version is not installed")
                        binary = self._resolver.binary(runtime.version, runtime.source)
                        scene_path, name = job.scene_path, job.name
                        job.analysis_status = AnalysisStatus.RUNNING
                await self._publish(job_id)
                if scene_path is None:
                    raise InspectionError("The uploaded scene is unavailable")
                scene, configuration = await self._inspector.inspect(
                    binary=binary,
                    job_directory=self._storage.job_directory(job_id),
                    scene_path=scene_path,
                    name=name,
                )
                async with lock:
                    async with self._database.session_factory() as session, session.begin():
                        job = await JobRepository(session).get(job_id)
                        if job is None or not self._current(job, analysis_id):
                            return
                        for key, value in configuration.model_dump().items():
                            setattr(job, key, value)
                        job.scene_metadata = scene.model_dump()
                        job.analysis_status = AnalysisStatus.COMPLETED
                        job.analysis_error = None
                        job.auto_configured = True
                await self._publish(job_id)
        except Exception as exc:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                # Runner cleanup can replace CancelledError with its own error.
                # The cancelling upload/delete already holds the shared lock.
                raise
            message = (
                str(exc)
                if isinstance(exc, InspectionError)
                else "Scene analysis failed; adjust settings manually or upload again"
            )
            if not isinstance(exc, InspectionError):
                logger.exception("Scene inspection failed for job %s", job_id)
            async with lock:
                async with self._database.session_factory() as session, session.begin():
                    job = await session.scalar(select(Job).where(Job.id == job_id))
                    if job is None or not self._current(job, analysis_id):
                        return
                    job.analysis_status = AnalysisStatus.FAILED
                    job.analysis_error = message
                    job.auto_configured = False
            await self._publish(job_id)

    @staticmethod
    def _current(job: Job | None, analysis_id: UUID) -> bool:
        return bool(
            job is not None
            and job.analysis_id == analysis_id
            and job.analysis_status in ANALYSIS_ACTIVE
            and job.status is JobStatus.READY
        )

    async def _publish(self, job_id: UUID) -> None:
        await self._events.publish("job.analysis_updated", job_id=str(job_id))
