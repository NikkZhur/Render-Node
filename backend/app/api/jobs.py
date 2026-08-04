"""Job CRUD and action endpoints."""

from __future__ import annotations

from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.jobs.schemas import JobCreate, JobPageResponse, JobResponse, JobUpdate
from app.jobs.service import JobService
from app.schemas import ErrorResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
}


def get_job_service(request: Request) -> JobService:
    return cast(JobService, request.app.state.job_service)


JobServiceDependency = Annotated[JobService, Depends(get_job_service)]


@router.post(
    "",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
async def create_job(payload: JobCreate, service: JobServiceDependency) -> JobResponse:
    return JobResponse.model_validate(await service.create(payload))


@router.get("", response_model=list[JobResponse])
async def list_jobs(service: JobServiceDependency) -> list[JobResponse]:
    return [JobResponse.model_validate(job) for job in await service.list()]


@router.get("/page", response_model=JobPageResponse)
async def page_jobs(
    service: JobServiceDependency,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=10)] = 10,
) -> JobPageResponse:
    result = await service.page(page=page, page_size=page_size)
    return JobPageResponse(
        items=[JobResponse.model_validate(job) for job in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
        pages=result.pages,
    )


@router.get("/{job_id}", response_model=JobResponse, responses=ERROR_RESPONSES)
async def get_job(job_id: UUID, service: JobServiceDependency) -> JobResponse:
    return JobResponse.model_validate(await service.get(job_id))


@router.put("/{job_id}", response_model=JobResponse, responses=ERROR_RESPONSES)
async def update_job(
    job_id: UUID, payload: JobUpdate, service: JobServiceDependency
) -> JobResponse:
    return JobResponse.model_validate(await service.update(job_id, payload))


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ERROR_RESPONSES,
)
async def delete_job(job_id: UUID, service: JobServiceDependency) -> Response:
    await service.delete(job_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{job_id}/start", response_model=JobResponse, responses=ERROR_RESPONSES)
async def start_job(job_id: UUID, service: JobServiceDependency) -> JobResponse:
    return JobResponse.model_validate(await service.start(job_id))


@router.post("/{job_id}/cancel", response_model=JobResponse, responses=ERROR_RESPONSES)
async def cancel_job(job_id: UUID, service: JobServiceDependency) -> JobResponse:
    return JobResponse.model_validate(await service.cancel(job_id))


@router.post("/{job_id}/retry", response_model=JobResponse, responses=ERROR_RESPONSES)
async def retry_job(job_id: UUID, service: JobServiceDependency) -> JobResponse:
    return JobResponse.model_validate(await service.retry(job_id))


@router.post(
    "/{job_id}/rerender",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
async def rerender_job(job_id: UUID, service: JobServiceDependency) -> JobResponse:
    return JobResponse.model_validate(await service.rerender(job_id))
