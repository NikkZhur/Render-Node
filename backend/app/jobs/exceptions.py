"""Errors raised below the HTTP boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class ServiceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class JobNotFoundError(ServiceError):
    def __init__(self) -> None:
        super().__init__("job_not_found", "Job was not found")


class JobConflictError(ServiceError):
    pass


class UploadRejectedError(ServiceError):
    pass


class UploadTooLargeError(UploadRejectedError):
    pass
