import asyncio
from typing import cast

from fastapi import APIRouter, Request

from app.jobs.manager import JobManager
from app.system.metrics import SystemMetricsService
from app.system.schemas import (
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
    return SystemCapabilitiesResponse(
        runner=RunnerCapabilityResponse(
            available=available,
            mode=manager.runner_mode,
            message=(
                "Ready to render trusted Blender scenes"
                if available
                else manager.runner_unavailable_reason or "Render runner is unavailable"
            ),
        )
    )
