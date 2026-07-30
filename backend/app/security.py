"""Shared REST/WebSocket authentication and browser-origin boundary."""

from __future__ import annotations

import hmac
from collections.abc import Sequence
from typing import cast
from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.schemas import ErrorBody, ErrorResponse


def _header(scope: Scope, name: bytes) -> bytes | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return cast(bytes, value)
    return None


class ApiSecurityMiddleware:
    """Protect every versioned API transport with the same policy."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        api_prefix: str,
        auth_token: str | None,
        allowed_origins: Sequence[str],
    ) -> None:
        self.app = app
        self._api_prefix = api_prefix
        self._token = auth_token.encode() if auth_token is not None else None
        self._allowed_origins = {origin.encode() for origin in allowed_origins}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"} or not self._is_api(scope):
            await self.app(scope, receive, send)
            return

        origin = _header(scope, b"origin")
        if origin is not None and origin not in self._allowed_origins:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4403, "reason": "Origin denied"})
            else:
                await self._reject_http(scope, receive, send, status_code=403, code="origin_denied")
            return

        if self._token is not None and not self._authorized(scope):
            if scope["type"] == "websocket":
                await send(
                    {"type": "websocket.close", "code": 4401, "reason": "Authentication required"}
                )
            else:
                await self._reject_http(
                    scope,
                    receive,
                    send,
                    status_code=401,
                    code="authentication_required",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return

        await self.app(scope, receive, send)

    def _is_api(self, scope: Scope) -> bool:
        path = str(scope.get("path", ""))
        return path == self._api_prefix or path.startswith(f"{self._api_prefix}/")

    def _authorized(self, scope: Scope) -> bool:
        authorization = _header(scope, b"authorization")
        if authorization is None:
            return False
        parts = authorization.split(None, maxsplit=1)
        return (
            len(parts) == 2
            and parts[0].lower() == b"bearer"
            and hmac.compare_digest(parts[1], self._token or b"")
        )

    @staticmethod
    async def _reject_http(
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        status_code: int,
        code: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        request_id = str(scope.get("state", {}).get("request_id", uuid4()))
        messages = {
            "authentication_required": "Authentication is required",
            "origin_denied": "Request origin is not allowed",
        }
        payload = ErrorResponse(
            error=ErrorBody(code=code, message=messages[code], request_id=request_id)
        )
        response = JSONResponse(
            status_code=status_code,
            content=payload.model_dump(mode="json"),
            headers=headers,
        )
        await response(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, production: bool) -> None:
        self.app = app
        self._production = production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                security_names = {
                    b"x-content-type-options",
                    b"x-frame-options",
                    b"referrer-policy",
                    b"permissions-policy",
                    b"strict-transport-security",
                }
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in security_names
                ]
                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    ]
                )
                if self._production:
                    headers.append(
                        (b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                    )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_security_headers)
