"""First-browser claim, invitation, pairing, and local recovery endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Body, Request, Response, status
from pydantic import BaseModel, Field

from app.access import (
    AccessManager,
    DeviceLimitReachedError,
    InvalidInvitationError,
    NodeAlreadyClaimedError,
    SessionGrant,
)
from app.api.errors import AppError
from app.schemas import ErrorResponse
from app.security import session_token_from_scope

router = APIRouter(prefix="/access", tags=["access"])


class AccessStatusResponse(BaseModel):
    status: Literal["claimed", "paired"]
    session_count: int = Field(ge=1)
    max_sessions: int = Field(ge=1)
    expires_at: datetime


class PairRequest(BaseModel):
    token: str = Field(min_length=32, max_length=512)


class InvitationResponse(BaseModel):
    token: str
    expires_at: datetime


class ResetResponse(BaseModel):
    status: Literal["reset"] = "reset"


def _manager(request: Request) -> AccessManager:
    return cast(AccessManager, request.app.state.access_manager)


def _expires_at(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=UTC)


def _set_session_cookie(response: Response, manager: AccessManager, grant: SessionGrant) -> None:
    response.set_cookie(
        key=manager.cookie_name,
        value=grant.token,
        max_age=manager.remaining_session_seconds(grant.expires_at),
        httponly=True,
        secure=manager.production,
        samesite="strict",
        path="/",
    )


def _status(
    manager: AccessManager, grant: SessionGrant, value: Literal["claimed", "paired"]
) -> AccessStatusResponse:
    return AccessStatusResponse(
        status=value,
        session_count=grant.session_count,
        max_sessions=manager.max_sessions,
        expires_at=_expires_at(grant.expires_at),
    )


@router.post(
    "/claim",
    response_model=AccessStatusResponse,
    responses={status.HTTP_409_CONFLICT: {"model": ErrorResponse}},
)
async def claim(request: Request, response: Response) -> AccessStatusResponse:
    manager = _manager(request)
    existing_token = session_token_from_scope(request.scope, manager.cookie_name)
    try:
        grant = await manager.claim(existing_token)
    except NodeAlreadyClaimedError as exc:
        raise AppError(
            status_code=status.HTTP_409_CONFLICT,
            code="node_already_claimed",
            message="This Render Node is already paired with another browser",
        ) from exc
    _set_session_cookie(response, manager, grant)
    return _status(manager, grant, "claimed")


@router.post(
    "/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_409_CONFLICT: {"model": ErrorResponse}},
)
async def create_invitation(request: Request) -> InvitationResponse:
    manager = _manager(request)
    try:
        invitation = await manager.create_invitation()
    except DeviceLimitReachedError as exc:
        raise AppError(
            status_code=status.HTTP_409_CONFLICT,
            code="device_limit_reached",
            message="Device limit reached",
        ) from exc
    return InvitationResponse(
        token=invitation.token,
        expires_at=_expires_at(invitation.expires_at),
    )


@router.post(
    "/pair",
    response_model=AccessStatusResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    },
)
async def pair(
    request: Request,
    response: Response,
    payload: Annotated[PairRequest, Body()],
) -> AccessStatusResponse:
    manager = _manager(request)
    try:
        grant = await manager.redeem(payload.token)
    except InvalidInvitationError as exc:
        raise AppError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="pairing_invitation_invalid",
            message="The pairing link is invalid or has expired",
        ) from exc
    except DeviceLimitReachedError as exc:
        raise AppError(
            status_code=status.HTTP_409_CONFLICT,
            code="device_limit_reached",
            message="Device limit reached",
        ) from exc
    _set_session_cookie(response, manager, grant)
    return _status(manager, grant, "paired")


@router.post("/reset", response_model=ResetResponse)
async def reset_access(request: Request) -> ResetResponse:
    await _manager(request).reset()
    return ResetResponse()
