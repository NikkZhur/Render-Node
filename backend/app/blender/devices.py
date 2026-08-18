"""NVIDIA GPU discovery that degrades to an empty inventory."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any


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
    utilization_percent: float
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


class GpuDiscovery:
    def __init__(self, nvml: Any | None = None) -> None:
        self._nvml = nvml

    def discover(self) -> list[GpuDevice]:
        nvml = self._nvml
        if nvml is None:
            try:
                import pynvml as nvml_module
            except ImportError:
                return []
            nvml = nvml_module
        initialized = False
        try:
            nvml.nvmlInit()
            initialized = True
            devices: list[GpuDevice] = []
            for index in range(int(nvml.nvmlDeviceGetCount())):
                handle = nvml.nvmlDeviceGetHandleByIndex(index)
                name = nvml.nvmlDeviceGetName(handle)
                uuid = nvml.nvmlDeviceGetUUID(handle)
                memory = nvml.nvmlDeviceGetMemoryInfo(handle)
                devices.append(
                    GpuDevice(
                        id=index,
                        uuid=self._text(uuid),
                        name=self._text(name),
                        memory_total_bytes=int(memory.total),
                    )
                )
            return devices
        except Exception:
            return []
        finally:
            if initialized:
                with contextlib.suppress(Exception):
                    nvml.nvmlShutdown()

    def metrics(self) -> list[GpuMetric]:
        nvml = self._module()
        if nvml is None:
            return []
        initialized = False
        try:
            nvml.nvmlInit()
            initialized = True
            metrics: list[GpuMetric] = []
            for index in range(int(nvml.nvmlDeviceGetCount())):
                handle = nvml.nvmlDeviceGetHandleByIndex(index)
                memory = nvml.nvmlDeviceGetMemoryInfo(handle)
                utilization = nvml.nvmlDeviceGetUtilizationRates(handle)
                temperature: float | None = None
                power: float | None = None
                with contextlib.suppress(Exception):
                    temperature = float(
                        nvml.nvmlDeviceGetTemperature(handle, nvml.NVML_TEMPERATURE_GPU)
                    )
                with contextlib.suppress(Exception):
                    power = float(nvml.nvmlDeviceGetPowerUsage(handle)) / 1000
                metrics.append(
                    GpuMetric(
                        id=index,
                        uuid=self._text(nvml.nvmlDeviceGetUUID(handle)),
                        name=self._text(nvml.nvmlDeviceGetName(handle)),
                        utilization_percent=float(utilization.gpu),
                        memory_used_bytes=int(memory.used),
                        memory_total_bytes=int(memory.total),
                        temperature_celsius=temperature,
                        power_watts=power,
                    )
                )
            return metrics
        except Exception:
            return []
        finally:
            if initialized:
                with contextlib.suppress(Exception):
                    nvml.nvmlShutdown()

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
