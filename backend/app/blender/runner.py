"""The sole subprocess boundary for untrusted Blender scenes."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import math
import os
import resource
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import aiofiles

from app.blender.command import RenderCommand
from app.blender.sandbox import SandboxPolicy

LineCallback = Callable[[str], Awaitable[None]]
StartedCallback = Callable[[int], Awaitable[None]]
OutputCallback = Callable[[Path], Awaitable[None]]


class RunnerError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RunnerLimits:
    timeout_seconds: float
    terminate_grace_seconds: float
    max_output_bytes: int
    max_log_bytes: int
    memory_bytes: int
    pids: int


@dataclass(frozen=True, slots=True)
class RunResult:
    exit_code: int
    cancelled: bool = False
    timed_out: bool = False
    limit_exceeded: bool = False


class SandboxRunner:
    def __init__(self, policy: SandboxPolicy, limits: RunnerLimits) -> None:
        self._policy = policy
        self._limits = limits

    async def run(
        self,
        command: RenderCommand,
        *,
        environment: dict[str, str],
        cancellation: asyncio.Event,
        on_started: StartedCallback,
        on_line: LineCallback,
        on_output: OutputCallback | None = None,
    ) -> RunResult:
        self._policy.ensure_local_runner_allowed()
        binary = Path(command.arguments[0])
        if not await asyncio.to_thread(self._is_executable, binary):
            raise RunnerError(f"Blender executable is unavailable: {binary}")
        limit_event = asyncio.Event()
        process = await asyncio.create_subprocess_exec(
            *command.arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=command.job_directory,
            env=environment,
            close_fds=True,
            start_new_session=True,
            preexec_fn=self._child_limits,
        )
        await on_started(process.pid)
        stdout_task = asyncio.create_task(
            self._read_stdout(process, command.log_path, on_line, limit_event)
        )
        process_task = asyncio.create_task(process.wait())
        cancellation_task = asyncio.create_task(cancellation.wait())
        output_task = asyncio.create_task(
            self._watch_output(command.output_directory, limit_event, process_task, on_output)
        )
        timed_out = False
        cancelled = False
        try:
            done, _ = await asyncio.wait(
                {process_task, cancellation_task, output_task},
                timeout=self._limits.timeout_seconds,
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
            cancellation_task.cancel()
            await asyncio.gather(cancellation_task, return_exceptions=True)
            await output_task
            await stdout_task
        return RunResult(
            exit_code=int(
                process.returncode if process.returncode is not None else -signal.SIGKILL
            ),
            cancelled=cancelled,
            timed_out=timed_out,
            limit_exceeded=limit_event.is_set(),
        )

    def _child_limits(self) -> None:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        # RLIMIT_NPROC is per real UID, so it cannot safely isolate a process in the
        # shared-user development runner. The production sandbox must enforce this
        # limit through its own cgroup/PID namespace before it may be enabled.
        resource.setrlimit(
            resource.RLIMIT_AS,
            (self._limits.memory_bytes, self._limits.memory_bytes),
        )
        file_limit = self._limits.max_output_bytes
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
        cpu_limit = max(1, math.ceil(self._limits.timeout_seconds))
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
    ) -> None:
        if process.stdout is None:
            return
        written = 0
        pending = b""
        async with aiofiles.open(log_path, "wb") as log:
            while chunk := await process.stdout.read(64 * 1024):
                remaining = max(self._limits.max_log_bytes - written, 0)
                if remaining:
                    saved = chunk[:remaining]
                    await log.write(saved)
                    written += len(saved)
                if len(chunk) > remaining:
                    limit_event.set()
                pending += chunk
                while b"\n" in pending:
                    raw_line, pending = pending.split(b"\n", maxsplit=1)
                    await on_line(raw_line.decode("utf-8", errors="replace").rstrip("\r"))
                if len(pending) > 1024 * 1024:
                    await on_line(pending.decode("utf-8", errors="replace"))
                    pending = b""
            if pending:
                await on_line(pending.decode("utf-8", errors="replace"))

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
                is_closed = process_task.done()
                if path not in announced and (is_closed or stable_count >= 1):
                    announced.add(path)
                    if on_output is not None:
                        await self._notify_output(on_output, path)
            if process_task.done():
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
