from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from app.blender.cgroups import CgroupIsolation, CgroupLimits
from app.blender.command import RenderCommand
from app.blender.runner import (
    FailureKind,
    RunnerLimits,
    SandboxRunner,
    WorkerRunnerError,
    WorkerRunSpec,
    _cpu_time_limit,
)
from app.config import ResourceIsolationMode


def executable(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "fake-blender"
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n")
    path.chmod(0o755)
    return path


def command(tmp_path: Path, binary: Path, *, log_name: str = "blender.log") -> RenderCommand:
    for child in ("output", "logs", "temp"):
        (tmp_path / child).mkdir(exist_ok=True)
    return RenderCommand(
        arguments=(str(binary), "--background", "--factory-startup", "--disable-autoexec"),
        job_directory=tmp_path,
        output_directory=tmp_path / "output",
        log_path=tmp_path / "logs" / log_name,
    )


def runner(*, timeout_seconds: float = 5, max_log_bytes: int = 1024 * 1024) -> SandboxRunner:
    return SandboxRunner(
        RunnerLimits(
            timeout_seconds=timeout_seconds,
            terminate_grace_seconds=0.2,
            max_output_bytes=1024 * 1024,
            max_log_bytes=max_log_bytes,
        ),
        CgroupIsolation(
            ResourceIsolationMode.TRUSTED,
            CgroupLimits(
                memory_bytes=1024**3,
                swap_bytes=0,
                pids=32,
                cleanup_timeout_seconds=0.2,
            ),
        ),
    )


def test_cpu_limit_accounts_for_parallel_render_threads() -> None:
    assert _cpu_time_limit(30, logical_cpus=8) == 240
    assert _cpu_time_limit(0.1, logical_cpus=0) == 1


async def test_runner_preserves_log_and_does_not_inherit_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = executable(
        tmp_path,
        'printf "secret=%s\\n" "${BACKEND_SECRET-unset}"\n'
        'printf "Fra:1 | Rendering 1 / 2 samples\\n"',
    )
    monkeypatch.setenv("BACKEND_SECRET", "must-not-leak")
    lines: list[str] = []
    pids: list[int] = []

    async def on_line(line: str) -> None:
        lines.append(line)

    async def on_started(pid: int) -> None:
        pids.append(pid)

    result = await runner().run(
        command(tmp_path, binary),
        job_id=uuid4(),
        environment={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "temp")},
        cancellation=asyncio.Event(),
        on_started=on_started,
        on_line=on_line,
    )

    assert result.exit_code == 0
    assert pids and pids[0] > 0
    assert "secret=unset" in lines
    raw_log = await asyncio.to_thread((tmp_path / "logs" / "blender.log").read_text)
    assert "must-not-leak" not in raw_log


async def test_cancel_terminates_whole_process_group(tmp_path: Path) -> None:
    binary = executable(
        tmp_path,
        'sleep 60 &\nchild="$!"\nprintf "%s" "$child" > "$PWD/child.pid"\necho READY\nwait',
    )
    cancellation = asyncio.Event()

    async def on_line(line: str) -> None:
        if line == "READY":
            cancellation.set()

    async def on_started(pid: int) -> None:
        assert pid > 0

    result = await runner().run(
        command(tmp_path, binary),
        job_id=uuid4(),
        environment={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "temp")},
        cancellation=cancellation,
        on_started=on_started,
        on_line=on_line,
    )

    child_pid = int(await asyncio.to_thread((tmp_path / "child.pid").read_text))
    assert result.cancelled is True
    await assert_process_stopped(child_pid)


async def test_timeout_terminates_process(tmp_path: Path) -> None:
    binary = executable(tmp_path, "sleep 60")

    async def ignore_line(line: str) -> None:
        pass

    async def ignore_pid(pid: int) -> None:
        pass

    result = await runner(timeout_seconds=0.1).run(
        command(tmp_path, binary),
        job_id=uuid4(),
        environment={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "temp")},
        cancellation=asyncio.Event(),
        on_started=ignore_pid,
        on_line=ignore_line,
    )
    assert result.timed_out is True
    assert result.exit_code != 0


