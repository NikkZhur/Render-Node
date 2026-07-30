from uuid import UUID

import pytest
from fastapi import FastAPI, Query
from httpx import ASGITransport, AsyncClient

from app.storage.database import Database


async def test_health_and_readiness(client: AsyncClient) -> None:
    health_response = await client.get("/health")
    ready_response = await client.get("/ready")

    assert health_response.status_code == 200
    assert health_response.json() == {
        "status": "ok",
        "service": "Render Node",
        "version": "0.1.0",
    }
    assert ready_response.status_code == 200
    assert ready_response.json() == {"status": "ready", "checks": {"database": "up"}}
    UUID(health_response.headers["x-request-id"])


async def test_readiness_uses_error_envelope(client: AsyncClient, app: FastAPI) -> None:
    app.state.ready = False

    response = await client.get("/ready")

    assert response.status_code == 503
    payload = response.json()["error"]
    assert payload["code"] == "service_not_ready"
    assert payload["message"] == "Service is not ready"
    assert payload["details"] is None
    UUID(payload["request_id"])
    assert payload["request_id"] == response.headers["x-request-id"]


async def test_not_found_uses_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_404"


async def test_vite_origin_is_allowed_by_cors(client: AsyncClient) -> None:
    response = await client.options(
        "/health",
        headers={
            "origin": "http://localhost:5173",
            "access-control-request-method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


async def test_validation_error_uses_safe_error_envelope(client: AsyncClient, app: FastAPI) -> None:
    @app.get("/validated")
    async def validated(limit: int = Query(ge=1)) -> dict[str, int]:
        return {"limit": limit}

    response = await client.get("/validated", params={"limit": 0})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "request_validation_failed"
    assert error["details"] == [
        {
            "location": ["query", "limit"],
            "message": "Input should be greater than or equal to 1",
            "type": "greater_than_equal",
        }
    ]
    assert "input" not in error["details"][0]


async def test_unexpected_error_does_not_leak_exception(app: FastAPI) -> None:
    @app.get("/broken")
    async def broken() -> None:
        raise RuntimeError("sensitive internal detail")

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            response = await http_client.get("/broken")

    assert response.status_code == 500
    assert response.json()["error"]["message"] == "Internal server error"
    assert "sensitive" not in response.text


async def test_lifespan_disposes_database(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    disposed_databases: list[Database] = []

    async def record_dispose(database: Database) -> None:
        disposed_databases.append(database)
        await database.engine.dispose()

    monkeypatch.setattr(Database, "dispose", record_dispose)

    async with app.router.lifespan_context(app):
        database = app.state.database
        assert app.state.ready is True

    assert app.state.ready is False
    assert disposed_databases == [database]
