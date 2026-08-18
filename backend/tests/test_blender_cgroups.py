from __future__ import annotations

import asyncio
import errno
import resource
from pathlib import Path
from uuid import uuid4

import pytest

from app.blender.cgroups import (
    CgroupIsolation,
    CgroupLimits,
    LocalCgroupFileSystem,
    MemoryEvents,
)
from app.blender.command import RenderCommand
from app.blender.runner import FailureKind, RunnerError, RunnerLimits, SandboxRunner
from app.config import ResourceIsolationMode


class FakeCgroupFileSystem(LocalCgroupFileSystem):
    def __init__(self, delegated_root: Path) -> None:
        self.delegated_root = delegated_root

    def mkdir(self, path: Path) -> None:
        super().mkdir(path)
        if path.parent == self.delegated_root:
            controls = {
                "cgroup.events": "populated 0\nfrozen 0\n",
                "cgroup.kill": "",
                "cgroup.procs": "",
                "memory.current": "0\n",
                "memory.events": ("low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n"),
                "memory.max": "max\n",
                "memory.oom.group": "0\n",
                "memory.swap.current": "0\n",
                "memory.swap.max": "max\n",
                "pids.current": "0\n",
                "pids.events": "max 0\n",
                "pids.max": "max\n",
            }
            for name, value in controls.items():
                (path / name).write_text(value, encoding="ascii")


class ReadOnlyCgroupFileSystem(FakeCgroupFileSystem):
    def mkdir(self, path: Path) -> None:
        if path == self.delegated_root:
            raise OSError(errno.EROFS, "read-only cgroup hierarchy")
        super().mkdir(path)


class BrokenAttachCgroupFileSystem(FakeCgroupFileSystem):
    def write_text(self, path: Path, value: str) -> None:
        super().write_text(path, value)
        if path.name == "memory.oom.group" and path.parent.name.startswith("job-"):
            (path.parent / "cgroup.procs").unlink()


def fake_hierarchy(
    tmp_path: Path,
    *,
    create_delegated_root: bool = True,
    cgroup_v2: bool = True,
) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    mount = tmp_path / "cgroup"
    mount.mkdir()
    delegated = mount / "render-node"
    if create_delegated_root:
        delegated.mkdir()
        (delegated / "cgroup.controllers").write_text("memory pids\n", encoding="ascii")
        (delegated / "cgroup.subtree_control").write_text("memory pids\n", encoding="ascii")
    mountinfo = tmp_path / "mountinfo"
    if cgroup_v2:
        mountinfo.write_text(
            f"36 25 0:32 / {mount} rw,nosuid,nodev,noexec - cgroup2 cgroup rw\n",
            encoding="utf-8",
        )
    else:
        mountinfo.write_text(
            f"36 25 0:32 / {mount} rw,nosuid,nodev,noexec - cgroup cgroup rw,memory\n",
            encoding="utf-8",
        )
    process_cgroup = tmp_path / "process-cgroup"
    process_cgroup.write_text("0::/\n" if cgroup_v2 else "4:memory:/\n", encoding="utf-8")
    return delegated, mountinfo, process_cgroup


def limits() -> CgroupLimits:
    return CgroupLimits(
        memory_bytes=2 * 1024**3,
        swap_bytes=512 * 1024**2,
        pids=64,
        cleanup_timeout_seconds=0.2,
    )


def strict_isolation(
    tmp_path: Path,
    *,
    file_system_type: type[FakeCgroupFileSystem] = FakeCgroupFileSystem,
) -> tuple[CgroupIsolation, Path]:
    delegated, mountinfo, process_cgroup = fake_hierarchy(tmp_path)
    isolation = CgroupIsolation(
        ResourceIsolationMode.STRICT,
        limits(),
        configured_root=delegated,
        mountinfo_path=mountinfo,
        process_cgroup_path=process_cgroup,
        file_system=file_system_type(delegated),
    )
    return isolation, delegated


