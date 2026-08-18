"""The sole subprocess boundary for untrusted Blender scenes."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import math
import os
import resource
import signal
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from pathlib import Path
from uuid import UUID

import aiofiles

from app.blender.cgroups import (
    CgroupHandle,
    CgroupIsolation,
    CgroupIsolationError,
    MemoryEvents,
    ResourceIsolationStatus,
)
from app.blender.command import RenderCommand

LineCallback = Callable[[str], Awaitable[None]]
StartedCallback = Callable[[int], Awaitable[None]]
OutputCallback = Callable[[Path], Awaitable[None]]


def _cpu_time_limit(wall_timeout_seconds: float, logical_cpus: int | None = None) -> int:
    cpu_count = logical_cpus if logical_cpus is not None else os.cpu_count()
    return max(1, math.ceil(wall_timeout_seconds * max(cpu_count or 1, 1)))


class RunnerError(RuntimeError):
    pass


class WorkerRunnerError(RunnerError):
    def __init__(self, worker_id: str, message: str) -> None:
        self.worker_id = worker_id
        super().__init__(f"Blender worker {worker_id} failed: {message}")


class FailureKind(StrEnum):
    SYSTEM_MEMORY_OOM = "system_memory_oom"
    GPU_MEMORY_OOM = "gpu_memory_oom"
    BLENDER_ABORT = "blender_abort"
    BLENDER_ERROR = "blender_error"


@dataclass(frozen=True, slots=True)
class RunnerLimits:
    timeout_seconds: float
    terminate_grace_seconds: float
    max_output_bytes: int
    max_log_bytes: int


@dataclass(frozen=True, slots=True)
class RunResult:
    exit_code: int
    cancelled: bool = False
    timed_out: bool = False
    limit_exceeded: bool = False
    failure_kind: FailureKind | None = None
    memory_events: MemoryEvents = field(default_factory=MemoryEvents)


@dataclass(frozen=True, slots=True)
class WorkerRunSpec:
    worker_id: str
    command: RenderCommand
    environment: dict[str, str]
    on_started: StartedCallback
    on_line: LineCallback
    on_output: OutputCallback | None = None


@dataclass(frozen=True, slots=True)
class WorkerRunResult:
    worker_id: str
    result: RunResult


class _LogBudget:
    def __init__(self, maximum_bytes: int) -> None:
        self._remaining = maximum_bytes
        self._lock = asyncio.Lock()

    async def take(self, chunk: bytes) -> tuple[bytes, bool]:
        async with self._lock:
            accepted = chunk[: self._remaining]
            self._remaining -= len(accepted)
            return accepted, len(accepted) < len(chunk)


class SandboxRunner:
    def __init__(
        self,
        limits: RunnerLimits,
        resource_isolation: CgroupIsolation,
    ) -> None:
        self._limits = limits
        self._resource_isolation = resource_isolation

    async def initialize(self) -> None:
        await self._resource_isolation.initialize()

    @property
    def available(self) -> bool:
        return self._resource_isolation.available

    @property
    def mode(self) -> str:
        return "local"

    @property
    def unavailable_reason(self) -> str | None:
        return self._resource_isolation.unavailable_reason

    @property
    def isolation_status(self) -> ResourceIsolationStatus:
        return self._resource_isolation.status

    async def run(
        self,
        command: RenderCommand,
        *,
        job_id: UUID,
        environment: dict[str, str],
        cancellation: asyncio.Event,
        on_started: StartedCallback,
        on_line: LineCallback,
        on_output: OutputCallback | None = None,
    ) -> RunResult:
        isolation_reason = self._resource_isolation.unavailable_reason
        if isolation_reason is not None:
            raise RunnerError(isolation_reason)
        binary = Path(command.arguments[0])
        if not await asyncio.to_thread(self._is_executable, binary):
            raise RunnerError(f"Blender executable is unavailable: {binary}")
        try:
            cgroup = await self._resource_isolation.create(job_id)
        except CgroupIsolationError as exc:
            raise RunnerError(str(exc)) from exc
        try:
            return await self._run_process(
                command,
                cgroup=cgroup,
                environment=environment,
                cancellation=cancellation,
                on_started=on_started,
                on_line=on_line,
                on_output=on_output,
                log_budget=_LogBudget(self._limits.max_log_bytes),
            )
        finally:
            if cgroup is not None:
                cleanup_task = asyncio.create_task(cgroup.cleanup())
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    await cleanup_task
                    raise
                except CgroupIsolationError as exc:
                    raise RunnerError(str(exc)) from exc

    async def run_workers(
        self,
        workers: tuple[WorkerRunSpec, ...],
        *,
        job_id: UUID,
        cancellation: asyncio.Event,
    ) -> tuple[WorkerRunResult, ...]:
        if not workers:
            raise RunnerError("A render worker group cannot be empty")
        isolation_reason = self._resource_isolation.unavailable_reason
        if isolation_reason is not None:
            raise RunnerError(isolation_reason)
        for worker in workers:
            binary = Path(worker.command.arguments[0])
            if not await asyncio.to_thread(self._is_executable, binary):
                raise WorkerRunnerError(
                    worker.worker_id, f"Blender executable is unavailable: {binary}"
                )
        try:
            cgroup = await self._resource_isolation.create(job_id)
        except CgroupIsolationError as exc:
            raise RunnerError(str(exc)) from exc
        try:
            return await self._run_worker_group(
                workers,
                cgroup=cgroup,
                cancellation=cancellation,
            )
        finally:
            if cgroup is not None:
                cleanup_task = asyncio.create_task(cgroup.cleanup())
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    await cleanup_task
                    raise
                except CgroupIsolationError as exc:
                    raise RunnerError(str(exc)) from exc

    async def _run_worker_group(
        self,
        workers: tuple[WorkerRunSpec, ...],
        *,
        cgroup: CgroupHandle | None,
        cancellation: asyncio.Event,
    ) -> tuple[WorkerRunResult, ...]:
        group_cancellation = asyncio.Event()
        limit_event = asyncio.Event()
        log_budget = _LogBudget(self._limits.max_log_bytes)
        deadline = asyncio.get_running_loop().time() + self._limits.timeout_seconds

        async def forward_cancellation() -> None:
            await cancellation.wait()
            group_cancellation.set()

        cancellation_task = asyncio.create_task(forward_cancellation())
        tasks = {
            asyncio.create_task(
                self._run_process(
                    worker.command,
                    cgroup=cgroup,
                    environment=worker.environment,
                    cancellation=group_cancellation,
                    on_started=worker.on_started,
                    on_line=worker.on_line,
                    on_output=worker.on_output,
                    deadline=deadline,
                    limit_event=limit_event,
                    log_budget=log_budget,
                )
            ): worker
            for worker in workers
        }
        results: dict[str, RunResult] = {}
        first_error: tuple[str, BaseException] | None = None
        pending = set(tasks)
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    worker = tasks[task]
                    try:
                        result = task.result()
                    except asyncio.CancelledError:
                        raise
                    except BaseException as exc:
                        if first_error is None:
                            first_error = (worker.worker_id, exc)
                        group_cancellation.set()
                        continue
                    results[worker.worker_id] = result
                    if self._worker_failed(result):
                        group_cancellation.set()
        finally:
            if pending:
                group_cancellation.set()
                await asyncio.gather(*pending, return_exceptions=True)
            cancellation_task.cancel()
            await asyncio.gather(cancellation_task, return_exceptions=True)

        if first_error is not None:
            worker_id, error = first_error
            raise WorkerRunnerError(worker_id, str(error)) from error
        return tuple(
            WorkerRunResult(worker.worker_id, results[worker.worker_id]) for worker in workers
        )

    @staticmethod
    def _worker_failed(result: RunResult) -> bool:
        return not result.cancelled and (
            result.timed_out
            or result.limit_exceeded
            or result.failure_kind is not None
            or result.exit_code != 0
        )

    async def _run_process(
        self,
        command: RenderCommand,
        *,
        cgroup: CgroupHandle | None,
        environment: dict[str, str],
        cancellation: asyncio.Event,
        on_started: StartedCallback,
        on_line: LineCallback,
        on_output: OutputCallback | None,
        deadline: float | None = None,
        limit_event: asyncio.Event | None = None,
        log_budget: _LogBudget | None = None,
    ) -> RunResult:
        limit_event = limit_event or asyncio.Event()
        log_budget = log_budget or _LogBudget(self._limits.max_log_bytes)
        gpu_memory_event = asyncio.Event()
        try:
            process = await asyncio.create_subprocess_exec(
                *command.arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=command.job_directory,
                env=environment,
                close_fds=True,
                start_new_session=True,
                preexec_fn=partial(self._child_limits, cgroup),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RunnerError(f"Cannot start Blender in its resource boundary: {exc}") from exc
        stdout_task: asyncio.Task[None] | None = None
        process_task: asyncio.Task[int] | None = None
        cancellation_task: asyncio.Task[bool] | None = None
        output_task: asyncio.Task[None] | None = None
        timed_out = False
        cancelled = False
        try:
            await on_started(process.pid)
            stdout_task = asyncio.create_task(
                self._read_stdout(
                    process,
                    command.log_path,
                    on_line,
                    limit_event,
                    gpu_memory_event,
                    log_budget,
                )
            )
            process_task = asyncio.create_task(process.wait())
            cancellation_task = asyncio.create_task(cancellation.wait())
            output_task = asyncio.create_task(
                self._watch_output(
                    command.output_directory,
                    limit_event,
                    process_task,
                    on_output,
                )
            )
            done, _ = await asyncio.wait(
                {process_task, cancellation_task, output_task},
                timeout=(
                    self._limits.timeout_seconds
                    if deadline is None
                    else max(deadline - asyncio.get_running_loop().time(), 0)
                ),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                timed_out = True
                await self._terminate_group(process)
            elif cancellation_task in done and cancellation.is_set():
                cancelled = True
                await self._terminate_group(process)
            elif output_task in done and limit_event.is_set():
                await self._terminate_group(process)
            await process_task
        finally:
            if process.returncode is None:
                await self._terminate_group(process)
            if cancellation_task is not None:
                cancellation_task.cancel()
                await asyncio.gather(cancellation_task, return_exceptions=True)
            if output_task is not None:
                await output_task
            if stdout_task is not None:
                await stdout_task
        memory_events = await cgroup.memory_events() if cgroup is not None else MemoryEvents()
        exit_code = int(process.returncode if process.returncode is not None else -signal.SIGKILL)
        return RunResult(
            exit_code=exit_code,
            cancelled=cancelled,
            timed_out=timed_out,
            limit_exceeded=limit_event.is_set(),
            failure_kind=self._failure_kind(
                exit_code,
                memory_events=memory_events,
                gpu_memory_reported=gpu_memory_event.is_set(),
                cancelled=cancelled,
                timed_out=timed_out,
                limit_exceeded=limit_event.is_set(),
            ),
            memory_events=memory_events,
        )

    def _child_limits(self, cgroup: CgroupHandle | None = None) -> None:
        if cgroup is not None:
            cgroup.attach_current_process_in_child()
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        # RLIMIT_AS is intentionally absent: CUDA/OptiX reserve large virtual address
        # ranges. Actual resident memory and process counts belong to the job cgroup.
        file_limit = self._limits.max_output_bytes
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
        # RLIMIT_CPU counts aggregate CPU time across Blender's render threads.
        # Scale it by available logical CPUs so it cannot expire before the
        # configured wall-time deadline for a fully parallel CPU render.
        cpu_limit = _cpu_time_limit(self._limits.timeout_seconds)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 1))
        libc = ctypes.CDLL(None)
        if libc.prctl(38, 1, 0, 0, 0) != 0:
            os._exit(126)

    async def _terminate_group(self, process: asyncio.subprocess.Process) -> None:
        self._signal_group(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=self._limits.terminate_grace_seconds)
            return
        except TimeoutError:
            self._signal_group(process.pid, signal.SIGKILL)
            await process.wait()

    @staticmethod
    def _signal_group(pid: int, requested_signal: signal.Signals) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, requested_signal)

    async def _read_stdout(
        self,
        process: asyncio.subprocess.Process,
        log_path: Path,
        on_line: LineCallback,
        limit_event: asyncio.Event,
        gpu_memory_event: asyncio.Event,
        log_budget: _LogBudget,
    ) -> None:
        if process.stdout is None:
            return
        written = 0
        pending = b""
        async with aiofiles.open(log_path, "wb") as log:
            while chunk := await process.stdout.read(64 * 1024):
                saved, exceeded = await log_budget.take(chunk)
                if saved:
                    await log.write(saved)
                    written += len(saved)
                if exceeded:
                    limit_event.set()
                pending += saved
                while b"\n" in pending:
                    raw_line, pending = pending.split(b"\n", maxsplit=1)
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r")
                    if self._reports_gpu_memory_exhaustion(line):
                        gpu_memory_event.set()
                    await on_line(line)
                if len(pending) > 1024 * 1024:
                    await on_line(pending.decode("utf-8", errors="replace"))
                    pending = b""
            if pending:
                line = pending.decode("utf-8", errors="replace")
                if self._reports_gpu_memory_exhaustion(line):
                    gpu_memory_event.set()
                await on_line(line)

    @staticmethod
    def _failure_kind(
        exit_code: int,
        *,
        memory_events: MemoryEvents,
        gpu_memory_reported: bool,
        cancelled: bool,
        timed_out: bool,
        limit_exceeded: bool,
    ) -> FailureKind | None:
        if exit_code == 0 or cancelled or timed_out or limit_exceeded:
            return None
        if memory_events.killed_by_oom or (memory_events.oom > 0 and exit_code != 0):
            return FailureKind.SYSTEM_MEMORY_OOM
        if gpu_memory_reported:
            return FailureKind.GPU_MEMORY_OOM
        if exit_code in {-signal.SIGABRT, 128 + signal.SIGABRT}:
            return FailureKind.BLENDER_ABORT
        return FailureKind.BLENDER_ERROR

    @staticmethod
    def _reports_gpu_memory_exhaustion(line: str) -> bool:
        normalized = line.casefold()
        return any(
            marker in normalized
            for marker in (
                "system is out of gpu and shared host memory",
                "out of gpu memory",
                "cuda_error_out_of_memory",
                "cuda error: out of memory",
                "optix error: out of memory",
            )
        )

    async def _watch_output(
        self,
        output_directory: Path,
        limit_event: asyncio.Event,
        process_task: asyncio.Task[int],
        on_output: OutputCallback | None,
    ) -> None:
        observations: dict[Path, tuple[int, int]] = {}
        announced: set[Path] = set()
        while not limit_event.is_set():
            process_closed_at_snapshot = process_task.done()
            size, unsafe, files = await asyncio.to_thread(
                self._directory_snapshot, output_directory
            )
            if unsafe or size > self._limits.max_output_bytes:
                limit_event.set()
                return
            for path, file_size in files.items():
                previous = observations.get(path)
                stable_count = previous[1] + 1 if previous and previous[0] == file_size else 0
                observations[path] = (file_size, stable_count)
                if path not in announced and (process_closed_at_snapshot or stable_count >= 1):
                    announced.add(path)
                    if on_output is not None:
                        await self._notify_output(on_output, path)
            # A callback can spend noticeable time generating a preview while the
            # process writes another frame and exits. Only stop after a snapshot
            # that began with the process already closed, so that late output is
            # observed at least once.
            if process_closed_at_snapshot:
                return
            await asyncio.sleep(0.2)

    @staticmethod
    async def _notify_output(callback: OutputCallback, path: Path) -> None:
        try:
            await callback(path)
        except Exception:
            # Artifact parsing and preview generation are best-effort and must not
            # turn a successful Blender process into a failed render.
            return

    @staticmethod
    def _directory_snapshot(root: Path) -> tuple[int, bool, dict[Path, int]]:
        total = 0
        files_by_path: dict[Path, int] = {}
        for current_root, directories, files in os.walk(root, followlinks=False):
            current = Path(current_root)
            for name in (*directories, *files):
                path = current / name
                if path.is_symlink():
                    return total, True, files_by_path
            for filename in files:
                path = current / filename
                try:
                    file_size = path.stat().st_size
                except FileNotFoundError:
                    continue
                total += file_size
                files_by_path[path] = file_size
        return total, False, files_by_path

    @staticmethod
    def _is_executable(path: Path) -> bool:
        return path.is_file() and os.access(path, os.X_OK)