async def test_gpu_memory_log_is_distinct_from_plain_sigabrt(tmp_path: Path) -> None:
    async def ignore_line(line: str) -> None:
        pass

    async def ignore_pid(pid: int) -> None:
        pass

    gpu_result = await runner().run(
        command(
            tmp_path,
            executable(
                tmp_path,
                "echo 'System is out of GPU and shared host memory'; kill -ABRT $$",
            ),
        ),
        job_id=uuid4(),
        environment={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "temp")},
        cancellation=asyncio.Event(),
        on_started=ignore_pid,
        on_line=ignore_line,
    )
    plain_root = tmp_path / "plain"
    plain_root.mkdir()
    plain_result = await runner().run(
        command(plain_root, executable(plain_root, "kill -ABRT $$")),
        job_id=uuid4(),
        environment={"PATH": "/usr/bin:/bin", "HOME": str(plain_root / "temp")},
        cancellation=asyncio.Event(),
        on_started=ignore_pid,
        on_line=ignore_line,
    )

    assert gpu_result.failure_kind is FailureKind.GPU_MEMORY_OOM
    assert plain_result.failure_kind is FailureKind.BLENDER_ABORT


async def test_worker_group_runs_concurrently_with_separate_logs(tmp_path: Path) -> None:
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    first = executable(
        tmp_path / "first",
        'touch "$PWD/first.ready"\n'
        'while [ ! -f "$PWD/second.ready" ]; do sleep 0.02; done\n'
        "echo first-worker",
    )
    second = executable(
        tmp_path / "second",
        'touch "$PWD/second.ready"\n'
        'while [ ! -f "$PWD/first.ready" ]; do sleep 0.02; done\n'
        "echo second-worker",
    )
    for child in ("output", "logs", "temp"):
        (tmp_path / child).mkdir(exist_ok=True)

    async def ignore_line(line: str) -> None:
        pass

    async def ignore_pid(pid: int) -> None:
        pass

    workers = (
        WorkerRunSpec(
            "GPU-a",
            command(tmp_path, first, log_name="blender-gpu-0.log"),
            {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "temp")},
            ignore_pid,
            ignore_line,
        ),
        WorkerRunSpec(
            "GPU-b",
            command(tmp_path, second, log_name="blender-gpu-1.log"),
            {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "temp")},
            ignore_pid,
            ignore_line,
        ),
    )

    results = await runner().run_workers(
        workers,
        job_id=uuid4(),
        cancellation=asyncio.Event(),
    )

    assert [worker.result.exit_code for worker in results] == [0, 0]
    assert "first-worker" in (tmp_path / "logs" / "blender-gpu-0.log").read_text()
    assert "second-worker" in (tmp_path / "logs" / "blender-gpu-1.log").read_text()


async def test_output_watcher_rescans_after_process_exits_during_callback(
    tmp_path: Path,
) -> None:
    binary = executable(
        tmp_path,
        'printf first > "$PWD/output/frame_0001.png"\n'
        'while [ ! -f "$PWD/output-callback.started" ]; do sleep 0.01; done\n'
        'printf second > "$PWD/output/frame_0002.png"\n'
        "echo PROCESS_OUTPUT_FINISHED",
    )
    announced: list[str] = []
    process_output_finished = asyncio.Event()

    async def on_line(line: str) -> None:
        if line == "PROCESS_OUTPUT_FINISHED":
            process_output_finished.set()

    async def ignore_pid(pid: int) -> None:
        pass

    async def on_output(path: Path) -> None:
        announced.append(path.name)
        if path.name != "frame_0001.png":
            return
        await asyncio.to_thread((tmp_path / "output-callback.started").touch)
        await process_output_finished.wait()
        # Keep the callback busy until the process task has observed the exit.
        await asyncio.sleep(0.05)

    result = await runner().run(
        command(tmp_path, binary),
        job_id=uuid4(),
        environment={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "temp")},
        cancellation=asyncio.Event(),
        on_started=ignore_pid,
        on_line=on_line,
        on_output=on_output,
    )

    assert result.exit_code == 0
    assert announced == ["frame_0001.png", "frame_0002.png"]


