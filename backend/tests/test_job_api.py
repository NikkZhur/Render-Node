from __future__ import annotations

from uuid import UUID

from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app


def job_payload(name: str = "Studio scene") -> dict[str, object]:
    return {
        "name": name,
        "blender_version": "4.5.11",
        "engine": "CYCLES",
        "device": "OPTIX",
        "gpu_ids": [0, 1],
        "frame_mode": "RANGE",
        "frame_start": 1,
        "frame_end": 240,
    }


async def test_job_lifecycle_and_server_generated_storage(
    job_client: AsyncClient, job_settings: Settings
) -> None:
    create_response = await job_client.post("/api/v1/jobs", json=job_payload())
    assert create_response.status_code == 201
    created = create_response.json()
    job_id = UUID(created["id"])
    job_directory = job_settings.jobs_root / str(job_id)
    assert created["status"] == "created"
    assert created["source_filename"] is None
    assert job_directory.is_dir()
    assert {path.name for path in job_directory.iterdir()} == {
        "logs",
        "output",
        "preview",
        "temp",
    }

    early_start = await job_client.post(f"/api/v1/jobs/{job_id}/start")
    assert early_start.status_code == 409
    assert early_start.json()["error"]["code"] == "invalid_job_transition"

    upload_response = await job_client.post(
        f"/api/v1/jobs/{job_id}/uploads",
        files={
            "file": (
                "../../client-scene.blend",
                b"BLENDER-v300",
                "application/octet-stream",
            )
        },
    )
    assert upload_response.status_code == 200
    assert upload_response.json()["status"] == "ready"
    assert upload_response.json()["source_filename"] == "client-scene.blend"
    assert (job_directory / "input" / "scene.blend").read_bytes() == b"BLENDER-v300"
    assert not (job_settings.workspace / "client-scene.blend").exists()

    list_response = await job_client.get("/api/v1/jobs")
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [str(job_id)]

    start_response = await job_client.post(f"/api/v1/jobs/{job_id}/start")
    assert start_response.json()["status"] == "queued"
    delete_active = await job_client.delete(f"/api/v1/jobs/{job_id}")
    assert delete_active.status_code == 409
    assert delete_active.json()["error"]["code"] == "active_job_cannot_be_deleted"

    cancelled = await job_client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["finished_at"].endswith("Z")
    retry_response = await job_client.post(f"/api/v1/jobs/{job_id}/retry")
    assert retry_response.json()["status"] == "queued"
    second_cancel = await job_client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert second_cancel.json()["status"] == "cancelled"

    delete_response = await job_client.delete(f"/api/v1/jobs/{job_id}")
    assert delete_response.status_code == 204
    assert not job_directory.exists()
    assert (await job_client.get(f"/api/v1/jobs/{job_id}")).status_code == 404


async def test_jobs_survive_application_restart(job_settings: Settings) -> None:
    first_app = create_app(job_settings)
    async with first_app.router.lifespan_context(first_app):
        first_transport = ASGITransport(app=first_app)
        async with AsyncClient(transport=first_transport, base_url="http://test") as client:
            response = await client.post("/api/v1/jobs", json=job_payload("Persistent scene"))
            job_id = response.json()["id"]

    second_app = create_app(job_settings)
    async with second_app.router.lifespan_context(second_app):
        second_transport = ASGITransport(app=second_app)
        async with AsyncClient(transport=second_transport, base_url="http://test") as client:
            jobs = (await client.get("/api/v1/jobs")).json()

    assert jobs[0]["id"] == job_id
    assert jobs[0]["name"] == "Persistent scene"


async def test_unknown_job_uses_shared_error_contract(job_client: AsyncClient) -> None:
    response = await job_client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"
