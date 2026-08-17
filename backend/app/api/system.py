import asyncio
from typing import cast

from fastapi import APIRouter, Request

from app.jobs.manager import JobManager
from app.system.metrics import SystemMetricsService
from app.system.schemas import (
    ResourceIsolationCapabilityResponse,
    RunnerCapabilityResponse,
    SystemCapabilitiesResponse,
    SystemMetricsResponse,
)

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/metrics", response_model=SystemMetricsResponse)
async def metrics(request: Request) -> SystemMetricsResponse:
    service = cast(SystemMetricsService, request.app.state.system_metrics_service)
    return await asyncio.shield(service.collect())


@router.get("/capabilities", response_model=SystemCapabilitiesResponse)
async def capabilities(request: Request) -> SystemCapabilitiesResponse:
    manager = cast(JobManager, request.app.state.job_manager)
    available = manager.runner_available
    isolation = manager.resource_isolation_status
    return SystemCapabilitiesResponse(
        runner=RunnerCapabilityResponse(
            available=available,
            mode=manager.runner_mode,
            message=(
                (
                    "Ready to render trusted Blender scenes; per-job cgroup v2 isolation is active"
                    if isolation.backend == "cgroup_v2"
                    else "Ready to render trusted Blender scenes; using external resource limits"
                )
                if available
                else manager.runner_unavailable_reason or "Render runner is unavailable"
            ),
            isolation=ResourceIsolationCapabilityResponse(
                mode=isolation.mode.value,
                available=isolation.available,
                backend=isolation.backend,
                cgroup_version=isolation.cgroup_version,
                memory_limit_bytes=isolation.memory_limit_bytes,
                swap_limit_bytes=isolation.swap_limit_bytes,
                pids_limit=isolation.pids_limit,
                swap_limit_supported=isolation.swap_limit_supported,
                oom_group_supported=isolation.oom_group_supported,
                message=isolation.message,
            ),
        )
    )
