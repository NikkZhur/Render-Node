"""Application errors and the shared HTTP error envelope."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.artifacts.exceptions import ArtifactNotFoundError, ArtifactRejectedError
from app.blender.exceptions import (
    BlenderConflictError,
    BlenderNotFoundError,
    BlenderOperationNotFoundError,
    BlenderRejectedError,
)
from app.jobs.exceptions import (
    JobConflictError,
    JobNotFoundError,
    ServiceError,
    UploadRejectedError,
    UploadTooLargeError,
)
from app.schemas import ErrorBody, ErrorResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: Mapping[str, Any] | Sequence[Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", uuid4()))


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            details=details,
            request_id=_request_id(request),
        )
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return _error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    if isinstance(
        exc,
        (
            JobNotFoundError,
            BlenderNotFoundError,
            BlenderOperationNotFoundError,
            ArtifactNotFoundError,
        ),
    ):
        status_code = 404
    elif isinstance(exc, UploadTooLargeError):
        status_code = 413
    elif isinstance(exc, (UploadRejectedError, BlenderRejectedError, ArtifactRejectedError)):
        status_code = 422
    elif isinstance(exc, (JobConflictError, BlenderConflictError)):
        status_code = 409
    else:
        status_code = 500
    return _error_response(
        request,
        status_code=status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {
            "location": [str(part) for part in error["loc"]],
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    return _error_response(
        request,
        status_code=422,
        code="request_validation_failed",
        message="Request validation failed",
        details=details,
    )


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    try:
        default_message = HTTPStatus(exc.status_code).phrase
    except ValueError:
        default_message = "HTTP error"
    message = exc.detail if isinstance(exc.detail, str) else default_message
    return _error_response(
        request,
        status_code=exc.status_code,
        code=f"http_{exc.status_code}",
        message=message,
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled application error", exc_info=exc)
    return _error_response(
        request,
        status_code=500,
        code="internal_server_error",
        message="Internal server error",
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ServiceError, service_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unexpected_error_handler)
