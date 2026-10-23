"""Shared discovery and telemetry for physical NVIDIA GPUs and MIG devices."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GpuDevice:
    id: int
    uuid: str
    name: str
    memory_total_bytes: int


@dataclass(frozen=True, slots=True)
class GpuMetric:
    id: int
    uuid: str
    name: str
    utilization_percent: float | None
    memory_used_bytes: int
    memory_total_bytes: int
    temperature_celsius: float | None
    power_watts: float | None


class GpuReservationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GpuAllocation:
    """Stable NVML identities reserved for one logical render job."""

    devices: tuple[GpuDevice, ...]

    @property
    def uuids(self) -> tuple[str, ...]:
        return tuple(device.uuid for device in self.devices)


@dataclass(frozen=True, slots=True)
class _NvmlDevice:
    """A device sample whose handle is valid only inside the NVML session."""

    device: GpuDevice
    handle: Any
    memory_used_bytes: int


class GpuDiscovery:
    def __init__(self, nvml: Any | None = None) -> None:
        self._nvml = nvml
        self._lock = Lock()
        self._ids_by_uuid: dict[str, int] = {}

    def discover(self) -> list[GpuDevice]:
        with self._session() as nvml:
            if nvml is None:
                return []
            return sorted(
                (sample.device for sample in self._devices(nvml)), key=lambda item: item.id
            )

    def metrics(self) -> list[GpuMetric]:
        with self._session() as nvml:
            if nvml is None:
                return []
            return sorted(
                (self._metric(nvml, sample) for sample in self._devices(nvml)),
                key=lambda item: item.id,
            )

    def _metric(self, nvml: Any, sample: _NvmlDevice) -> GpuMetric:
        device, handle = sample.device, sample.handle
        return GpuMetric(
            id=device.id,
            uuid=device.uuid,
            name=device.name,
            utilization_percent=self._optional_metric(
                lambda: float(nvml.nvmlDeviceGetUtilizationRates(handle).gpu)
            ),
            memory_used_bytes=sample.memory_used_bytes,
            memory_total_bytes=device.memory_total_bytes,
            temperature_celsius=self._optional_metric(
                lambda: float(nvml.nvmlDeviceGetTemperature(handle, nvml.NVML_TEMPERATURE_GPU))
            ),
            power_watts=self._optional_metric(
                lambda: float(nvml.nvmlDeviceGetPowerUsage(handle)) / 1000
            ),
        )

    @contextlib.contextmanager
    def _session(self) -> Iterator[Any]:
        # Scheduler checks and metrics run in separate threads. Keep handle lifetimes
        # and the process-local UUID -> logical ID mapping under the same lock.
        with self._lock:
            nvml = self._module()
            if nvml is None:
                yield None
                return
            try:
                nvml.nvmlInit()
            except Exception:
                logger.debug("NVIDIA driver is unavailable", exc_info=True)
                yield None
                return
            try:
                yield nvml
            finally:
                with contextlib.suppress(Exception):
                    nvml.nvmlShutdown()

    def _devices(self, nvml: Any) -> Iterator[_NvmlDevice]:
        for handle in self._handles(nvml):
            try:
                uuid = self._text(nvml.nvmlDeviceGetUUID(handle))
                name = self._text(nvml.nvmlDeviceGetName(handle))
                memory = nvml.nvmlDeviceGetMemoryInfo(handle)
                total, used = int(memory.total), int(memory.used)
            except Exception:
                logger.debug("Skipping an inaccessible NVIDIA device", exc_info=True)
                continue
            yield _NvmlDevice(
                device=GpuDevice(
                    id=self._ids_by_uuid.setdefault(uuid, len(self._ids_by_uuid)),
                    uuid=uuid,
                    name=name,
                    memory_total_bytes=total,
                ),
                handle=handle,
                memory_used_bytes=used,
            )

    @staticmethod
    def _handles(nvml: Any) -> Iterator[Any]:
        try:
            count = int(nvml.nvmlDeviceGetCount())
        except Exception:
            logger.debug("NVIDIA inventory is unavailable", exc_info=True)
            return
        for index in range(count):
            try:
                parent = nvml.nvmlDeviceGetHandleByIndex(index)
                try:
                    mig_mode, _pending_mode = nvml.nvmlDeviceGetMigMode(parent)
                except (nvml.NVMLError_NotSupported, nvml.NVMLError_FunctionNotFound):
                    mig_mode = 0
                if mig_mode != nvml.NVML_DEVICE_MIG_ENABLE:
                    yield parent
                    continue
                # A MIG-enabled parent is not a render device. Query only its
                # accessible compute instances, including sparse MIG slot lists.
                mig_count = int(nvml.nvmlDeviceGetMaxMigDeviceCount(parent))
            except Exception:
                logger.debug("Skipping NVIDIA parent device %s", index, exc_info=True)
                continue
            for mig_index in range(mig_count):
                try:
                    yield nvml.nvmlDeviceGetMigDeviceHandleByIndex(parent, mig_index)
                except Exception:
                    logger.debug(
                        "MIG slot %s on NVIDIA device %s is unavailable",
                        mig_index,
                        index,
                        exc_info=True,
                    )

    @staticmethod
    def _optional_metric(query: Callable[[], float]) -> float | None:
        with contextlib.suppress(Exception):
            return query()
        return None

    def _module(self) -> Any | None:
        if self._nvml is not None:
            return self._nvml
        try:
            import pynvml
        except ImportError:
            return None
        return pynvml

    @staticmethod
    def _text(value: str | bytes) -> str:
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


class GpuScheduler:
    """Reserve every GPU assigned to a job by stable UUID until final cleanup."""

    def __init__(self, discovery: GpuDiscovery) -> None:
        self._discovery = discovery
        self._lock = asyncio.Lock()
        self._inventory_by_uuid: dict[str, GpuDevice] = {}
        self._reserved_uuids: set[str] = set()

    async def initialize(self) -> None:
        devices = await asyncio.to_thread(self._discovery.discover)
        self._inventory_by_uuid = self._validate_inventory(devices)

    async def reserve(self, gpu_ids: list[int]) -> GpuAllocation:
        async with self._lock:
            startup_by_index = {device.id: device for device in self._inventory_by_uuid.values()}
            try:
                requested_uuids = tuple(startup_by_index[gpu_id].uuid for gpu_id in gpu_ids)
            except KeyError as exc:
                raise GpuReservationError("Requested GPU is unavailable") from exc
            current_inventory = self._validate_inventory(
                await asyncio.to_thread(self._discovery.discover)
            )
            try:
                requested = tuple(current_inventory[uuid] for uuid in requested_uuids)
            except KeyError as exc:
                raise GpuReservationError("Requested GPU is unavailable") from exc
            requested_uuid_set = set(requested_uuids)
            if requested_uuid_set & self._reserved_uuids:
                raise GpuReservationError("Requested GPU is already reserved")
            self._reserved_uuids.update(requested_uuid_set)
            return GpuAllocation(requested)

    async def release(self, allocation: GpuAllocation) -> None:
        async with self._lock:
            self._reserved_uuids.difference_update(allocation.uuids)

    @property
    def reserved_uuids(self) -> frozenset[str]:
        return frozenset(self._reserved_uuids)

    @property
    def inventory(self) -> tuple[GpuDevice, ...]:
        return tuple(sorted(self._inventory_by_uuid.values(), key=lambda device: device.id))

    @staticmethod
    def _validate_inventory(devices: list[GpuDevice]) -> dict[str, GpuDevice]:
        inventory: dict[str, GpuDevice] = {}
        indexes: set[int] = set()
        for device in devices:
            if not device.uuid or device.uuid in inventory or device.id in indexes:
                raise GpuReservationError("NVML returned an invalid or duplicate GPU identity")
            inventory[device.uuid] = device
            indexes.add(device.id)
        return inventory
