from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.blender.sandbox import SandboxUnavailableError
from app.config import Environment, Settings
from app.main import create_app

AUTH_TOKEN = "x" * 32
ALLOWED_ORIGIN = "https://render.example.com"


def production_settings(job_settings: Settings, **updates: object) -> Settings:
    values = job_settings.model_dump(
        exclude={"env", "auth_token", "allowed_origins", "render_scheduler_enabled"}
    )
    values.update(
        {
            "env": Environment.PRODUCTION,
            "auth_token": AUTH_TOKEN,
            "allowed_origins": [ALLOWED_ORIGIN],
            "render_scheduler_enabled": False,
            **updates,
        }
    )
    return Settings(**values)


@pytest.fixture
async def production_client(job_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(production_settings(job_settings))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="https://render.example.com"
        ) as client:
            yield client


async def test_production_api_requires_bearer_but_health_stays_public(
    production_client: AsyncClient,
) -> None:
    health = await production_client.get("/health")
    assert health.status_code == 200

    unauthorized = await production_client.get("/api/v1/jobs")
    assert unauthorized.status_code == 401
    assert unauthorized.headers["www-authenticate"] == "Bearer"
    assert unauthorized.json()["error"]["code"] == "authentication_required"
    assert unauthorized.json()["error"]["request_id"] == unauthorized.headers["x-request-id"]

    wrong_token = await production_client.get(
        "/api/v1/jobs", headers={"authorization": "Bearer wrong"}
    )
    assert wrong_token.status_code == 401

    authorized = await production_client.get(
        "/api/v1/jobs", headers={"authorization": f"Bearer {AUTH_TOKEN}"}
    )
    assert authorized.status_code == 200
    assert authorized.headers["x-content-type-options"] == "nosniff"
    assert authorized.headers["x-frame-options"] == "DENY"
    assert "max-age=31536000" in authorized.headers["strict-transport-security"]


async def test_production_disables_interactive_api_documentation(
    production_client: AsyncClient,
) -> None:
    assert (await production_client.get("/docs")).status_code == 404
    assert (await production_client.get("/redoc")).status_code == 404
    assert (await production_client.get("/openapi.json")).status_code == 404


async def test_origin_guard_and_cors_preflight(production_client: AsyncClient) -> None:
    denied = await production_client.get(
        "/api/v1/jobs",
        headers={
            "authorization": f"Bearer {AUTH_TOKEN}",
            "origin": "https://attacker.example",
        },
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "origin_denied"
    assert "access-control-allow-origin" not in denied.headers

    allowed = await production_client.get(
        "/api/v1/jobs",
        headers={
            "authorization": f"Bearer {AUTH_TOKEN}",
            "origin": ALLOWED_ORIGIN,
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == ALLOWED_ORIGIN

    preflight = await production_client.options(
        "/api/v1/jobs",
        headers={
            "origin": ALLOWED_ORIGIN,
            "access-control-request-method": "GET",
            "access-control-request-headers": "authorization",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert preflight.headers.get("access-control-allow-credentials") is None


def test_websocket_uses_auth_origin_and_message_limits(job_settings: Settings) -> None:
    app = create_app(production_settings(job_settings, websocket_message_max_kb=1))
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as unauthorized:
            with client.websocket_connect("/api/v1/events", headers={"origin": ALLOWED_ORIGIN}):
                pass
        assert unauthorized.value.code == 4401

        with pytest.raises(WebSocketDisconnect) as denied:
            with client.websocket_connect(
                "/api/v1/events",
                headers={
                    "authorization": f"Bearer {AUTH_TOKEN}",
                    "origin": "https://attacker.example",
                },
            ):
                pass
        assert denied.value.code == 4403

        with client.websocket_connect(
            "/api/v1/events",
            headers={
                "authorization": f"Bearer {AUTH_TOKEN}",
                "origin": ALLOWED_ORIGIN,
            },
        ) as websocket:
            assert websocket.receive_json()["type"] == "connection.ready"
            websocket.send_text("x" * 1025)
            with pytest.raises(WebSocketDisconnect) as oversized:
                websocket.receive_json()
            assert oversized.value.code == 1009


@pytest.mark.parametrize(
    "values",
    [
        {"allowed_origins": [ALLOWED_ORIGIN]},
        {"auth_token": "short", "allowed_origins": [ALLOWED_ORIGIN]},
        {"auth_token": AUTH_TOKEN, "allowed_origins": []},
        {"auth_token": AUTH_TOKEN, "allowed_origins": ["http://render.example.com"]},
        {"auth_token": AUTH_TOKEN, "allowed_origins": [f"{ALLOWED_ORIGIN}/api"]},
    ],
)
def test_production_rejects_insecure_boundary_configuration(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"env": Environment.PRODUCTION, **values})


async def test_production_scheduler_fails_closed_without_worker_sandbox(
    job_settings: Settings,
) -> None:
    app: FastAPI = create_app(production_settings(job_settings, render_scheduler_enabled=True))

    with pytest.raises(SandboxUnavailableError, match="sandbox is unavailable"):
        async with app.router.lifespan_context(app):
            pass


async def test_general_mutation_body_limit_is_enforced(job_settings: Settings) -> None:
    app = create_app(job_settings.model_copy(update={"max_api_request_mb": 0.001}))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/jobs",
                content=b"x" * 2048,
                headers={"content-type": "application/json"},
            )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_body_too_large"
