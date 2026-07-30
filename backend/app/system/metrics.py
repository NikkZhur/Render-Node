"""Bounded CPU/GPU/storage sampling and one-second realtime publication."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil

from app.blender.devices import GpuDiscovery
from app.events.hub import EventHub
from app.system.schemas import (
    CpuMetricResponse,
    GpuMetricResponse,
    StorageMetricResponse,
    StorageStatus,
    SystemMetricsResponse,
)

logger = logging.getLogger(__name__)


class SystemMetricsService:
    def __init__(
        self,
        gpu_discovery: GpuDiscovery,
        event_hub: EventHub,
        *,
        storage_paths: list[tuple[str, Path]],
        low_space_percent: float,
        low_space_bytes: int,
        interval_seconds: float,
    ) -> None:
        self._gpu_discovery = gpu_discovery
        self._events = event_hub
        self._storage_paths = storage_paths
        self._low_space_percent = low_space_percent
        self._low_space_bytes = low_space_bytes
        self._interval_seconds = interval_seconds
        self._sample_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._last_disk_sample: tuple[float, int, int] | None = None

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._publish_loop())

    async def shutdown(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def collect(self) -> SystemMetricsResponse:
        async with self._sample_lock:
            return await asyncio.to_thread(self._collect_sync)

    async def _publish_loop(self) -> None:
        while not self._stop.is_set():
            try:
                metrics = await self.collect()
                await self._events.publish(
                    "system.metrics_updated", metrics=metrics.model_dump(mode="json")
                )
            except Exception:
                # Telemetry is advisory and must never stop the API lifecycle.
                logger.exception("System metrics collection failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                pass

    def _collect_sync(self) -> SystemMetricsResponse:
        memory = psutil.virtual_memory()
        cpu = CpuMetricResponse(
            id=0,
            name=self._cpu_name(),
            cores=psutil.cpu_count(logical=True) or 1,
            utilization_percent=float(psutil.cpu_percent(interval=None)),
            memory_used_bytes=int(memory.used),
            memory_total_bytes=int(memory.total),
            temperature_celsius=self._cpu_temperature(),
        )
        gpus = [
            GpuMetricResponse(
                id=item.id,
                uuid=item.uuid,
                name=item.name,
                utilization_percent=item.utilization_percent,
                memory_used_bytes=item.memory_used_bytes,
                memory_total_bytes=item.memory_total_bytes,
                temperature_celsius=item.temperature_celsius,
                power_watts=item.power_watts,
            )
            for item in self._gpu_discovery.metrics()
        ]
        read_mbps, write_mbps = self._disk_throughput()
        storages: list[StorageMetricResponse] = []
        devices_seen: set[int] = set()
        for name, configured_path in self._storage_paths:
            path = configured_path.resolve()
            try:
                device = path.stat().st_dev
                usage = shutil.disk_usage(path)
            except OSError:
                continue
            if device in devices_seen:
                continue
            devices_seen.add(device)
            free_percent = usage.free / usage.total * 100 if usage.total else 0
            status = (
                StorageStatus.LOW_SPACE
                if free_percent < self._low_space_percent or usage.free < self._low_space_bytes
                else StorageStatus.HEALTHY
            )
            storages.append(
                StorageMetricResponse(
                    id=len(storages),
                    name=name,
                    mount_point=str(path),
                    total_bytes=usage.total,
                    free_bytes=usage.free,
                    read_mbps=read_mbps,
                    write_mbps=write_mbps,
                    status=status,
                )
            )
        return SystemMetricsResponse(
            timestamp=datetime.now(UTC),
            cpus=[cpu],
            gpus=gpus,
            storages=storages,
            websocket_clients=self._events.client_count,
        )

    def _disk_throughput(self) -> tuple[float, float]:
        counters = psutil.disk_io_counters()
        now = time.monotonic()
        if counters is None:
            return 0.0, 0.0
        current = (now, int(counters.read_bytes), int(counters.write_bytes))
        previous = self._last_disk_sample
        self._last_disk_sample = current
        if previous is None or now <= previous[0]:
            return 0.0, 0.0
        elapsed = now - previous[0]
        return (
            max(0.0, (current[1] - previous[1]) / elapsed / 1024**2),
            max(0.0, (current[2] - previous[2]) / elapsed / 1024**2),
        )

    @staticmethod
    def _cpu_name() -> str:
        with contextlib.suppress(OSError):
            for line in Path("/proc/cpuinfo").read_text(errors="replace").splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", maxsplit=1)[1].strip()
        return "CPU"

    @staticmethod
    def _cpu_temperature() -> float | None:
        with contextlib.suppress(Exception):
            sensors = psutil.sensors_temperatures(fahrenheit=False)
            for entries in sensors.values():
                for entry in entries:
                    if entry.current is not None and 0 <= entry.current <= 150:
                        return float(entry.current)
        return None
