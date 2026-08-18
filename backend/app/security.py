"""Shared REST/WebSocket browser-session and service-client boundary."""

from __future__ import annotations

import hmac
import ipaddress
import re
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlsplit
from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.access import AccessManager
from app.schemas import ErrorBody, ErrorResponse

BROWSER_WEBSOCKET_PROTOCOL = "render-node-browser-v1"


def _header(scope: Scope, name: bytes) -> bytes | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return cast(bytes, value)
    return None


def _headers(scope: Scope, name: bytes) -> list[bytes]:
    return [cast(bytes, value) for key, value in scope.get("headers", []) if key.lower() == name]


@dataclass(frozen=True, slots=True)
class _Endpoint:
    hostname: str
    port: int


_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _normalized_hostname(value: str) -> str | None:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        try:
            hostname = value.encode("idna").decode("ascii").lower()
        except UnicodeError:
            return None
        if not hostname or len(hostname) > 253 or hostname.endswith("."):
            return None
        if not all(_DNS_LABEL.fullmatch(label) for label in hostname.split(".")):
            return None
        return hostname
    return address.compressed.lower()


def _parse_authority(value: str, *, default_port: int) -> _Endpoint | None:
    if not value or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value):
        return None
    if any(character in value for character in "/\\?#@,"):
        return None

    port_text: str | None = None
    if value.startswith("["):
        closing_bracket = value.find("]")
        if closing_bracket <= 1:
            return None
        hostname = value[1:closing_bracket]
        suffix = value[closing_bracket + 1 :]
        if suffix:
            if not suffix.startswith(":") or len(suffix) == 1:
                return None
            port_text = suffix[1:]
        try:
            if ipaddress.ip_address(hostname).version != 6:
                return None
        except ValueError:
            return None
    else:
        if value.count(":") > 1:
            return None
        hostname, separator, port_text = value.partition(":")
        if not separator:
            port_text = None

    normalized_hostname = _normalized_hostname(hostname)
    if normalized_hostname is None:
        return None
    if port_text is None:
        port = default_port
    elif not port_text.isascii() or not port_text.isdecimal():
        return None
    else:
        port = int(port_text)
        if not 1 <= port <= 65535:
            return None
    return _Endpoint(hostname=normalized_hostname, port=port)