async def test_fake_cgroup_v2_is_configured_attached_diagnosed_and_removed(
    tmp_path: Path,
) -> None:
    isolation, delegated = strict_isolation(tmp_path)
    await isolation.initialize()

    assert isolation.status.available is True
    assert isolation.status.cgroup_version == 2
    assert isolation.status.swap_limit_supported is True
    assert isolation.status.oom_group_supported is True

    job_id = uuid4()
    handle = await isolation.create(job_id)
    assert handle is not None
    assert handle.path.name == f"job-{job_id.hex}"
    assert (handle.path / "memory.max").read_text() == str(2 * 1024**3)
    assert (handle.path / "memory.swap.max").read_text() == str(512 * 1024**2)
    assert (handle.path / "pids.max").read_text() == "64"
    assert (handle.path / "memory.oom.group").read_text() == "1"

    await handle.attach(999_999)
    assert (handle.path / "cgroup.procs").read_text() == "999999"
    (handle.path / "memory.events").write_text(
        "low 0\nhigh 0\nmax 1\noom 1\noom_kill 1\noom_group_kill 1\n"
    )
    events = await handle.memory_events()
    assert events.killed_by_oom is True
    assert events.maximum == 1
    await handle.cleanup()

    assert not handle.path.exists()
    assert not list(delegated.glob("job-*"))


async def test_strict_mode_reports_read_only_and_cgroup_v1_without_writing(
    tmp_path: Path,
) -> None:
    read_only_root, mountinfo, process_cgroup = fake_hierarchy(
        tmp_path / "readonly", create_delegated_root=False
    )
    read_only = CgroupIsolation(
        ResourceIsolationMode.STRICT,
        limits(),
        configured_root=read_only_root,
        mountinfo_path=mountinfo,
        process_cgroup_path=process_cgroup,
        file_system=ReadOnlyCgroupFileSystem(read_only_root),
    )
    await read_only.initialize()

    assert read_only.status.available is False
    assert "read-only" in read_only.status.message
    assert not read_only_root.exists()

    v1_root, v1_mountinfo, v1_process_cgroup = fake_hierarchy(
        tmp_path / "v1", create_delegated_root=False, cgroup_v2=False
    )
    v1 = CgroupIsolation(
        ResourceIsolationMode.STRICT,
        limits(),
        configured_root=v1_root,
        mountinfo_path=v1_mountinfo,
        process_cgroup_path=v1_process_cgroup,
    )
    await v1.initialize()

    assert v1.status.available is False
    assert "cgroup v1" in v1.status.message
    assert not v1_root.exists()


async def test_trusted_mode_is_explicit_and_does_not_inspect_cgroup(tmp_path: Path) -> None:
    isolation = CgroupIsolation(
        ResourceIsolationMode.TRUSTED,
        limits(),
        mountinfo_path=tmp_path / "missing-mountinfo",
        process_cgroup_path=tmp_path / "missing-cgroup",
    )

    await isolation.initialize()

    assert isolation.status.available is True
    assert isolation.status.backend == "external"
    assert "no per-job cgroup" in isolation.status.message
    assert await isolation.create(uuid4()) is None


