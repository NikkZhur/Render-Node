"""Allowlisted worker environment for Blender subprocesses."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.jobs.types import ComputeDevice
from app.storage.jobs import contained_path


async def build_worker_environment(
    job_directory: Path,
    *,
    device: ComputeDevice,
    gpu_uuids: list[str],
    worker_index: int | None = None,
) -> dict[str, str]:
    temp_root = contained_path(job_directory, job_directory / "temp")
    if worker_index is not None:
        if worker_index < 0:
            raise ValueError("worker_index cannot be negative")
        temp_root = contained_path(temp_root, temp_root / f"gpu-{worker_index}")
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
        environment["CUDA_VISIBLE_DEVICES"] = ",".join(gpu_uuids)
    return environment
