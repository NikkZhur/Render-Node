from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import ValidationError
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.access import PRODUCTION_COOKIE_NAME, AccessManager
from app.config import Environment, ResourceIsolationMode, Settings
from app.main import create_app
from app.security import BROWSER_WEBSOCKET_PROTOCOL, _origin_is_valid

AUTH_TOKEN = "x" * 32
PUBLIC_ORIGIN = "https://panel.arbitrary-cloud.example"
WEBSOCKET_ORIGIN = "https://testserver"
MARKER_HEADER = "x-render-node-request"


def production_settings(job_settings: Settings, **updates: object) -> Settings:
    values = job_settings.model_dump(exclude={"env", "auth_token", "render_scheduler_enabled"})
    values.update(
        {
            "env": Environment.PRODUCTION,
            "auth_token": AUTH_TOKEN,
            "render_scheduler_enabled": False,
            "resource_isolation_mode": ResourceIsolationMode.STRICT,
            **updates,
        }
    )
    return Settings(**values)


def browser_headers(
    *,
    mutation: bool = False,
    origin: str | None = PUBLIC_ORIGIN,
    fetch_site: str = "same-origin",
) -> dict[str, str]:
    headers = {"sec-fetch-site": fetch_site}
    if origin is not None:
        headers["origin"] = origin
    if mutation:
        headers[MARKER_HEADER] = "1"
    return headers


async def claim_browser(client: AsyncClient, *, origin: str = PUBLIC_ORIGIN) -> Response:
    return await client.post(
        "/api/v1/access/claim",
        headers={
            **browser_headers(mutation=True, origin=origin),
            # A gateway may rewrite this upstream authority without weakening pairing.
            "host": "127.0.0.1:8000",
        },
    )


@pytest.fixture
async def production_app(job_settings: Settings) -> AsyncIterator[FastAPI]:
    app = create_app(production_settings(job_settings))
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def production_client(production_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=production_app)
    async with AsyncClient(
        transport=transport,
        base_url="https://panel.arbitrary-cloud.example",
    ) as client:
        yield client


async def test_first_deliberate_browser_claim_is_idempotent_and_probes_do_not_claim(
    production_app: FastAPI,
    production_client: AsyncClient,
) -> None:
    manager: AccessManager = production_app.state.access_manager
    for method, path in (
        ("GET", "/health"),
        ("GET", "/ready"),
        ("HEAD", "/health"),
        ("GET", "/"),
        ("HEAD", "/api/v1/access/claim"),
        ("OPTIONS", "/api/v1/access/claim"),
    ):
        await production_client.request(method, path)
    assert (await manager.snapshot()).claimed is False

    claimed = await claim_browser(production_client)
    assert claimed.status_code == 200
    assert claimed.json()["status"] == "claimed"
    assert claimed.json()["session_count"] == 1
    assert PRODUCTION_COOKIE_NAME in production_client.cookies

    jobs = await production_client.get("/api/v1/jobs", headers=browser_headers(origin=None))
    assert jobs.status_code == 200

    repeated = await claim_browser(production_client)
    assert repeated.status_code == 200
    assert repeated.json()["session_count"] == 1
    assert (await manager.snapshot()).session_count == 1


async def test_two_concurrent_unknown_browsers_cannot_both_claim(
    job_settings: Settings,
) -> None:
    app = create_app(production_settings(job_settings))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with (
            AsyncClient(transport=transport, base_url=PUBLIC_ORIGIN) as first,
            AsyncClient(transport=transport, base_url=PUBLIC_ORIGIN) as second,
        ):
            responses = await asyncio.gather(claim_browser(first), claim_browser(second))

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert [
        response.json()["error"]["code"] for response in responses if response.status_code == 409
    ] == ["node_already_claimed"]


async def test_unknown_second_browser_cannot_read_mutate_or_bypass_with_bearer(
    production_app: FastAPI,
    production_client: AsyncClient,
) -> None:
    assert (await claim_browser(production_client)).status_code == 200
    transport = ASGITransport(app=production_app)
    async with AsyncClient(transport=transport, base_url=PUBLIC_ORIGIN) as unknown:
        second_claim = await claim_browser(unknown)
        read = await unknown.get("/api/v1/jobs", headers=browser_headers(origin=None))
        mutation = await unknown.post(
            "/api/v1/jobs",
            headers=browser_headers(mutation=True),
            json={},
        )
        bearer_bypass = await unknown.get(
            "/api/v1/jobs",
            headers={
                **browser_headers(origin=None),
                "authorization": f"Bearer {AUTH_TOKEN}",
            },
        )

    assert second_claim.status_code == 409
    assert second_claim.json()["error"]["code"] == "node_already_claimed"
    assert read.status_code == mutation.status_code == bearer_bypass.status_code == 401
    assert read.json()["error"]["code"] == "browser_session_required"


