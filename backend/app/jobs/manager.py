"""Single-process render scheduler and explicit Job subprocess lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import aiofiles

from app.artifacts.service import ArtifactService, frame_number_from_output
from app.blender.cgroups import ResourceIsolationStatus
from app.blender.command import BlenderExecutableResolver, FrameWorker, build_render_command
from app.blender.devices import GpuAllocation, GpuReservationError, GpuScheduler
from app.blender.progress import FramePlan, parse_frame_plan, parse_progress
from app.blender.repository import BlenderRepository
from app.blender.runner import (
    FailureKind,
    RunnerError,
    RunResult,
    SandboxRunner,
    WorkerRunResult,
    WorkerRunSpec,
)
from app.blender.sandbox import build_worker_environment
from app.blender.types import RuntimeSource, RuntimeState
from app.events.hub import EventHub
from app.jobs.exceptions import JobConflictError, JobNotFoundError, ServiceError
from app.jobs.models import Job, utc_now
from app.jobs.repository import JobRepository
from app.jobs.state_machine import transition_job
from app.jobs.types import ComputeDevice, FrameMode, GpuRenderMode, JobStatus
from app.storage.database import Database


@dataclass(slots=True)
class _WorkerProgress:
    assigned_frames: int | None = None
    completed_frames: set[int] = field(default_factory=set)
    active_frame: int | None = None
    frame_progress: float = 0.0

    def record_frame(self, frame_number: int, frame_progress: float) -> None:
        if self.active_frame is not None and self.active_frame != frame_number:
            self.completed_frames.add(self.active_frame)
            self.frame_progress = 0.0
        self.active_frame = frame_number
        if frame_number not in self.completed_frames:
            self.frame_progress = max(self.frame_progress, frame_progress)

    def complete_frame(self, frame_number: int) -> None:
        self.completed_frames.add(frame_number)
        if self.active_frame == frame_number:
            self.active_frame = None
            self.frame_progress = 0.0

    def overall(self) -> float | None:
        if not self.assigned_frames:
            return None
        completed = min(len(self.completed_frames), self.assigned_frames)
        partial = (
            self.frame_progress
            if self.active_frame is not None and self.active_frame not in self.completed_frames
            else 0.0
        )
        return min((completed + partial) / self.assigned_frames, 1.0)


@dataclass(slots=True)
class _DistributedProgress:
    worker_ids: tuple[str, ...]
    total_frames: int | None = None
    completed_frames: set[int] = field(default_factory=set)
    active_frames: dict[str, tuple[int, float]] = field(default_factory=dict)
    workers: dict[str, _WorkerProgress] = field(default_factory=dict)
    frame_plans: dict[str, FramePlan] = field(default_factory=dict)
    registered_outputs: set[Path] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def worker_for_frame(self, frame_number: int) -> str | None:
        if len(self.worker_ids) == 1:
            return self.worker_ids[0]
        worker_count = len(self.worker_ids)
        for worker_id, plan in self.frame_plans.items():
            first_frame = plan.start + plan.worker * plan.step
            worker_step = plan.step * worker_count
            if (
                worker_step > 0
                and first_frame <= frame_number <= plan.end
                and (frame_number - first_frame) % worker_step == 0
            ):
                return worker_id
        return None

    def overall(self) -> float | None:
        if not self.total_frames:
            return None
        partial_by_frame: dict[int, float] = {}
        for frame_number, frame_progress in self.active_frames.values():
            if frame_number not in self.completed_frames:
                partial_by_frame[frame_number] = max(
                    partial_by_frame.get(frame_number, 0.0), frame_progress
                )
        completed = min(len(self.completed_frames), self.total_frames)
        return min((completed + sum(partial_by_frame.values())) / self.total_frames, 1.0)


class _AggregateLog:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()

    async def reset(self) -> None:
        async with aiofiles.open(self._path, "wb"):
            pass

    async def append(self, worker_id: str, line: str) -> None:
        async with self._lock, aiofiles.open(self._path, "ab") as destination:
            await destination.write(f"[GPU {worker_id}] {line}\n".encode())


class JobManager:
    def __init__(
        self,
        database: Database,
        runner: SandboxRunner,
        executable_resolver: BlenderExecutableResolver,
        gpu_scheduler: GpuScheduler,
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
        self._gpu_scheduler = gpu_scheduler
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
        await self._runner.initialize()
        await self._gpu_scheduler.initialize()
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

    @property
    def resource_isolation_status(self) -> ResourceIsolationStatus:
        return self._runner.isolation_status

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
        gpu_allocation: GpuAllocation | None = None
        async with self._state_lock:
            if self._active_job_id is not None:
                raise RuntimeError("Job Manager attempted to run two jobs")
            self._active_job_id = job_id
            self._active_cancellation = cancellation
            self._active_finished = finished
        try:
            job, source = await self._prepare(job_id)
            binary = self._resolver.binary(job.blender_version, source)
            if job.device is not ComputeDevice.CPU:
                gpu_allocation = await self._gpu_scheduler.reserve(job.gpu_ids)
            await self._mark_rendering(job_id)
            if job.gpu_render_mode is GpuRenderMode.FRAME_PER_GPU:
                if gpu_allocation is None:
                    raise RuntimeError("FRAME_PER_GPU job has no reserved GPU allocation")
                results = await self._run_frame_workers(
                    job,
                    binary=binary,
                    gpu_allocation=gpu_allocation,
                    cancellation=cancellation,
                )
                await self._reconcile_outputs(job_id)
                await self._finish_from_worker_results(
                    job_id,
                    results,
                    cancellation_requested=cancellation.is_set(),
                )
            else:
                command = build_render_command(job, binary=binary, jobs_root=self._jobs_root)
                gpu_uuids = list(gpu_allocation.uuids) if gpu_allocation is not None else []
                environment = await build_worker_environment(
                    command.job_directory,
                    device=job.device,
                    gpu_uuids=gpu_uuids,
                )

                async def on_started(pid: int) -> None:
                    await self._set_pid(job_id, pid)

                async def on_line(line: str) -> None:
                    await self._record_progress(job_id, job, line, gpu_uuids=gpu_uuids)

                async def on_output(path: Path) -> None:
                    await self._artifacts.register_output(job_id, path)

                result = await self._runner.run(
                    command,
                    job_id=job_id,
                    environment=environment,
                    cancellation=cancellation,
                    on_started=on_started,
                    on_line=on_line,
                    on_output=on_output,
                )
                await self._reconcile_outputs(job_id)
                await self._finish_from_result(job_id, result)
        except (
            RunnerError,
            GpuReservationError,
            ServiceError,
            ValueError,
            RuntimeError,
        ) as exc:
            await self._fail(job_id, str(exc))
        except Exception:
            await self._fail(job_id, "Unexpected render worker failure")
        finally:
            cleanup_cancellation: asyncio.CancelledError | None = None
            if gpu_allocation is not None:
                release_task = asyncio.create_task(self._gpu_scheduler.release(gpu_allocation))
                try:
                    await asyncio.shield(release_task)
                except asyncio.CancelledError as exc:
                    await release_task
                    cleanup_cancellation = exc
            with contextlib.suppress(Exception):
                await self._artifacts.register_log(job_id)
            async with self._state_lock:
                self._active_job_id = None
                self._active_cancellation = None
                self._active_finished = None
                finished.set()
            self._wake.set()
            if cleanup_cancellation is not None:
                raise cleanup_cancellation

    async def _reconcile_outputs(self, job_id: UUID) -> None:
        # Artifact discovery remains best-effort: a preview/indexing problem must
        # not change a successful Blender exit into a failed render.
        with contextlib.suppress(Exception):
            await self._artifacts.reconcile_outputs(job_id)

    async def _run_frame_workers(
        self,
        job: Job,
        *,
        binary: Path,
        gpu_allocation: GpuAllocation,
        cancellation: asyncio.Event,
    ) -> tuple[WorkerRunResult, ...]:
        worker_count = self._frame_worker_count(job, len(gpu_allocation.devices))
        worker_ids = tuple(device.uuid for device in gpu_allocation.devices[:worker_count])
        tracker = _DistributedProgress(
            worker_ids=worker_ids,
            total_frames=1 if job.frame_mode is FrameMode.SINGLE else None,
        )
        aggregate_log = _AggregateLog(self._jobs_root / str(job.id) / "logs" / "blender.log")
        await aggregate_log.reset()
        workers: list[WorkerRunSpec] = []
        for worker_index, device in enumerate(gpu_allocation.devices[:worker_count]):
            command = build_render_command(
                job,
                binary=binary,
                jobs_root=self._jobs_root,
                worker=FrameWorker(index=worker_index, count=worker_count),
            )
            environment = await build_worker_environment(
                command.job_directory,
                device=job.device,
                gpu_uuids=[device.uuid],
                worker_index=worker_index,
            )

            async def on_started(pid: int) -> None:
                await self._set_pid(job.id, pid)

            async def on_line(
                line: str,
                *,
                worker_id: str = device.uuid,
                index: int = worker_index,
            ) -> None:
                await self._record_distributed_progress(
                    job.id,
                    tracker,
                    aggregate_log,
                    worker_id=worker_id,
                    worker_index=index,
                    line=line,
                )

            async def on_output(path: Path) -> None:
                await self._record_distributed_output(
                    job.id,
                    tracker,
                    path,
                )

            workers.append(
                WorkerRunSpec(
                    worker_id=device.uuid,
                    command=command,
                    environment=environment,
                    on_started=on_started,
                    on_line=on_line,
                    on_output=on_output,
                )
            )
        return await self._runner.run_workers(
            tuple(workers),
            job_id=job.id,
            cancellation=cancellation,
        )

    @staticmethod
    def _frame_worker_count(job: Job, gpu_count: int) -> int:
        if gpu_count < 1:
            raise RuntimeError("FRAME_PER_GPU job has no available GPUs")
        if job.frame_mode is FrameMode.SINGLE:
            return 1
        if job.frame_mode is FrameMode.RANGE:
            if job.frame_start is None or job.frame_end is None:
                raise RuntimeError("Frame-range job is incomplete")
            return min(gpu_count, job.frame_end - job.frame_start + 1)
        return gpu_count

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
            if job.process_pid is None:
                job.process_pid = pid

    async def _record_progress(
        self,
        job_id: UUID,
        original_job: Job,
        line: str,
        *,
        gpu_uuids: list[str],
    ) -> None:
        normalized_line = "".join(
            character for character in line if character == "\t" or ord(character) >= 32
        )[:4000]
        visible_line = (
            f"[GPUs {','.join(gpu_uuids)}] {normalized_line}" if gpu_uuids else normalized_line
        )
        await self._events.publish(
            "render.log",
            job_id=str(job_id),
            stream="combined",
            line=visible_line,
            gpu_uuids=gpu_uuids,
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

    async def _record_distributed_progress(
        self,
        job_id: UUID,
        tracker: _DistributedProgress,
        aggregate_log: _AggregateLog,
        *,
        worker_id: str,
        worker_index: int,
        line: str,
    ) -> None:
        normalized_line = "".join(
            character for character in line if character == "\t" or ord(character) >= 32
        )[:4000]
        await aggregate_log.append(worker_id, normalized_line)
        await self._events.publish(
            "render.log",
            job_id=str(job_id),
            stream="combined",
            line=f"[GPU {worker_id}] {normalized_line}",
            gpu_uuid=worker_id,
        )
        plan = parse_frame_plan(line)
        if plan is not None:
            if plan.worker != worker_index:
                raise RuntimeError(f"Frame plan worker mismatch for GPU {worker_id}: {plan.worker}")
            async with tracker.lock:
                if tracker.total_frames is not None and tracker.total_frames != plan.total:
                    raise RuntimeError("GPU workers reported inconsistent frame ranges")
                reference_plan = next(iter(tracker.frame_plans.values()), None)
                if reference_plan is not None and (
                    plan.start,
                    plan.end,
                    plan.step,
                ) != (
                    reference_plan.start,
                    reference_plan.end,
                    reference_plan.step,
                ):
                    raise RuntimeError("GPU workers reported inconsistent frame plans")
                tracker.total_frames = plan.total
                worker = tracker.workers.setdefault(worker_id, _WorkerProgress())
                if worker.assigned_frames is not None and worker.assigned_frames != plan.assigned:
                    raise RuntimeError(f"GPU {worker_id} reported an inconsistent frame plan")
                worker.assigned_frames = plan.assigned
                tracker.frame_plans[worker_id] = plan
            return
        try:
            update = parse_progress(line)
        except Exception:
            return
        if update is None:
            return

        async with tracker.lock:
            worker = tracker.workers.setdefault(worker_id, _WorkerProgress())
            if update.frame is not None:
                previous = tracker.active_frames.get(worker_id)
                if previous is not None and previous[0] != update.frame:
                    tracker.completed_frames.add(previous[0])
                previous_progress = (
                    previous[1] if previous is not None and previous[0] == update.frame else 0.0
                )
                tracker.active_frames[worker_id] = (
                    update.frame,
                    max(previous_progress, update.frame_progress or 0.0),
                )
                worker.record_frame(update.frame, update.frame_progress or 0.0)
            overall = tracker.overall()
            worker_progress = worker.overall()
            persisted_progress, current_frame, _ = await self._persist_progress(
                job_id,
                frame=update.frame,
                progress=overall,
            )
        await self._events.publish(
            "render.progress",
            job_id=str(job_id),
            gpu_uuid=worker_id,
            frame=update.frame if update.frame is not None else current_frame,
            sample=update.sample,
            total_samples=update.total_samples,
            progress=persisted_progress,
            worker_progress=worker_progress,
        )

    async def _record_distributed_output(
        self,
        job_id: UUID,
        tracker: _DistributedProgress,
        path: Path,
    ) -> None:
        frame_number = frame_number_from_output(path)
        if frame_number is None:
            return
        async with tracker.lock:
            if path in tracker.registered_outputs:
                return
            tracker.registered_outputs.add(path)
            tracker.completed_frames.add(frame_number)
            worker_id = tracker.worker_for_frame(frame_number)
            worker_progress = None
            if worker_id is not None:
                worker = tracker.workers.setdefault(worker_id, _WorkerProgress())
                worker.complete_frame(frame_number)
                worker_progress = worker.overall()
            tracker.active_frames = {
                active_worker_id: active
                for active_worker_id, active in tracker.active_frames.items()
                if active[0] != frame_number
            }
            overall = tracker.overall()
            persisted_progress, current_frame, changed = await self._persist_progress(
                job_id,
                frame=frame_number,
                progress=overall,
            )
        await self._artifacts.register_output(job_id, path)
        if changed:
            event: dict[str, object] = {
                "job_id": str(job_id),
                "frame": current_frame,
                "sample": None,
                "total_samples": None,
                "progress": persisted_progress,
            }
            if worker_id is not None and worker_progress is not None:
                event["gpu_uuid"] = worker_id
                event["worker_progress"] = worker_progress
            await self._events.publish("render.progress", **event)

    async def _persist_progress(
        self,
        job_id: UUID,
        *,
        frame: int | None,
        progress: float | None,
    ) -> tuple[float, int | None, bool]:
        async with self._database.session_factory() as session, session.begin():
            job = await self._require_job(JobRepository(session), job_id)
            if job.status is not JobStatus.RENDERING:
                return job.progress, job.current_frame, False
            before = job.progress
            if frame is not None:
                job.current_frame = frame
            if progress is not None:
                job.progress = max(job.progress, min(progress, 1.0))
            return job.progress, job.current_frame, job.progress > before

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
            elif result.failure_kind is not None:
                target = JobStatus.FAILED
                error = self._failure_message(result)
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

    async def _finish_from_worker_results(
        self,
        job_id: UUID,
        results: tuple[WorkerRunResult, ...],
        *,
        cancellation_requested: bool,
    ) -> None:
        failed = next(
            (
                worker
                for worker in results
                if not worker.result.cancelled
                and (
                    worker.result.timed_out
                    or worker.result.limit_exceeded
                    or worker.result.failure_kind is not None
                    or worker.result.exit_code != 0
                )
            ),
            None,
        )
        async with self._database.session_factory() as session, session.begin():
            job = await self._require_job(JobRepository(session), job_id)
            if self._stopping and cancellation_requested:
                target = JobStatus.FAILED
                exit_code = next(
                    (worker.result.exit_code for worker in results if worker.result.exit_code != 0),
                    None,
                )
                error = "Render was interrupted by service shutdown"
            elif failed is not None:
                target = JobStatus.FAILED
                exit_code = failed.result.exit_code
                error = self._worker_failure_message(failed)
            elif cancellation_requested or any(worker.result.cancelled for worker in results):
                target = JobStatus.CANCELLED
                exit_code = next(
                    (worker.result.exit_code for worker in results if worker.result.exit_code != 0),
                    None,
                )
                error = None
            elif all(worker.result.exit_code == 0 for worker in results):
                target = JobStatus.COMPLETED
                exit_code = 0
                error = None
            else:
                target = JobStatus.FAILED
                exit_code = next(
                    (worker.result.exit_code for worker in results if worker.result.exit_code != 0),
                    None,
                )
                error = "One or more Blender GPU workers did not complete"
            job.status = transition_job(job.status, target)
            job.progress = 1.0 if target is JobStatus.COMPLETED else job.progress
            job.process_pid = None
            job.exit_code = exit_code
            job.error = error
            job.finished_at = utc_now()
        await self._publish_status(job)

    def _worker_failure_message(self, worker: WorkerRunResult) -> str:
        result = worker.result
        if result.timed_out:
            detail = "render exceeded the job wall-time limit"
        elif result.limit_exceeded:
            detail = "render exceeded the job output or log size limit"
        elif result.failure_kind is not None:
            detail = self._failure_message(result)
        else:
            detail = f"Blender exited with code {result.exit_code}"
        return f"GPU {worker.worker_id}: {detail}"

    @staticmethod
    def _failure_message(result: RunResult) -> str:
        if result.failure_kind is FailureKind.SYSTEM_MEMORY_OOM:
            return "Render exceeded its per-job cgroup system memory limit"
        if result.failure_kind is FailureKind.GPU_MEMORY_OOM:
            return (
                "Blender/CUDA reported GPU or shared GPU-memory exhaustion; "
                "the system-memory cgroup does not limit VRAM"
            )
        if result.failure_kind is FailureKind.BLENDER_ABORT:
            return "Blender aborted (SIGABRT) without a cgroup OOM event"
        return f"Blender exited with code {result.exit_code}"

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