async def test_worker_failure_terminates_other_process_group(tmp_path: Path) -> None:
    (tmp_path / "failing").mkdir()
    (tmp_path / "slow").mkdir()
    failing = executable(
        tmp_path / "failing",
        'while [ ! -f "$PWD/slow.ready" ]; do sleep 0.02; done\nexit 7',
    )
    slow = executable(
        tmp_path / "slow",
        'sleep 60 &\nchild="$!"\nprintf "%s" "$child" > "$PWD/slow-child.pid"\n'
        'touch "$PWD/slow.ready"\nwait',
    )
    for child in ("output", "logs", "temp"):
        (tmp_path / child).mkdir(exist_ok=True)

    async def ignore_line(line: str) -> None:
        pass

    async def ignore_pid(pid: int) -> None:
        pass

    results = await runner().run_workers(
        (
            WorkerRunSpec(
                "GPU-fail",
                command(tmp_path, failing, log_name="blender-gpu-0.log"),
                {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "temp")},
                ignore_pid,
                ignore_line,
            ),
            WorkerRunSpec(
                "GPU-slow",
                command(tmp_path, slow, log_name="blender-gpu-1.log"),
                {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "temp")},
                ignore_pid,
                ignore_line,
            ),
        ),
        job_id=uuid4(),
        cancellation=asyncio.Event(),
    )

    assert results[0].result.exit_code == 7
    assert results[1].result.cancelled is True
    child_pid = int((tmp_path / "slow-child.pid").read_text())
    await assert_process_stopped(child_pid)


async def test_worker_start_error_terminates_already_started_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "slow").mkdir()
    (tmp_path / "bad").mkdir()
    slow = executable(
        tmp_path / "slow",
        'sleep 60 &\nchild="$!"\nprintf "%s" "$child" > "$PWD/start-child.pid"\necho STARTED\nwait',
    )
    bad = executable(tmp_path / "bad", "exit 0")
    for child in ("output", "logs", "temp"):
        (tmp_path / child).mkdir(exist_ok=True)
    original_create_subprocess_exec = asyncio.create_subprocess_exec
    slow_ready = asyncio.Event()

    async def create_subprocess_exec(*arguments: object, **kwargs: object) -> object:
        if arguments and arguments[0] == str(bad):
            await slow_ready.wait()
            raise OSError("synthetic exec failure")
        return await original_create_subprocess_exec(*arguments, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)

    async def ignore_line(line: str) -> None:
        if line == "STARTED":
            slow_ready.set()

    async def ignore_pid(pid: int) -> None:
        pass

    with pytest.raises(WorkerRunnerError, match=r"GPU-bad.*synthetic exec failure"):
        await runner().run_workers(
            (
                WorkerRunSpec(
                    "GPU-slow",
                    command(tmp_path, slow, log_name="blender-gpu-0.log"),
                    {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "temp")},
                    ignore_pid,
                    ignore_line,
                ),
                WorkerRunSpec(
                    "GPU-bad",
                    command(tmp_path, bad, log_name="blender-gpu-1.log"),
                    {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "temp")},
                    ignore_pid,
                    ignore_line,
                ),
            ),
            job_id=uuid4(),
            cancellation=asyncio.Event(),
        )

    child_pid = int((tmp_path / "start-child.pid").read_text())
    await assert_process_stopped(child_pid)


async def test_worker_group_shares_one_job_log_limit(tmp_path: Path) -> None:
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    first = executable(tmp_path / "first", "head -c 200 /dev/zero | tr '\\0' a")
    second = executable(tmp_path / "second", "head -c 200 /dev/zero | tr '\\0' b")
    for child in ("output", "logs", "temp"):
        (tmp_path / child).mkdir(exist_ok=True)

    async def ignore_line(line: str) -> None:
        pass

    async def ignore_pid(pid: int) -> None:
        pass

    results = await runner(max_log_bytes=100).run_workers(
        (
            WorkerRunSpec(
                "GPU-a",
                command(tmp_path, first, log_name="blender-gpu-0.log"),
                {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "temp")},
                ignore_pid,
                ignore_line,
            ),
            WorkerRunSpec(
                "GPU-b",
                command(tmp_path, second, log_name="blender-gpu-1.log"),
                {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "temp")},
                ignore_pid,
                ignore_line,
            ),
        ),
        job_id=uuid4(),
        cancellation=asyncio.Event(),
    )

    total_saved = sum(path.stat().st_size for path in (tmp_path / "logs").iterdir())
    assert total_saved <= 100
    assert any(worker.result.limit_exceeded for worker in results)


async def assert_process_stopped(pid: int) -> None:
    for _ in range(50):
        try:
            raw_stat = await asyncio.to_thread(Path(f"/proc/{pid}/stat").read_text)
            state = raw_stat.split()[2]
        except FileNotFoundError:
            return
        if state == "Z":
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"child process {pid} is still running")
