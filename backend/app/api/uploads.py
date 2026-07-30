"""Scene upload endpoint."""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, File, Request, UploadFile, status

from app.jobs.schemas import JobResponse
from app.jobs.upload_service import JobUploadService
from app.schemas import ErrorResponse

router = APIRouter(prefix="/jobs", tags=["uploads"])


def get_upload_service(request: Request) -> JobUploadService:
    return cast(JobUploadService, request.app.state.job_upload_service)


UploadFileDependency = Annotated[UploadFile, File()]
UploadServiceDependency = Annotated[JobUploadService, Depends(get_upload_service)]


@router.post(
    "/{job_id}/uploads",
    response_model=JobResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_413_CONTENT_TOO_LARGE: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
    },
)
async def upload_scene(
    job_id: UUID,
    file: UploadFileDependency,
    service: UploadServiceDependency,
) -> JobResponse:
    return JobResponse.model_validate(await service.upload(job_id, file))
