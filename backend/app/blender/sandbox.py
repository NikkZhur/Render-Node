"""Fail-closed sandbox policy and allowlisted worker environment."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import Environment
from app.jobs.types import ComputeDevice
from app.storage.jobs import contained_path


class SandboxUnavailableError(RuntimeError):
    pass


class SandboxPolicy:
    def __init__(self, *, environment: Environment, allow_unsandboxed: bool) -> None:
        self._environment = environment
        self._allow_unsandboxed = allow_unsandboxed

    @property
    def local_runner_allowed(self) -> bool:
        return self._environment is Environment.TEST or (
            self._environment is Environment.DEVELOPMENT and self._allow_unsandboxed
        )

    def ensure_startup_ready(self, *, scheduler_enabled: bool) -> None:
        if scheduler_enabled and self._environment is Environment.PRODUCTION:
            raise SandboxUnavailableError(
                "Production render sandbox is unavailable; local subprocess fallback is forbidden"
            )

    def ensure_local_runner_allowed(self) -> None:
        if not self.local_runner_allowed:
            raise SandboxUnavailableError(
                "Local runner is disabled; explicitly enable it only for development"
            )


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
