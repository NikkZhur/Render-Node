"""Per-job Linux cgroup resource isolation for Blender processes."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import UUID, uuid4

from app.config import ResourceIsolationMode


class CgroupIsolationError(RuntimeError):
    """The configured per-job cgroup boundary cannot be established safely."""


class CgroupFileSystem(Protocol):
    """Small filesystem seam used to exercise cgroupfs behavior in tests."""

    def exists(self, path: Path) -> bool: ...

    def is_dir(self, path: Path) -> bool: ...

    def mkdir(self, path: Path) -> None: ...

    def read_text(self, path: Path) -> str: ...

    def write_text(self, path: Path, value: str) -> None: ...

    def unlink(self, path: Path) -> None: ...

    def rmdir(self, path: Path) -> None: ...


class LocalCgroupFileSystem:
    def exists(self, path: Path) -> bool:
        return path.exists()

    def is_dir(self, path: Path) -> bool:
        return path.is_dir()

    def mkdir(self, path: Path) -> None:
        path.mkdir()

    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def write_text(self, path: Path, value: str) -> None:
        path.write_text(value, encoding="ascii")

    def unlink(self, path: Path) -> None:
        path.unlink()

    def rmdir(self, path: Path) -> None:
        path.rmdir()


@dataclass(frozen=True, slots=True)
class CgroupLimits:
    memory_bytes: int
    swap_bytes: int
    pids: int
    cleanup_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class CgroupFeatures:
    swap_limit: bool
    oom_group: bool
    group_kill: bool


@dataclass(frozen=True, slots=True)
class MemoryEvents:
    low: int = 0
    high: int = 0
    maximum: int = 0
    oom: int = 0
    oom_kill: int = 0
    oom_group_kill: int = 0

    def since(self, baseline: MemoryEvents) -> MemoryEvents:
        return MemoryEvents(
            low=max(self.low - baseline.low, 0),
            high=max(self.high - baseline.high, 0),
            maximum=max(self.maximum - baseline.maximum, 0),
            oom=max(self.oom - baseline.oom, 0),
            oom_kill=max(self.oom_kill - baseline.oom_kill, 0),
            oom_group_kill=max(self.oom_group_kill - baseline.oom_group_kill, 0),
        )

    @property
    def killed_by_oom(self) -> bool:
        return self.oom_kill > 0 or self.oom_group_kill > 0


@dataclass(frozen=True, slots=True)
class ResourceIsolationStatus:
    mode: ResourceIsolationMode
    available: bool
    backend: str
    cgroup_version: int | None
    message: str
    memory_limit_bytes: int
    swap_limit_bytes: int
    pids_limit: int
    swap_limit_supported: bool
    oom_group_supported: bool


@dataclass(frozen=True, slots=True)
class _CgroupMount:
    root: PurePosixPath
    mount_point: Path


class CgroupHandle:
    """One configured cgroup whose children inherit membership from Blender."""

    _FAKE_CONTROL_FILES = (
        "cgroup.events",
        "cgroup.kill",
        "cgroup.procs",
        "memory.current",
        "memory.events",
        "memory.max",
        "memory.oom.group",
        "memory.swap.current",
        "memory.swap.max",
        "pids.current",
        "pids.events",
        "pids.max",
    )

    def __init__(
        self,
        path: Path,
        *,
        file_system: CgroupFileSystem,
        baseline: MemoryEvents,
        cleanup_timeout_seconds: float,
    ) -> None:
        self.path = path
        self._file_system = file_system
        self._baseline = baseline
        self._cleanup_timeout_seconds = cleanup_timeout_seconds

    @property
    def procs_path(self) -> Path:
        return self.path / "cgroup.procs"

    def attach_current_process_in_child(self) -> None:
        """Attach before exec, so Blender cannot spawn outside the job cgroup."""

        descriptor = os.open(self.procs_path, os.O_WRONLY | os.O_CLOEXEC)
        try:
            os.write(descriptor, b"0")
        finally:
            os.close(descriptor)

    async def attach(self, pid: int) -> None:
        await asyncio.to_thread(self._file_system.write_text, self.procs_path, str(pid))

    async def memory_events(self) -> MemoryEvents:
        current = await asyncio.to_thread(
            _read_memory_events,
            self._file_system,
            self.path / "memory.events",
        )
        return current.since(self._baseline)

    async def cleanup(self) -> None:
        await asyncio.to_thread(self._cleanup_sync)

    def _cleanup_sync(self) -> None:
        kill_path = self.path / "cgroup.kill"
        if self._file_system.exists(kill_path):
            with contextlib.suppress(OSError):
                self._file_system.write_text(kill_path, "1")
        else:
            self._kill_listed_processes()

        deadline = time.monotonic() + self._cleanup_timeout_seconds
        while self._live_processes() and time.monotonic() < deadline:
            self._kill_listed_processes()
            time.sleep(0.02)
        live_processes = self._live_processes()
        if live_processes:
            raise CgroupIsolationError(
                f"Cannot remove job cgroup; processes remain: {live_processes}"
            )

        # Real cgroupfs control files cannot be unlinked. The suppressions make the
        # same lifecycle testable with kernel-like files in a temporary directory.
        for filename in self._FAKE_CONTROL_FILES:
            with contextlib.suppress(OSError):
                self._file_system.unlink(self.path / filename)
        try:
            self._file_system.rmdir(self.path)
        except OSError as exc:
            raise CgroupIsolationError(f"Cannot remove job cgroup {self.path}: {exc}") from exc

    def _kill_listed_processes(self) -> None:
        for pid in self._listed_processes():
            if pid == os.getpid():
                continue
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)

    def _live_processes(self) -> list[int]:
        live: list[int] = []
        for pid in self._listed_processes():
            if pid == os.getpid():
                live.append(pid)
                continue
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            except PermissionError:
                live.append(pid)
            else:
                live.append(pid)
        return live

    def _listed_processes(self) -> list[int]:
        try:
            content = self._file_system.read_text(self.procs_path)
        except OSError:
            return []
        return [int(value) for value in content.split() if value.isdecimal() and int(value) > 1]


class CgroupIsolation:
    """Detect and manage a delegated cgroup v2 subtree."""

    def __init__(
        self,
        mode: ResourceIsolationMode,
        limits: CgroupLimits,
        *,
        configured_root: Path | None = None,
        mountinfo_path: Path = Path("/proc/self/mountinfo"),
        process_cgroup_path: Path = Path("/proc/self/cgroup"),
        file_system: CgroupFileSystem | None = None,
    ) -> None:
        self._mode = mode
        self._limits = limits
        self._configured_root = configured_root
        self._mountinfo_path = mountinfo_path
        self._process_cgroup_path = process_cgroup_path
        self._file_system = file_system or LocalCgroupFileSystem()
        self._root: Path | None = None
        self._features: CgroupFeatures | None = None
        self._status = self._trusted_status() if mode is ResourceIsolationMode.TRUSTED else None

    @property
    def status(self) -> ResourceIsolationStatus:
        if self._status is None:
            return self._unavailable_status("Per-job cgroup isolation has not been initialized")
        return self._status

    @property
    def available(self) -> bool:
        return self.status.available

    @property
    def unavailable_reason(self) -> str | None:
        return None if self.available else self.status.message

    async def initialize(self) -> None:
        if self._mode is ResourceIsolationMode.TRUSTED:
            self._status = self._trusted_status()
            return
        try:
            root, features = await asyncio.to_thread(self._initialize_strict_sync)
        except (CgroupIsolationError, OSError) as exc:
            self._root = None
            self._features = None
            self._status = self._unavailable_status(str(exc))
            return
        self._root = root
        self._features = features
        self._status = ResourceIsolationStatus(
            mode=self._mode,
            available=True,
            backend="cgroup_v2",
            cgroup_version=2,
            message=(
                "Per-job cgroup v2 isolation is ready "
                f"(RAM={self._limits.memory_bytes} bytes, pids={self._limits.pids})"
            ),
            memory_limit_bytes=self._limits.memory_bytes,
            swap_limit_bytes=self._limits.swap_bytes,
            pids_limit=self._limits.pids,
            swap_limit_supported=features.swap_limit,
            oom_group_supported=features.oom_group,
        )

    async def create(self, job_id: UUID) -> CgroupHandle | None:
        if self._mode is ResourceIsolationMode.TRUSTED:
            return None
        if self._root is None or self._features is None or not self.available:
            raise CgroupIsolationError(self.unavailable_reason or "Per-job cgroup is unavailable")
        return await asyncio.to_thread(self._create_sync, job_id)

    def _initialize_strict_sync(self) -> tuple[Path, CgroupFeatures]:
        try:
            mountinfo = self._mountinfo_path.read_text(encoding="utf-8")
            process_cgroup = self._process_cgroup_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CgroupIsolationError(f"Cannot inspect Linux cgroups: {exc}") from exc

        mount = _find_cgroup2_mount(mountinfo)
        if mount is None:
            if _contains_cgroup_v1(mountinfo, process_cgroup):
                raise CgroupIsolationError(
                    "cgroup v1 is active; strict per-job isolation requires a delegated "
                    "writable cgroup v2 subtree"
                )
            raise CgroupIsolationError("cgroup v2 is not mounted")
        relative_path = _current_v2_relative_path(process_cgroup, mount.root)
        current_directory = (mount.mount_point / relative_path).resolve()
        root = (
            self._configured_root.expanduser().resolve()
            if self._configured_root is not None
            else current_directory / "render-node"
        )
        if not root.is_relative_to(current_directory):
            raise CgroupIsolationError(
                f"Configured cgroup root {root} is outside the current delegated subtree "
                f"{current_directory}"
            )
        if not self._file_system.exists(root):
            try:
                self._file_system.mkdir(root)
            except OSError as exc:
                raise CgroupIsolationError(
                    f"Cannot create delegated cgroup root {root}: {exc}"
                ) from exc
        if not self._file_system.is_dir(root):
            raise CgroupIsolationError(f"Configured cgroup root is not a directory: {root}")

        controllers_path = root / "cgroup.controllers"
        subtree_path = root / "cgroup.subtree_control"
        try:
            controllers = set(self._file_system.read_text(controllers_path).split())
        except OSError as exc:
            raise CgroupIsolationError(
                f"Cgroup root is not a usable delegated v2 subtree: {exc}"
            ) from exc
        missing = {"memory", "pids"} - controllers
        if missing:
            raise CgroupIsolationError(
                "Delegated cgroup v2 subtree is missing controllers: " + ", ".join(sorted(missing))
            )
        self._enable_subtree_controllers(subtree_path)

        probe = root / f".probe-{os.getpid()}-{uuid4().hex}"
        try:
            self._file_system.mkdir(probe)
            required = ("memory.max", "memory.events", "pids.max", "cgroup.procs")
            absent = [name for name in required if not self._file_system.exists(probe / name)]
            if absent:
                raise CgroupIsolationError(
                    "Delegated cgroup v2 subtree did not expose required controls: "
                    + ", ".join(absent)
                )
            features = CgroupFeatures(
                swap_limit=self._file_system.exists(probe / "memory.swap.max"),
                oom_group=self._file_system.exists(probe / "memory.oom.group"),
                group_kill=self._file_system.exists(probe / "cgroup.kill"),
            )
        except OSError as exc:
            raise CgroupIsolationError(
                f"Delegated cgroup v2 subtree is not writable at {root}: {exc}"
            ) from exc
        finally:
            self._remove_probe(probe)
        return root, features

    def _enable_subtree_controllers(self, subtree_path: Path) -> None:
        try:
            enabled = {
                value.removeprefix("+")
                for value in self._file_system.read_text(subtree_path).split()
            }
            missing = {"memory", "pids"} - enabled
            if missing:
                self._file_system.write_text(
                    subtree_path,
                    " ".join(f"+{controller}" for controller in sorted(missing)),
                )
                enabled = {
                    value.removeprefix("+")
                    for value in self._file_system.read_text(subtree_path).split()
                }
            missing = {"memory", "pids"} - enabled
            if missing:
                raise CgroupIsolationError(
                    "Cannot enable delegated cgroup controllers: " + ", ".join(sorted(missing))
                )
        except OSError as exc:
            raise CgroupIsolationError(
                f"Cannot enable memory/pids controllers in {subtree_path}: {exc}"
            ) from exc

    def _create_sync(self, job_id: UUID) -> CgroupHandle:
        if self._root is None or self._features is None:
            raise CgroupIsolationError("Per-job cgroup is unavailable")
        path = self._root / f"job-{job_id.hex}"
        try:
            self._file_system.mkdir(path)
            self._file_system.write_text(path / "memory.max", str(self._limits.memory_bytes))
            self._file_system.write_text(path / "pids.max", str(self._limits.pids))
            if self._features.swap_limit:
                self._file_system.write_text(path / "memory.swap.max", str(self._limits.swap_bytes))
            if self._features.oom_group:
                self._file_system.write_text(path / "memory.oom.group", "1")
            baseline = _read_memory_events(self._file_system, path / "memory.events")
        except OSError as exc:
            self._remove_probe(path)
            raise CgroupIsolationError(f"Cannot configure job cgroup {path}: {exc}") from exc
        return CgroupHandle(
            path,
            file_system=self._file_system,
            baseline=baseline,
            cleanup_timeout_seconds=self._limits.cleanup_timeout_seconds,
        )

    def _remove_probe(self, path: Path) -> None:
        if not self._file_system.exists(path):
            return
        for filename in CgroupHandle._FAKE_CONTROL_FILES:
            with contextlib.suppress(OSError):
                self._file_system.unlink(path / filename)
        with contextlib.suppress(OSError):
            self._file_system.rmdir(path)
        if self._file_system.exists(path):
            raise CgroupIsolationError(f"Cannot remove temporary cgroup {path}")

    def _trusted_status(self) -> ResourceIsolationStatus:
        return ResourceIsolationStatus(
            mode=self._mode,
            available=True,
            backend="external",
            cgroup_version=None,
            message=(
                "Trusted mode uses the external container/Pod memory, swap, and pids limits; "
                "no per-job cgroup is enforced"
            ),
            memory_limit_bytes=self._limits.memory_bytes,
            swap_limit_bytes=self._limits.swap_bytes,
            pids_limit=self._limits.pids,
            swap_limit_supported=False,
            oom_group_supported=False,
        )

    def _unavailable_status(self, message: str) -> ResourceIsolationStatus:
        return ResourceIsolationStatus(
            mode=self._mode,
            available=False,
            backend="unavailable",
            cgroup_version=None,
            message=message,
            memory_limit_bytes=self._limits.memory_bytes,
            swap_limit_bytes=self._limits.swap_bytes,
            pids_limit=self._limits.pids,
            swap_limit_supported=False,
            oom_group_supported=False,
        )


def _read_memory_events(file_system: CgroupFileSystem, path: Path) -> MemoryEvents:
    values: dict[str, int] = {}
    try:
        content = file_system.read_text(path)
    except OSError as exc:
        raise CgroupIsolationError(f"Cannot read cgroup memory events from {path}: {exc}") from exc
    for line in content.splitlines():
        key, separator, raw_value = line.partition(" ")
        if separator and raw_value.isdecimal():
            values[key] = int(raw_value)
    return MemoryEvents(
        low=values.get("low", 0),
        high=values.get("high", 0),
        maximum=values.get("max", 0),
        oom=values.get("oom", 0),
        oom_kill=values.get("oom_kill", 0),
        oom_group_kill=values.get("oom_group_kill", 0),
    )


def _find_cgroup2_mount(mountinfo: str) -> _CgroupMount | None:
    for line in mountinfo.splitlines():
        left, separator, right = line.partition(" - ")
        if not separator or not right.startswith("cgroup2 "):
            continue
        fields = left.split()
        if len(fields) < 5:
            continue
        return _CgroupMount(
            root=PurePosixPath(_decode_mount_field(fields[3])),
            mount_point=Path(_decode_mount_field(fields[4])),
        )
    return None


def _current_v2_relative_path(process_cgroup: str, mount_root: PurePosixPath) -> Path:
    current: PurePosixPath | None = None
    for line in process_cgroup.splitlines():
        hierarchy, controllers, raw_path = line.split(":", maxsplit=2)
        if hierarchy == "0" and not controllers:
            current = PurePosixPath(raw_path)
            break
    if current is None:
        raise CgroupIsolationError("Cannot determine the process cgroup v2 path")
    try:
        relative = current.relative_to(mount_root)
    except ValueError as exc:
        raise CgroupIsolationError(
            f"Process cgroup {current} is outside mounted cgroup root {mount_root}"
        ) from exc
    return Path(*relative.parts)


def _contains_cgroup_v1(mountinfo: str, process_cgroup: str) -> bool:
    return any(" - cgroup " in line for line in mountinfo.splitlines()) or any(
        line.split(":", maxsplit=2)[1]
        for line in process_cgroup.splitlines()
        if line.count(":") >= 2
    )


def _decode_mount_field(value: str) -> str:
    for encoded, decoded in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        value = value.replace(encoded, decoded)
    return value