def _origin_is_valid(origin_value: bytes, *, production: bool) -> bool:
    try:
        origin = origin_value.decode("ascii")
    except UnicodeDecodeError:
        return False
    if (
        origin.lower() == "null"
        or "?" in origin
        or "#" in origin
        or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in origin)
    ):
        return False
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or (production and scheme != "https"):
        return False
    if (
        not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False
    default_port = 443 if scheme == "https" else 80
    origin_endpoint = _parse_authority(parsed.netloc, default_port=default_port)
    return origin_endpoint is not None


def session_token_from_scope(scope: Scope, cookie_name: str) -> str | None:
    """Return one unambiguous opaque session cookie without logging it."""

    cookie_headers = _headers(scope, b"cookie")
    if len(cookie_headers) != 1:
        return None
    try:
        cookie = cookie_headers[0].decode("ascii")
    except UnicodeDecodeError:
        return None
    matches: list[str] = []
    for segment in cookie.split(";"):
        name, separator, value = segment.strip().partition("=")
        if separator and name == cookie_name:
            matches.append(value)
    return matches[0] if len(matches) == 1 else None


class ApiSecurityMiddleware:
    """Protect every versioned API transport with the same policy."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        api_prefix: str,
        auth_token: str | None,
        access_manager: AccessManager,
    ) -> None:
        self.app = app
        self._api_prefix = api_prefix
        self._token = auth_token.encode() if auth_token is not None else None
        self._access_manager = access_manager
        self._claim_path = f"{api_prefix}/access/claim"
        self._pair_path = f"{api_prefix}/access/pair"
        self._reset_path = f"{api_prefix}/access/reset"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"} or not self._is_api(scope):
            await self.app(scope, receive, send)
            return

        if self._is_browser_request(scope):
            denied_code = self._browser_context_error(scope)
            if denied_code is not None:
                await self._reject(
                    scope,
                    receive,
                    send,
                    status_code=403,
                    code=denied_code,
                    websocket_code=4403,
                )
                return

            path = str(scope.get("path", ""))
            if path == self._reset_path:
                await self._reject(
                    scope,
                    receive,
                    send,
                    status_code=403,
                    code="browser_reset_denied",
                    websocket_code=4403,
                )
                return
            if path in {self._claim_path, self._pair_path}:
                await self.app(scope, receive, send)
                return

            session_token = session_token_from_scope(scope, self._access_manager.cookie_name)
            if not await self._access_manager.authenticate(session_token):
                await self._reject(
                    scope,
                    receive,
                    send,
                    status_code=401,
                    code="browser_session_required",
                    websocket_code=4401,
                )
                return

            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        if path in {self._claim_path, self._pair_path}:
            await self._reject(
                scope,
                receive,
                send,
                status_code=403,
                code="browser_context_required",
                websocket_code=4403,
            )
            return

        service_auth_required = (
            self._token is not None or scope["type"] == "websocket" or path == self._reset_path
        )
        if service_auth_required and not self._authorized(scope):
            await self._reject(
                scope,
                receive,
                send,
                status_code=401,
                code="authentication_required",
                websocket_code=4401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            return

        await self.app(scope, receive, send)

    def _is_browser_request(self, scope: Scope) -> bool:
        browser_headers = (
            b"origin",
            b"sec-fetch-site",
            b"sec-fetch-mode",
            b"sec-fetch-dest",
            b"x-render-node-request",
        )
        return any(_headers(scope, name) for name in browser_headers) or bool(
            _headers(scope, b"cookie")
        )

    def _browser_context_error(self, scope: Scope) -> str | None:
        origins = _headers(scope, b"origin")
        if origins and (
            len(origins) != 1
            or not _origin_is_valid(origins[0], production=self._access_manager.production)
        ):
            return "origin_denied"

        fetch_sites = _headers(scope, b"sec-fetch-site")
        websocket_protocol_fallback = (
            scope["type"] == "websocket"
            and not fetch_sites
            and self._has_browser_websocket_protocol(scope)
        )
        if not websocket_protocol_fallback and (
            len(fetch_sites) != 1 or fetch_sites[0].lower() != b"same-origin"
        ):
            return "browser_context_denied"

        if scope["type"] == "websocket" and len(origins) != 1:
            return "origin_denied"

        if scope["type"] == "http" and scope.get("method") in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }:
            marker = _headers(scope, b"x-render-node-request")
            if len(marker) != 1 or marker[0] != b"1":
                return "browser_request_marker_required"
        return None

    @staticmethod
    def _has_browser_websocket_protocol(scope: Scope) -> bool:
        protocols = _headers(scope, b"sec-websocket-protocol")
        return len(protocols) == 1 and protocols[0].strip() == BROWSER_WEBSOCKET_PROTOCOL.encode(
            "ascii"
        )

    def _is_api(self, scope: Scope) -> bool:
        path = str(scope.get("path", ""))
        return path == self._api_prefix or path.startswith(f"{self._api_prefix}/")

    def _authorized(self, scope: Scope) -> bool:
        authorizations = _headers(scope, b"authorization")
        if len(authorizations) != 1:
            return False
        authorization = authorizations[0]
        parts = authorization.split(None, maxsplit=1)
        return (
            len(parts) == 2
            and parts[0].lower() == b"bearer"
            and hmac.compare_digest(parts[1], self._token or b"")
        )

    @staticmethod
    async def _reject(
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        status_code: int,
        code: str,
        websocket_code: int,
        headers: dict[str, str] | None = None,
    ) -> None:
        if scope["type"] == "websocket":
            reasons = {
                "authentication_required": "Authentication required",
                "browser_session_required": "Browser session required",
                "origin_denied": "Origin denied",
                "browser_context_denied": "Browser context denied",
                "browser_context_required": "Browser context required",
                "browser_request_marker_required": "Browser request marker required",
                "browser_reset_denied": "Browser reset denied",
            }
            await send(
                {
                    "type": "websocket.close",
                    "code": websocket_code,
                    "reason": reasons[code],
                }
            )
            return

        request_id = str(scope.get("state", {}).get("request_id", uuid4()))
        messages = {
            "authentication_required": "Authentication is required",
            "origin_denied": "Request origin is not allowed",
            "browser_session_required": "A paired browser session is required",
            "browser_context_denied": "Browser request context is not allowed",
            "browser_context_required": "A same-origin browser request is required",
            "browser_request_marker_required": "Browser mutation marker is required",
            "browser_reset_denied": "Access reset is restricted to local service clients",
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
