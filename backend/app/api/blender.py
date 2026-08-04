"""Thin HTTP adapter for Blender runtime management."""

from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, File, Request, Response, UploadFile, status

from app.blender.schemas import (
    OperationAccepted,
    OperationResponse,
    ReleaseResponse,
    RuntimeResponse,
)
from app.blender.service import BlenderService
from app.blender.types import SUPPORTED_VERSIONS, RuntimeState

router = APIRouter(prefix="/blender", tags=["blender"])


def _service(request: Request) -> BlenderService:
    return cast(BlenderService, request.app.state.blender_service)


@router.get("/versions", response_model=list[RuntimeResponse])
async def list_versions(request: Request) -> list[RuntimeResponse]:
    runtimes = await _service(request).list_runtimes()
    return [
        RuntimeResponse.model_validate(runtime).model_copy(
            update={"archive_filename": runtime.official_filename}
        )
        for runtime in runtimes
    ]


@router.get("/releases", response_model=list[ReleaseResponse])
async def list_releases(request: Request) -> list[ReleaseResponse]:
    service = _service(request)
    releases = await service.releases()
    runtimes = {runtime.version: runtime for runtime in await service.list_runtimes()}
    response: list[ReleaseResponse] = []
    for release in releases:
        runtime = runtimes.get(release.version)
        response.append(
            ReleaseResponse(
                version=release.version,
                filename=release.filename,
                channel="stable",
                supported=release.version in SUPPORTED_VERSIONS,
                downloaded=runtime is not None and runtime.archive_available,
                installed=runtime is not None and runtime.state is RuntimeState.INSTALLED,
                active=runtime.active if runtime else False,
                source=runtime.source if runtime else None,
            )
        )
    return response


@router.post(
    "/releases/{version}/download",
    response_model=OperationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def download_release(version: str, request: Request) -> OperationAccepted:
    operation = await _service(request).start_download(version)
    return OperationAccepted(operation_id=operation.id)


@router.post(
    "/versions/upload",
    response_model=OperationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_version(
    request: Request, file: Annotated[UploadFile, File()]
) -> OperationAccepted:
    operation = await _service(request).upload(file)
    return OperationAccepted(operation_id=operation.id)


@router.post(
    "/versions/{version}/install",
    response_model=OperationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def install_version(version: str, request: Request) -> OperationAccepted:
    operation = await _service(request).start_install(version)
    return OperationAccepted(operation_id=operation.id)


@router.post("/versions/{version}/activate", response_model=RuntimeResponse)
async def activate_version(version: str, request: Request) -> RuntimeResponse:
    runtime = await _service(request).activate(version)
    return RuntimeResponse.model_validate(runtime).model_copy(
        update={"archive_filename": runtime.official_filename}
    )


@router.delete("/versions/{version}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_version(version: str, request: Request) -> Response:
    await _service(request).delete(version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/operations/{operation_id}", response_model=OperationResponse)
async def get_operation(operation_id: UUID, request: Request) -> OperationResponse:
    return OperationResponse.model_validate(await _service(request).get_operation(operation_id))
