import asyncio
from typing import cast

from fastapi import APIRouter, Request

from app.system.metrics import SystemMetricsService
from app.system.schemas import SystemMetricsResponse

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/metrics", response_model=SystemMetricsResponse)
async def metrics(request: Request) -> SystemMetricsResponse:
    service = cast(SystemMetricsService, request.app.state.system_metrics_service)
    return await asyncio.shield(service.collect())
