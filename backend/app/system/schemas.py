from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class StorageStatus(StrEnum):
    HEALTHY = "healthy"
    LOW_SPACE = "low_space"


class CpuMetricResponse(BaseModel):
    id: int = Field(ge=0)
    name: str
    cores: int = Field(ge=1)
    utilization_percent: float = Field(ge=0, le=100)
    memory_used_bytes: int = Field(ge=0)
    memory_total_bytes: int = Field(ge=0)
    temperature_celsius: float | None


class GpuMetricResponse(BaseModel):
    id: int = Field(ge=0)
    uuid: str
    name: str
    utilization_percent: float = Field(ge=0, le=100)
    memory_used_bytes: int = Field(ge=0)
    memory_total_bytes: int = Field(ge=0)
    temperature_celsius: float | None
    power_watts: float | None


class StorageMetricResponse(BaseModel):
    id: int = Field(ge=0)
    name: str
    mount_point: str
    total_bytes: int = Field(ge=0)
    free_bytes: int = Field(ge=0)
    read_mbps: float = Field(ge=0)
    write_mbps: float = Field(ge=0)
    status: StorageStatus


class SystemMetricsResponse(BaseModel):
    timestamp: datetime
    cpus: list[CpuMetricResponse]
    gpus: list[GpuMetricResponse]
    storages: list[StorageMetricResponse]
    websocket_clients: int = Field(ge=0)


class ResourceIsolationCapabilityResponse(BaseModel):
    mode: str
    available: bool
    backend: str
    cgroup_version: int | None
    memory_limit_bytes: int = Field(ge=1)
    swap_limit_bytes: int = Field(ge=0)
    pids_limit: int = Field(ge=1)
    swap_limit_supported: bool
    oom_group_supported: bool
    message: str


class RunnerCapabilityResponse(BaseModel):
    available: bool
    mode: str
    message: str
    isolation: ResourceIsolationCapabilityResponse


class SystemCapabilitiesResponse(BaseModel):
    runner: RunnerCapabilityResponse
