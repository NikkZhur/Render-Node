"""Single-process render scheduler and explicit Job subprocess lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from uuid import UUID

from app.artifacts.service import ArtifactService
from app.blender.command import BlenderExecutableResolver, build_render_command
from app.blender.devices import GpuDiscovery
from app.blender.progress import parse_progress
from app.blender.repository import BlenderRepository
from app.blender.runner import RunnerError, RunResult, SandboxRunner
from app.blender.sandbox import SandboxUnavailableError, build_worker_environment
from app.blender.types import RuntimeSource, RuntimeState
from app.events.hub import EventHub
from app.jobs.exceptions import JobConflictError, JobNotFoundError, ServiceError
from app.jobs.models import Job, utc_now
from app.jobs.repository import JobRepository
from app.jobs.state_machine import transition_job
from app.jobs.types import ComputeDevice, FrameMode, JobStatus
from app.storage.database import Database


class JobManager:
    def __init__(
        self,
        database: Database,
        runner: SandboxRunner,
        executable_resolver: BlenderExecutableResolver,
        gpu_discovery: GpuDiscovery,
        artifact_service: ArtifactService,
        event_hub: EventHub,
        *,
        jobs_root: Path,
        enabled: bool,
        poll_seconds: float,
    ) -> None:
        self._database = database
        self._runner = runner
        self._resolver = executable_resolver
        self._gpu_discovery = gpu_discovery
        self._artifacts = artifact_service
        self._events = event_hub
        self._jobs_root = jobs_root
        self._enabled = enabled
        self._poll_seconds = poll_seconds
        self._wake = asyncio.Event()
        self._state_lock = asyncio.Lock()
        self._scheduler_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._active_job_id: UUID | None = None
        self._active_cancellation: asyncio.Event | None = None
        self._active_finished: asyncio.Event | None = None

    async def start(self) -> None:
        await self._recover_interrupted()
        if self._enabled:
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def shutdown(self) -> None:
        self._stopping = True
        self._wake.set()
        async with self._state_lock:
            cancellation = self._active_cancellation
        if cancellation is not None:
            cancellation.set()
        if self._scheduler_task is not None:
            await self._scheduler_task
            self._scheduler_task = None

    def notify_queued(self) -> None:
        self._wake.set()

    @property
    def runner_available(self) -> bool:
        return self._enabled and self._runner.available

    @property
    def runner_mode(self) -> str:
        return self._runner.mode

    @property
    def runner_unavailable_reason(self) -> str | None:
        if not self._enabled:
            return "Render scheduler is disabled"
        return self._runner.unavailable_reason

    def ensure_accepting_jobs(self) -> None:
        reason = self.runner_unavailable_reason
        if reason is not None:
            raise JobConflictError("runner_unavailable", reason)

    async def cancel(self, job_id: UUID) -> Job:
        async with self._state_lock:
            is_active = self._active_job_id == job_id
            cancellation = self._active_cancellation if is_active else None
            finished = self._active_finished if is_active else None
            if cancellation is not None:
                cancellation.set()
        if finished is not None:
            await finished.wait()
            return await self._get_job(job_id)

        async with self._database.session_factory() as session, session.begin():
            repository = JobRepository(session)
            job = await repository.get(job_id)
            if job is None:
                raise JobNotFoundError
            if job.status is JobStatus.QUEUED:
                job.status = transition_job(job.status, JobStatus.CANCELLED)
                job.finished_at = utc_now()
            elif job.status is JobStatus.RENDERING:
                raise JobConflictError(
                    "render_process_not_owned",
                    "Rendering process is not owned by this Job Manager",
                )
            else:
                job.status = transition_job(job.status, JobStatus.CANCELLED)
                job.finished_at = utc_now()
        await self._publish_status(job)
        return job

    async def wait_for_terminal(self, job_id: UUID, *, max_wait_seconds: float = 10) -> Job:
        async with asyncio.timeout(max_wait_seconds):
            while True:
                job = await self._get_job(job_id)
                if job.status in {
                    JobStatus.COMPLETED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                }:
                    return job
                await asyncio.sleep(0.05)

    async def _scheduler_loop(self) -> None:
        while not self._stopping:
            job_id = await self._oldest_queued_id()
            if job_id is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self._poll_seconds)
                except TimeoutError:
                    pass
                continue
            await self._execute(job_id)

    async def _execute(self, job_id: UUID) -> None:
        cancellation = asyncio.Event()
        finished = asyncio.Event()
        async with self._state_lock:
            if self._active_job_id is not None:
                raise RuntimeError("Job Manager attempted to run two jobs")
            self._active_job_id = job_id
            self._active_cancellation = cancellation
            self._active_finished = finished
        try:
            job, source = await self._prepare(job_id)
            binary = self._resolver.binary(job.blender_version, source)
            command = build_render_command(job, binary=binary, jobs_root=self._jobs_root)
            await self._validate_gpu_request(job)
            environment = await build_worker_environment(
                command.job_directory,
                device=job.device,
                gpu_ids=job.gpu_ids,
            )
            await self._mark_rendering(job_id)

            async def on_started(pid: int) -> None:
                await self._set_pid(job_id, pid)

            async def on_line(line: str) -> None:
                await self._record_progress(job_id, job, line)

            async def on_output(path: Path) -> None:
                await self._artifacts.register_output(job_id, path)

            result = await self._runner.run(
                command,
                environment=environment,
                cancellation=cancellation,
                on_started=on_started,
                on_line=on_line,
                on_output=on_output,
            )
            await self._finish_from_result(job_id, result)
        except (
            RunnerError,
            SandboxUnavailableError,
            ServiceError,
            ValueError,
            RuntimeError,
        ) as exc:
            await self._fail(job_id, str(exc))
        except Exception:
            await self._fail(job_id, "Unexpected render worker failure")
        finally:
            with contextlib.suppress(Exception):
                await self._artifacts.register_log(job_id)
            async with self._state_lock:
                self._active_job_id = None
                self._active_cancellation = None
                self._active_finished = None
                finished.set()
            self._wake.set()

    async def _prepare(self, job_id: UUID) -> tuple[Job, RuntimeSource]:
        async with self._database.session_factory() as session:
            job = await JobRepository(session).get(job_id)
            if job is None:
                raise JobNotFoundError
            if job.status is not JobStatus.QUEUED:
                raise JobConflictError(
                    "job_not_queued", "Scheduler selected a job that is no longer queued"
                )
            runtime = await BlenderRepository(session).get_runtime(job.blender_version)
            if runtime is None or runtime.state is not RuntimeState.INSTALLED:
                raise RuntimeError("Job Blender version is not installed")
            return job, runtime.source

    async def _validate_gpu_request(self, job: Job) -> None:
        if job.device is ComputeDevice.CPU:
            return
        devices = await asyncio.to_thread(self._gpu_discovery.discover)
        available = {device.id for device in devices}
        if not set(job.gpu_ids).issubset(available):
            raise RuntimeError("Requested GPU is unavailable")

    async def _mark_rendering(self, job_id: UUID) -> None:
        async with self._database.session_factory() as session, session.begin():
            job = await self._require_job(JobRepository(session), job_id)
            job.status = transition_job(job.status, JobStatus.RENDERING)
            job.started_at = utc_now()
            job.finished_at = None
            job.error = None
        await self._publish_status(job)

    async def _set_pid(self, job_id: UUID, pid: int) -> None:
        async with self._database.session_factory() as session, session.begin():
            job = await self._require_job(JobRepository(session), job_id)
            job.process_pid = pid

    async def _record_progress(self, job_id: UUID, original_job: Job, line: str) -> None:
        normalized_line = "".join(
            character for character in line if character == "\t" or ord(character) >= 32
        )[:4000]
        await self._events.publish(
            "render.log", job_id=str(job_id), stream="combined", line=normalized_line
        )
        try:
            update = parse_progress(line)
        except Exception:
            return
        if update is None:
            return
        progress = self._overall_progress(original_job, update.frame, update.frame_progress)
        async with self._database.session_factory() as session, session.begin():
            job = await self._require_job(JobRepository(session), job_id)
            if job.status is not JobStatus.RENDERING:
                return
            if update.frame is not None:
                job.current_frame = update.frame
            if progress is not None:
                job.progress = max(job.progress, min(progress, 1.0))
            persisted_progress = job.progress
            current_frame = job.current_frame
        await self._events.publish(
            "render.progress",
            job_id=str(job_id),
            frame=current_frame,
            sample=update.sample,
            total_samples=update.total_samples,
            progress=persisted_progress,
        )

    @staticmethod
    def _overall_progress(
        job: Job, frame: int | None, frame_progress: float | None
    ) -> float | None:
        if frame_progress is None:
            return None
        if job.frame_mode is FrameMode.RANGE and job.frame_start is not None and job.frame_end:
            current = frame if frame is not None else job.frame_start
            total = job.frame_end - job.frame_start + 1
            offset = min(max(current - job.frame_start, 0), total - 1)
            return (offset + frame_progress) / total
        return frame_progress

    async def _finish_from_result(self, job_id: UUID, result: RunResult) -> None:
        async with self._database.session_factory() as session, session.begin():
            job = await self._require_job(JobRepository(session), job_id)
            if self._stopping and result.cancelled:
                target = JobStatus.FAILED
                error = "Render was interrupted by service shutdown"
            elif result.cancelled:
                target = JobStatus.CANCELLED
                error = None
            elif result.timed_out:
                target = JobStatus.FAILED
                error = "Render exceeded its wall-time limit"
            elif result.limit_exceeded:
                target = JobStatus.FAILED
                error = "Render exceeded its output or log size limit"
            elif result.exit_code == 0:
                target = JobStatus.COMPLETED
                error = None
            else:
                target = JobStatus.FAILED
                error = f"Blender exited with code {result.exit_code}"
            job.status = transition_job(job.status, target)
            job.progress = 1.0 if target is JobStatus.COMPLETED else job.progress
            job.process_pid = None
            job.exit_code = result.exit_code
            job.error = error
            job.finished_at = utc_now()
        await self._publish_status(job)

    async def _fail(self, job_id: UUID, message: str) -> None:
        async with self._database.session_factory() as session, session.begin():
            job = await JobRepository(session).get(job_id)
            if job is None or job.status in {
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }:
                return
            job.status = transition_job(job.status, JobStatus.FAILED)
            job.process_pid = None
            job.error = message[:2000]
            job.finished_at = utc_now()
        await self._publish_status(job)

    async def _recover_interrupted(self) -> None:
        async with self._database.session_factory() as session, session.begin():
            repository = JobRepository(session)
            for job in await repository.list_by_status(JobStatus.RENDERING):
                job.status = transition_job(job.status, JobStatus.FAILED)
                job.process_pid = None
                job.error = "Render was interrupted by a service restart"
                job.finished_at = utc_now()

    async def _oldest_queued_id(self) -> UUID | None:
        async with self._database.session_factory() as session:
            job = await JobRepository(session).oldest_queued()
            return job.id if job is not None else None

    async def _get_job(self, job_id: UUID) -> Job:
        async with self._database.session_factory() as session:
            return await self._require_job(JobRepository(session), job_id)

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

    @staticmethod
    async def _require_job(repository: JobRepository, job_id: UUID) -> Job:
        job = await repository.get(job_id)
        if job is None:
            raise JobNotFoundError
        return job
