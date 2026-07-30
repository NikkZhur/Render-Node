"""Liveness and readiness endpoints."""

from fastapi import APIRouter, Request, status
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import AppError
from app.config import Settings
from app.schemas import ErrorResponse, HealthResponse, ReadinessResponse
from app.storage.repository import DatabaseHealthRepository

router = APIRouter(tags=["service"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    return HealthResponse(service=settings.app_name, version=settings.app_version)


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse}},
)
async def readiness(request: Request) -> ReadinessResponse:
    if not request.app.state.ready:
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="service_not_ready",
            message="Service is not ready",
        )

    repository: DatabaseHealthRepository = request.app.state.database_health_repository
    try:
        database_is_available = await repository.is_available()
    except SQLAlchemyError as exc:
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="database_unavailable",
            message="Database is unavailable",
        ) from exc

    if not database_is_available:
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="database_unavailable",
            message="Database is unavailable",
        )
    return ReadinessResponse()