async def test_browser_session_cookie_flags_differ_between_production_and_development(
    job_settings: Settings,
) -> None:
    production_app = create_app(production_settings(job_settings))
    async with production_app.router.lifespan_context(production_app):
        async with AsyncClient(
            transport=ASGITransport(app=production_app), base_url=PUBLIC_ORIGIN
        ) as client:
            production_cookie = (await claim_browser(client)).headers["set-cookie"]

    development_app = create_app(job_settings)
    async with development_app.router.lifespan_context(development_app):
        async with AsyncClient(
            transport=ASGITransport(app=development_app), base_url="http://localhost:5173"
        ) as client:
            development_cookie = (
                await client.post(
                    "/api/v1/access/claim",
                    headers=browser_headers(mutation=True, origin="http://localhost:5173"),
                )
            ).headers["set-cookie"]

    assert production_cookie.startswith(f"{PRODUCTION_COOKIE_NAME}=")
    assert "HttpOnly" in production_cookie
    assert "Path=/" in production_cookie
    assert "SameSite=strict" in production_cookie
    assert "Secure" in production_cookie
    assert development_cookie.startswith("render_node_dev_session=")
    assert "HttpOnly" in development_cookie
    assert "SameSite=strict" in development_cookie
    assert "Secure" not in development_cookie


async def test_expired_unknown_and_forged_browser_sessions_are_rejected(
    job_settings: Settings,
) -> None:
    now = [1_000.0]
    manager = AccessManager(production=True, clock=lambda: now[0], session_lifetime_seconds=60)
    app = create_app(production_settings(job_settings), access_manager=manager)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url=PUBLIC_ORIGIN) as client:
            assert (await claim_browser(client)).status_code == 200
            now[0] += 61
            expired = await client.get("/api/v1/jobs", headers=browser_headers(origin=None))
            reclaimed = await claim_browser(client)

        async with AsyncClient(transport=transport, base_url=PUBLIC_ORIGIN) as forged:
            forged.cookies.set(PRODUCTION_COOKIE_NAME, "forged-session-token")
            unknown = await forged.get("/api/v1/jobs", headers=browser_headers(origin=None))

    assert expired.status_code == unknown.status_code == 401
    assert reclaimed.status_code == 409
    assert (await manager.snapshot()).claimed is True
    assert (await manager.snapshot()).session_count == 0


