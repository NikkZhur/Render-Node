"""Schemas shared by HTTP endpoints and error handlers."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str


class ReadinessChecks(BaseModel):
    database: Literal["up"] = "up"


class ReadinessResponse(BaseModel):
    status: Literal["ready"] = "ready"
    checks: ReadinessChecks = Field(default_factory=ReadinessChecks)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody
