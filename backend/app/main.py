"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import register_error_handlers
from app.api.router import api_v1_router, router
from app.config import Environment, Settings
from app.lifespan import lifespan
from app.schemas import ErrorBody, ErrorResponse
from app.security import ApiSecurityMiddleware, SecurityHeadersMiddleware

MULTIPART_OVERHEAD_BYTES = 1024 * 1024


class RequestBodyLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        api_prefix: str,
        max_upload_bytes: int,
        max_blender_archive_bytes: int,
        max_api_request_bytes: int,
    ) -> None:
        self.app = app
        self._path_prefix = f"{api_prefix}/jobs/"
        self._job_max_request_bytes = max_upload_bytes + MULTIPART_OVERHEAD_BYTES
        self._blender_upload_path = f"{api_prefix}/blender/versions/upload"
        self._blender_max_request_bytes = max_blender_archive_bytes + MULTIPART_OVERHEAD_BYTES
        self._default_max_request_bytes = max_api_request_bytes

    def _request_limit(self, scope: Scope) -> int | None:
        path = str(scope.get("path", ""))
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            return None
        if path.startswith(self._path_prefix) and path.endswith("/uploads"):
            return self._job_max_request_bytes
        if path == self._blender_upload_path:
            return self._blender_max_request_bytes
        return self._default_max_request_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        max_request_bytes = self._request_limit(scope)
        if max_request_bytes is None:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > max_request_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                pass

        received_bytes = 0
        limit_exceeded = False
        response_messages: list[Message] = []

        async def limited_receive() -> Message:
            nonlocal limit_exceeded, received_bytes
            if limit_exceeded:
                return {"type": "http.request", "body": b"", "more_body": False}
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > max_request_bytes:
                    limit_exceeded = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def buffered_send(message: Message) -> None:
            response_messages.append(message)

        await self.app(scope, limited_receive, buffered_send)
        if limit_exceeded:
            await self._reject(scope, receive, send)
            return
        for message in response_messages:
            await send(message)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        request_id = str(scope.get("state", {}).get("request_id", uuid4()))
        payload = ErrorResponse(
            error=ErrorBody(
                code="request_body_too_large",
                message="Request body exceeds the configured size limit",
                request_id=request_id,
            )
        )
        response = JSONResponse(status_code=413, content=payload.model_dump(mode="json"))
        await response(scope, receive, send)


class RequestIdMiddleware:
    """Assign an untrusted-request-independent correlation ID to every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid4())
        state: MutableMapping[str, Any] = scope.setdefault("state", {})
        state["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_request_id)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        lifespan=lifespan,
        docs_url=None if resolved_settings.env is Environment.PRODUCTION else "/docs",
        redoc_url=None if resolved_settings.env is Environment.PRODUCTION else "/redoc",
        openapi_url=(None if resolved_settings.env is Environment.PRODUCTION else "/openapi.json"),
    )
    app.state.settings = resolved_settings
    app.state.ready = False

    register_error_handlers(app)
    app.include_router(router)
    app.include_router(api_v1_router, prefix=resolved_settings.api_prefix)
    app.add_middleware(
        ApiSecurityMiddleware,
        api_prefix=resolved_settings.api_prefix,
        auth_token=(
            resolved_settings.auth_token.get_secret_value()
            if resolved_settings.auth_token is not None
            else None
        ),
        allowed_origins=resolved_settings.cors_origins,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["Content-Disposition", "X-Request-ID"],
    )
    app.add_middleware(
        RequestBodyLimitMiddleware,
        api_prefix=resolved_settings.api_prefix,
        max_upload_bytes=resolved_settings.max_upload_bytes,
        max_blender_archive_bytes=resolved_settings.max_blender_archive_bytes,
        max_api_request_bytes=resolved_settings.max_api_request_bytes,
    )
    app.add_middleware(
        SecurityHeadersMiddleware,
        production=resolved_settings.env is Environment.PRODUCTION,
    )
    app.add_middleware(RequestIdMiddleware)
    return app


app = create_app()