@pytest.mark.parametrize("fetch_site", ["cross-site", "same-site", "none"])
async def test_fetch_metadata_denies_requests_even_with_valid_session(
    production_client: AsyncClient,
    fetch_site: str,
) -> None:
    assert (await claim_browser(production_client)).status_code == 200
    response = await production_client.get(
        "/api/v1/jobs", headers=browser_headers(fetch_site=fetch_site)
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "browser_context_denied"


async def test_browser_mutation_requires_fixed_custom_header(
    production_client: AsyncClient,
) -> None:
    missing_claim_marker = await production_client.post(
        "/api/v1/access/claim",
        headers=browser_headers(mutation=False),
    )
    assert missing_claim_marker.status_code == 403
    assert missing_claim_marker.json()["error"]["code"] == "browser_request_marker_required"

    assert (await claim_browser(production_client)).status_code == 200
    mutation = await production_client.post(
        "/api/v1/jobs",
        headers=browser_headers(mutation=False),
        json={},
    )
    assert mutation.status_code == 403
    assert mutation.json()["error"]["code"] == "browser_request_marker_required"


@pytest.mark.parametrize(
    "origin",
    [
        "https://pod-1234-5173.proxy.runpod.net",
        "https://panel.other-provider.example:8443",
        "https://RENDER.Example.COM",
        "https://127.0.0.1:9443",
        "https://localhost",
        "https://[2001:db8::1]:9443",
    ],
)
async def test_valid_public_origin_does_not_depend_on_rewritten_upstream_host(
    production_client: AsyncClient,
    origin: str,
) -> None:
    response = await claim_browser(production_client, origin=origin)
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    "origin",
    [
        "null",
        "not an origin",
        "ftp://render.example.com",
        "http://render.example.com",
        "https://user@render.example.com",
        "https://user:password@render.example.com",
        "https://render.example.com/",
        "https://render.example.com/path",
        "https://render.example.com?query",
        "https://render.example.com?",
        "https://render.example.com#fragment",
        "https://render.example.com#",
        "https://render.example.com:",
        "https://render.example.com,https://attacker.example",
        "https://[::1",
    ],
)
async def test_malformed_ambiguous_and_insecure_production_origins_are_denied(
    production_client: AsyncClient,
    origin: str,
) -> None:
    response = await production_client.post(
        "/api/v1/access/claim",
        headers=browser_headers(mutation=True, origin=origin),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_denied"


async def test_duplicate_origin_headers_are_denied(production_client: AsyncClient) -> None:
    response = await production_client.post(
        "/api/v1/access/claim",
        headers=[
            ("sec-fetch-site", "same-origin"),
            (MARKER_HEADER, "1"),
            ("origin", PUBLIC_ORIGIN),
            ("origin", PUBLIC_ORIGIN),
        ],
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_denied"


@pytest.mark.parametrize(
    "origin",
    [
        b" https://render.example.com",
        b"https://render.example.com\t",
        b"https://render.example.com\x7f",
    ],
)
def test_origin_parser_rejects_raw_whitespace_and_controls(origin: bytes) -> None:
    assert not _origin_is_valid(origin, production=True)


async def test_forwarded_headers_do_not_participate_in_browser_access(
    production_client: AsyncClient,
) -> None:
    response = await production_client.post(
        "/api/v1/access/claim",
        headers={
            **browser_headers(mutation=True),
            "host": "127.0.0.1:8000",
            "x-forwarded-host": "attacker.example",
            "x-forwarded-proto": "http",
            "forwarded": "host=attacker.example;proto=http",
        },
    )
    assert response.status_code == 200


async def test_originless_service_client_uses_bearer_and_health_stays_public(
    production_client: AsyncClient,
) -> None:
    health = await production_client.get("/health")
    unauthorized = await production_client.get("/api/v1/jobs")
    wrong_token = await production_client.get(
        "/api/v1/jobs", headers={"authorization": "Bearer wrong"}
    )
    authorized = await production_client.get(
        "/api/v1/jobs", headers={"authorization": f"Bearer {AUTH_TOKEN}"}
    )

    assert health.status_code == 200
    assert unauthorized.status_code == wrong_token.status_code == 401
    assert unauthorized.headers["www-authenticate"] == "Bearer"
    assert unauthorized.json()["error"]["code"] == "authentication_required"
    assert unauthorized.json()["error"]["request_id"] == unauthorized.headers["x-request-id"]
    assert authorized.status_code == 200
    assert authorized.headers["x-content-type-options"] == "nosniff"
    assert authorized.headers["x-frame-options"] == "DENY"
    assert "max-age=31536000" in authorized.headers["strict-transport-security"]


async def test_invitations_require_session_expire_and_are_single_use(
    job_settings: Settings,
) -> None:
    now = [2_000.0]
    manager = AccessManager(production=True, clock=lambda: now[0])
    app = create_app(production_settings(job_settings), access_manager=manager)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url=PUBLIC_ORIGIN) as first:
            unauthorized = await first.post(
                "/api/v1/access/invitations",
                headers=browser_headers(mutation=True),
            )
            assert unauthorized.status_code == 401
            assert (await claim_browser(first)).status_code == 200

            expired_invitation = await first.post(
                "/api/v1/access/invitations",
                headers=browser_headers(mutation=True),
            )
            now[0] += 301
            async with AsyncClient(transport=transport, base_url=PUBLIC_ORIGIN) as expired_client:
                expired = await expired_client.post(
                    "/api/v1/access/pair",
                    headers=browser_headers(mutation=True),
                    json={"token": expired_invitation.json()["token"]},
                )
            assert expired.status_code == 400
            assert expired.json()["error"]["code"] == "pairing_invitation_invalid"

            invitation = await first.post(
                "/api/v1/access/invitations",
                headers=browser_headers(mutation=True),
            )
            token = invitation.json()["token"]
            async with (
                AsyncClient(transport=transport, base_url=PUBLIC_ORIGIN) as second,
                AsyncClient(transport=transport, base_url=PUBLIC_ORIGIN) as third,
            ):
                paired = await asyncio.gather(
                    second.post(
                        "/api/v1/access/pair",
                        headers=browser_headers(mutation=True),
                        json={"token": token},
                    ),
                    third.post(
                        "/api/v1/access/pair",
                        headers=browser_headers(mutation=True),
                        json={"token": token},
                    ),
                )

    assert sorted(response.status_code for response in paired) == [200, 400]
    assert (await manager.snapshot()).session_count == 2


async def test_browser_session_limit_is_five(production_app: FastAPI) -> None:
    transport = ASGITransport(app=production_app)
    clients = [AsyncClient(transport=transport, base_url=PUBLIC_ORIGIN) for _ in range(5)]
    try:
        assert (await claim_browser(clients[0])).status_code == 200
        for client in clients[1:]:
            invitation = await clients[0].post(
                "/api/v1/access/invitations",
                headers=browser_headers(mutation=True),
            )
            paired = await client.post(
                "/api/v1/access/pair",
                headers=browser_headers(mutation=True),
                json={"token": invitation.json()["token"]},
            )
            assert paired.status_code == 200

        limit = await clients[0].post(
            "/api/v1/access/invitations",
            headers=browser_headers(mutation=True),
        )
        assert limit.status_code == 409
        assert limit.json()["error"]["code"] == "device_limit_reached"
    finally:
        await asyncio.gather(*(client.aclose() for client in clients))


async def test_service_reset_invalidates_access_without_changing_jobs(
    production_app: FastAPI,
) -> None:
    transport = ASGITransport(app=production_app)
    service_headers = {"authorization": f"Bearer {AUTH_TOKEN}"}
    async with (
        AsyncClient(transport=transport, base_url=PUBLIC_ORIGIN) as browser,
        AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as service,
    ):
        activated = await service.post(
            "/api/v1/blender/versions/5.2.0/activate",
            headers=service_headers,
        )
        assert activated.status_code == 200
        created = await service.post(
            "/api/v1/jobs",
            headers=service_headers,
            json={
                "name": "Survives access reset",
                "blender_version": "5.2.0",
                "engine": "BLENDER_EEVEE",
                "device": "CPU",
                "gpu_ids": [],
                "frame_mode": "SINGLE",
                "frame_start": 1,
                "frame_end": None,
            },
        )
        assert created.status_code == 201
        assert (await claim_browser(browser)).status_code == 200
        invitation = await browser.post(
            "/api/v1/access/invitations",
            headers=browser_headers(mutation=True),
        )
        assert invitation.status_code == 201

        browser_reset = await browser.post(
            "/api/v1/access/reset",
            headers={
                **browser_headers(mutation=True),
                "authorization": f"Bearer {AUTH_TOKEN}",
            },
        )
        reset = await service.post("/api/v1/access/reset", headers=service_headers)
        jobs = await service.get("/api/v1/jobs", headers=service_headers)
        old_session = await browser.get("/api/v1/jobs", headers=browser_headers(origin=None))
        reclaimed = await claim_browser(browser)

    assert browser_reset.status_code == 403
    assert reset.status_code == 200
    assert reset.json() == {"status": "reset"}
    assert [job["id"] for job in jobs.json()] == [created.json()["id"]]
    assert old_session.status_code == 401
    assert reclaimed.status_code == 200


def test_websocket_browser_sessions_service_auth_and_message_limits(
    job_settings: Settings,
) -> None:
    app = create_app(production_settings(job_settings, websocket_message_max_kb=1))
    browser_ws_headers = {
        "origin": WEBSOCKET_ORIGIN,
        "sec-fetch-site": "same-origin",
    }
    with TestClient(app, base_url=WEBSOCKET_ORIGIN) as client:
        with pytest.raises(WebSocketDisconnect) as missing_session:
            with client.websocket_connect("/api/v1/events", headers=browser_ws_headers):
                pass
        assert missing_session.value.code == 4401

        with client.websocket_connect(
            "/api/v1/events",
            headers={"authorization": f"Bearer {AUTH_TOKEN}"},
        ) as service_socket:
            assert service_socket.receive_json()["type"] == "connection.ready"

        claim = client.post(
            "/api/v1/access/claim",
            headers=browser_headers(mutation=True, origin=WEBSOCKET_ORIGIN),
        )
        assert claim.status_code == 200
        session_token = client.cookies.get(PRODUCTION_COOKIE_NAME)
        assert session_token is not None
        paired_ws_headers = {
            **browser_ws_headers,
            "cookie": f"{PRODUCTION_COOKIE_NAME}={session_token}",
        }

        with pytest.raises(WebSocketDisconnect) as cross_site:
            with client.websocket_connect(
                "/api/v1/events",
                headers={**paired_ws_headers, "sec-fetch-site": "cross-site"},
            ):
                pass
        assert cross_site.value.code == 4403

        with pytest.raises(WebSocketDisconnect) as malformed:
            with client.websocket_connect(
                "/api/v1/events",
                headers={**paired_ws_headers, "origin": f"{WEBSOCKET_ORIGIN}/path"},
            ):
                pass
        assert malformed.value.code == 4403

        with pytest.raises(WebSocketDisconnect) as missing_origin:
            with client.websocket_connect(
                "/api/v1/events", headers={"sec-fetch-site": "same-origin"}
            ):
                pass
        assert missing_origin.value.code == 4403

        with pytest.raises(WebSocketDisconnect) as missing_context:
            with client.websocket_connect(
                "/api/v1/events",
                headers={
                    "origin": WEBSOCKET_ORIGIN,
                    "cookie": f"{PRODUCTION_COOKIE_NAME}={session_token}",
                },
            ):
                pass
        assert missing_context.value.code == 4403

        with client.websocket_connect(
            "/api/v1/events",
            headers={
                "origin": WEBSOCKET_ORIGIN,
                "cookie": f"{PRODUCTION_COOKIE_NAME}={session_token}",
            },
            subprotocols=[BROWSER_WEBSOCKET_PROTOCOL],
        ) as chromium_compatible_socket:
            assert chromium_compatible_socket.receive_json()["type"] == "connection.ready"
            assert chromium_compatible_socket.accepted_subprotocol == BROWSER_WEBSOCKET_PROTOCOL

        with client.websocket_connect("/api/v1/events", headers=paired_ws_headers) as websocket:
            assert websocket.receive_json()["type"] == "connection.ready"
            websocket.send_text("x" * 1025)
            with pytest.raises(WebSocketDisconnect) as oversized:
                while True:
                    websocket.receive_json()
            assert oversized.value.code == 1009


def test_websocket_without_origin_requires_configured_bearer(job_settings: Settings) -> None:
    app = create_app(job_settings)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as unauthorized:
            with client.websocket_connect("/api/v1/events"):
                pass
        assert unauthorized.value.code == 4401


async def test_production_disables_interactive_api_documentation(
    production_client: AsyncClient,
) -> None:
    assert (await production_client.get("/docs")).status_code == 404
    assert (await production_client.get("/redoc")).status_code == 404
    assert (await production_client.get("/openapi.json")).status_code == 404


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"auth_token": "short"},
    ],
)
def test_production_rejects_insecure_boundary_configuration(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                "env": Environment.PRODUCTION,
                "resource_isolation_mode": ResourceIsolationMode.STRICT,
                **values,
            }
        )


def test_production_settings_require_no_origin_configuration() -> None:
    settings = Settings(
        env=Environment.PRODUCTION,
        auth_token=AUTH_TOKEN,
        resource_isolation_mode=ResourceIsolationMode.STRICT,
    )
    assert not hasattr(settings, "allowed_origins")


async def test_production_rendering_requires_only_the_resource_boundary(
    job_settings: Settings,
) -> None:
    app = create_app(
        production_settings(
            job_settings,
            render_scheduler_enabled=True,
            cgroup_root=job_settings.workspace / "not-a-delegated-cgroup",
        )
    )
    async with app.router.lifespan_context(app):
        assert app.state.ready is True
        assert app.state.job_manager.runner_mode == "local"
        assert app.state.job_manager.runner_available is False
        assert "cgroup" in app.state.job_manager.runner_unavailable_reason.lower()


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
