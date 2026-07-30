"""Safe HTTP access to registered render artifacts."""

from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import FileResponse

from app.artifacts.schemas import (
    ArtifactResponse,
    FramePageResponse,
    LogTailResponse,
)
from app.artifacts.service import ArtifactFile, ArtifactService
from app.artifacts.types import ArtifactKind

router = APIRouter(prefix="/jobs/{job_id}", tags=["artifacts"])


def get_artifact_service(request: Request) -> ArtifactService:
    return cast(ArtifactService, request.app.state.artifact_service)


ArtifactServiceDependency = Annotated[ArtifactService, Depends(get_artifact_service)]


def _file_response(
    delivery: ArtifactFile, *, inline: bool = False, cache: bool = False
) -> FileResponse:
    headers = {"X-Content-Type-Options": "nosniff"}
    if cache:
        headers["Cache-Control"] = "private, max-age=60"
    return FileResponse(
        delivery.path,
        media_type=delivery.content_type,
        filename=delivery.filename,
        content_disposition_type="inline" if inline else "attachment",
        headers=headers,
    )


@router.get("/artifacts", response_model=list[ArtifactResponse])
async def list_artifacts(
    job_id: UUID, service: ArtifactServiceDependency
) -> list[ArtifactResponse]:
    artifacts = await service.list_artifacts(job_id)
    return [
        ArtifactResponse(
            id=item.id,
            kind=item.kind,
            filename=item.filename,
            content_type=item.content_type,
            size_bytes=item.size_bytes,
            frame=item.frame,
            created_at=item.created_at,
            download_url=f"/api/v1/jobs/{job_id}/artifacts/{item.id}",
        )
        for item in artifacts
    ]


@router.get("/artifacts/{artifact_id}")
async def download_artifact(
    job_id: UUID, artifact_id: UUID, service: ArtifactServiceDependency
) -> FileResponse:
    return _file_response(await service.artifact_file(job_id, artifact_id))


@router.delete("/artifacts/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact(
    job_id: UUID, artifact_id: UUID, service: ArtifactServiceDependency
) -> Response:
    await service.delete(job_id, artifact_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/frames", response_model=FramePageResponse)
async def list_frames(
    job_id: UUID,
    service: ArtifactServiceDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=50),
) -> FramePageResponse:
    return await service.frame_page(job_id, page=page, page_size=page_size)


@router.get("/frames/{frame}/preview")
async def frame_preview(
    job_id: UUID, frame: int, service: ArtifactServiceDependency
) -> FileResponse:
    return _file_response(
        await service.frame_file(job_id, frame, ArtifactKind.FRAME_PREVIEW),
        inline=True,
        cache=True,
    )


@router.get("/frames/{frame}/original")
async def frame_original(
    job_id: UUID, frame: int, service: ArtifactServiceDependency
) -> FileResponse:
    return _file_response(await service.frame_file(job_id, frame, ArtifactKind.FRAME_ORIGINAL))


@router.get("/frames.zip")
async def frames_zip(job_id: UUID, service: ArtifactServiceDependency) -> FileResponse:
    return _file_response(await service.frames_zip(job_id))


@router.get("/logs/blender")
async def blender_log(job_id: UUID, service: ArtifactServiceDependency) -> FileResponse:
    return _file_response(await service.log_file(job_id))


@router.get("/logs/blender/tail", response_model=LogTailResponse)
async def blender_log_tail(
    job_id: UUID,
    service: ArtifactServiceDependency,
    lines: int = Query(default=100, ge=1, le=500),
) -> LogTailResponse:
    return LogTailResponse(lines=await service.log_tail(job_id, lines=lines))
