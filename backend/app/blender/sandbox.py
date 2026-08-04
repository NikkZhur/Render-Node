"""Fail-closed sandbox policy and allowlisted worker environment."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import Environment, RunnerMode
from app.jobs.types import ComputeDevice
from app.storage.jobs import contained_path


class SandboxUnavailableError(RuntimeError):
    pass


class SandboxPolicy:
    def __init__(self, *, environment: Environment, runner_mode: RunnerMode) -> None:
        self._environment = environment
        self._runner_mode = runner_mode

    @property
    def runner_mode(self) -> RunnerMode:
        return self._runner_mode

    @property
    def local_runner_allowed(self) -> bool:
        return self._environment is Environment.TEST or (
            self._environment is Environment.DEVELOPMENT
            and self._runner_mode is RunnerMode.LOCAL_TRUSTED
        )

    @property
    def unavailable_reason(self) -> str | None:
        if self.local_runner_allowed:
            return None
        if self._environment is Environment.PRODUCTION:
            return "Production render sandbox is unavailable on this node"
        return "Local trusted runner is disabled in configuration"

    def ensure_startup_ready(self, *, scheduler_enabled: bool) -> None:
        if scheduler_enabled and self._environment is Environment.PRODUCTION:
            raise SandboxUnavailableError(
                "Production render sandbox is unavailable; local subprocess fallback is forbidden"
            )

    def ensure_local_runner_allowed(self) -> None:
        reason = self.unavailable_reason
        if reason is not None:
            raise SandboxUnavailableError(reason)


async def build_worker_environment(
    job_directory: Path,
    *,
    device: ComputeDevice,
    gpu_ids: list[int],
) -> dict[str, str]:
    temp_root = contained_path(job_directory, job_directory / "temp")
    home = contained_path(temp_root, temp_root / "home")
    config = contained_path(temp_root, temp_root / "config")
    cache = contained_path(temp_root, temp_root / "cache")
    for path in (home, config, cache):
        await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
    environment = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config),
        "XDG_CACHE_HOME": str(cache),
        "TMPDIR": str(temp_root),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONPATH": "",
        "PYTHONHOME": "",
    }
    if device is not ComputeDevice.CPU:
        environment["CUDA_VISIBLE_DEVICES"] = ",".join(str(gpu_id) for gpu_id in gpu_ids)
    return environment