def executable(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "fake-blender"
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n")
    path.chmod(0o755)
    return path


def render_command(tmp_path: Path, binary: Path) -> RenderCommand:
    for child in ("output", "logs", "temp"):
        (tmp_path / child).mkdir(exist_ok=True)
    return RenderCommand(
        arguments=(str(binary), "--background", "--factory-startup", "--disable-autoexec"),
        job_directory=tmp_path,
        output_directory=tmp_path / "output",
        log_path=tmp_path / "logs" / "blender.log",
    )


def strict_runner(isolation: CgroupIsolation, *, timeout: float = 2) -> SandboxRunner:
    return SandboxRunner(
        RunnerLimits(
            timeout_seconds=timeout,
            terminate_grace_seconds=0.1,
            max_output_bytes=1024 * 1024,
            max_log_bytes=1024 * 1024,
        ),
        isolation,
    )


@pytest.mark.parametrize(
    ("body", "timeout_seconds", "cancel_on_ready"),
    [
        ("echo success", 2.0, False),
        ("echo failure; exit 3", 2.0, False),
        ("sleep 60", 0.1, False),
        ("echo READY; sleep 60", 2.0, True),
    ],
)
async def test_runner_cleans_cgroup_after_every_terminal_path(
    tmp_path: Path,
    body: str,
    timeout_seconds: float,
    cancel_on_ready: bool,
) -> None:
    isolation, delegated = strict_isolation(tmp_path)
    runner = strict_runner(isolation, timeout=timeout_seconds)
    await runner.initialize()
    cancellation = asyncio.Event()
    job_id = uuid4()
    cgroup_procs = delegated / f"job-{job_id.hex}" / "cgroup.procs"

    async def on_line(line: str) -> None:
        if cancel_on_ready and line == "READY":
            cancellation.set()

    async def on_started(pid: int) -> None:
        assert pid > 1

    result = await runner.run(
        render_command(
            tmp_path,
            executable(tmp_path, f'grep -q "^0$" "$TEST_CGROUP_PROCS"\n{body}'),
        ),
        job_id=job_id,
        environment={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path / "temp"),
            "TEST_CGROUP_PROCS": str(cgroup_procs),
        },
        cancellation=cancellation,
        on_started=on_started,
        on_line=on_line,
    )

    assert not list(delegated.glob("job-*"))
    if cancel_on_ready:
        assert result.cancelled is True
    elif timeout_seconds < 1:
        assert result.timed_out is True


async def test_runner_cleans_cgroup_when_process_setup_fails(tmp_path: Path) -> None:
    isolation, delegated = strict_isolation(tmp_path, file_system_type=BrokenAttachCgroupFileSystem)
    runner = strict_runner(isolation)
    await runner.initialize()

    async def ignore_line(line: str) -> None:
        pass

    async def ignore_pid(pid: int) -> None:
        pass

    with pytest.raises(RunnerError, match="resource boundary"):
        await runner.run(
            render_command(tmp_path, executable(tmp_path, "echo unreachable")),
            job_id=uuid4(),
            environment={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "temp")},
            cancellation=asyncio.Event(),
            on_started=ignore_pid,
            on_line=ignore_line,
        )

    assert not list(delegated.glob("job-*"))


def test_child_limits_never_set_rlimit_as(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolation = CgroupIsolation(ResourceIsolationMode.TRUSTED, limits())
    runner = strict_runner(isolation)
    applied: list[int] = []

    class FakeLibC:
        @staticmethod
        def prctl(*args: int) -> int:
            return 0

    monkeypatch.setattr(
        "app.blender.runner.resource.setrlimit",
        lambda kind, value: applied.append(kind),
    )
    monkeypatch.setattr("app.blender.runner.ctypes.CDLL", lambda name: FakeLibC())

    runner._child_limits()

    assert resource.RLIMIT_AS not in applied
    assert resource.RLIMIT_FSIZE in applied
    assert resource.RLIMIT_CPU in applied


def test_failure_classification_uses_cgroup_events_before_blender_log_or_signal() -> None:
    cgroup_oom = SandboxRunner._failure_kind(
        -9,
        memory_events=MemoryEvents(oom=1, oom_kill=1),
        gpu_memory_reported=True,
        cancelled=False,
        timed_out=False,
        limit_exceeded=False,
    )
    gpu_oom = SandboxRunner._failure_kind(
        -6,
        memory_events=MemoryEvents(),
        gpu_memory_reported=True,
        cancelled=False,
        timed_out=False,
        limit_exceeded=False,
    )
    plain_abort = SandboxRunner._failure_kind(
        -6,
        memory_events=MemoryEvents(),
        gpu_memory_reported=False,
        cancelled=False,
        timed_out=False,
        limit_exceeded=False,
    )

    assert cgroup_oom is FailureKind.SYSTEM_MEMORY_OOM
    assert gpu_oom is FailureKind.GPU_MEMORY_OOM
    assert plain_abort is FailureKind.BLENDER_ABORT
