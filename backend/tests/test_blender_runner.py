from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.blender.command import RenderCommand
from app.blender.runner import RunnerLimits, SandboxRunner
from app.blender.sandbox import SandboxPolicy
from app.config import Environment


def executable(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "fake-blender"
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n")
    path.chmod(0o755)
    return path


def command(tmp_path: Path, binary: Path) -> RenderCommand:
    for child in ("output", "logs", "temp"):
        (tmp_path / child).mkdir(exist_ok=True)
    return RenderCommand(
        arguments=(str(binary), "--background", "--factory-startup", "--disable-autoexec"),
        job_directory=tmp_path,
        output_directory=tmp_path / "output",
        log_path=tmp_path / "logs" / "blender.log",
    )


def runner(*, timeout_seconds: float = 5) -> SandboxRunner:
    return SandboxRunner(
        SandboxPolicy(environment=Environment.TEST, allow_unsandboxed=False),
        RunnerLimits(
            timeout_seconds=timeout_seconds,
            terminate_grace_seconds=0.2,
            max_output_bytes=1024 * 1024,
            max_log_bytes=1024 * 1024,
            memory_bytes=1024**3,
            pids=32,
        ),
    )


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
        environment={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "temp")},
        cancellation=asyncio.Event(),
        on_started=ignore_pid,
        on_line=ignore_line,
    )
    assert result.timed_out is True
    assert result.exit_code != 0


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
