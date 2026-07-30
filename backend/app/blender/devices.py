"""NVIDIA GPU discovery that degrades to an empty inventory."""

from __future__ import annotations

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
