from __future__ import annotations

import asyncio
from uuid import UUID

from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.jobs.repository import JobRepository
from app.jobs.types import JobStatus
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

    invalid_replacement = await job_client.post(
        f"/api/v1/jobs/{job_id}/uploads",
        files={"file": ("broken.blend", b"not-blender", "application/octet-stream")},
    )
    assert invalid_replacement.status_code == 422
    assert (job_directory / "input" / "scene.blend").read_bytes() == b"BLENDER-v300"

    replacement = await job_client.post(
        f"/api/v1/jobs/{job_id}/uploads",
        files={"file": ("replacement.blend", b"BLENDER-v310", "application/octet-stream")},
    )
    assert replacement.status_code == 200
    assert replacement.json()["source_filename"] == "replacement.blend"
    assert (job_directory / "input" / "scene.blend").read_bytes() == b"BLENDER-v310"

    update_payload = job_payload("Adjusted scene")
    update_payload.pop("blender_version")
    update_payload.update(
        {
            "engine": "BLENDER_EEVEE",
            "device": "CPU",
            "gpu_ids": [],
            "frame_mode": "SINGLE",
            "frame_start": 12,
            "frame_end": None,
        }
    )
    updated = await job_client.put(f"/api/v1/jobs/{job_id}", json=update_payload)
    assert updated.status_code == 200
    assert updated.json()["name"] == "Adjusted scene"
    assert updated.json()["engine"] == "BLENDER_EEVEE"
    assert updated.json()["frame_start"] == 12
    await asyncio.to_thread((job_directory / "input" / "textures").mkdir)
    await asyncio.to_thread(
        (job_directory / "input" / "textures" / "albedo.bin").write_bytes, b"texture"
    )

    list_response = await job_client.get("/api/v1/jobs")
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [str(job_id)]

    start_response = await job_client.post(f"/api/v1/jobs/{job_id}/start")
    assert start_response.json()["status"] == "queued"
    locked_update = await job_client.put(f"/api/v1/jobs/{job_id}", json=update_payload)
    assert locked_update.status_code == 409
    assert locked_update.json()["error"]["code"] == "job_settings_locked"
    locked_upload = await job_client.post(
        f"/api/v1/jobs/{job_id}/uploads",
        files={"file": ("too-late.blend", b"BLENDER-v300", "application/octet-stream")},
    )
    assert locked_upload.status_code == 409
    assert locked_upload.json()["error"]["code"] == "job_upload_locked"
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

    rerender = await job_client.post(f"/api/v1/jobs/{job_id}/rerender")
    assert rerender.status_code == 201
    rerendered = rerender.json()
    rerendered_id = UUID(rerendered["id"])
    rerendered_directory = job_settings.jobs_root / str(rerendered_id)
    assert rerendered["status"] == "ready"
    assert rerendered["name"] == "Adjusted scene rerender"
    assert rerendered["source_filename"] == "replacement.blend"
    assert rerendered["engine"] == "BLENDER_EEVEE"
    assert rerendered["frame_mode"] == "SINGLE"
    assert (rerendered_directory / "input" / "scene.blend").read_bytes() == b"BLENDER-v310"
    assert (rerendered_directory / "input" / "textures" / "albedo.bin").read_bytes() == b"texture"

    rerender_ready = await job_client.post(f"/api/v1/jobs/{rerendered_id}/rerender")
    assert rerender_ready.status_code == 409
    assert rerender_ready.json()["error"]["code"] == "job_not_rerenderable"

    delete_response = await job_client.delete(f"/api/v1/jobs/{job_id}")
    assert delete_response.status_code == 204
    assert not job_directory.exists()
    assert (await job_client.get(f"/api/v1/jobs/{job_id}")).status_code == 404

    delete_rerendered = await job_client.delete(f"/api/v1/jobs/{rerendered_id}")
    assert delete_rerendered.status_code == 204
    assert not rerendered_directory.exists()


async def test_jobs_survive_application_restart(job_settings: Settings) -> None:
    first_app = create_app(job_settings)
    async with first_app.router.lifespan_context(first_app):
        first_transport = ASGITransport(app=first_app)
        async with AsyncClient(transport=first_transport, base_url="http://test") as client:
            response = await client.post("/api/v1/jobs", json=job_payload("Persistent scene"))
            job_id = response.json()["id"]
            job_root = job_settings.jobs_root / job_id
            await asyncio.to_thread((job_root / "temp" / "upload.part").write_bytes, b"partial")
            await asyncio.to_thread((job_root / "input").mkdir)
            await asyncio.to_thread(
                (job_root / "input" / "scene.blend").write_bytes, b"BLENDER-v300"
            )

    second_app = create_app(job_settings)
    async with second_app.router.lifespan_context(second_app):
        second_transport = ASGITransport(app=second_app)
        async with AsyncClient(transport=second_transport, base_url="http://test") as client:
            jobs = (await client.get("/api/v1/jobs")).json()

    assert jobs[0]["id"] == job_id
    assert jobs[0]["name"] == "Persistent scene"
    assert list((job_settings.jobs_root / job_id / "temp").iterdir()) == []
    assert not (job_settings.jobs_root / job_id / "input").exists()


async def test_job_cannot_start_when_uploaded_scene_disappears(
    job_client: AsyncClient, job_settings: Settings
) -> None:
    created = await job_client.post("/api/v1/jobs", json=job_payload())
    job_id = created.json()["id"]
    uploaded = await job_client.post(
        f"/api/v1/jobs/{job_id}/uploads",
        files={"file": ("scene.blend", b"BLENDER-v300", "application/octet-stream")},
    )
    assert uploaded.status_code == 200
    await asyncio.to_thread((job_settings.jobs_root / job_id / "input" / "scene.blend").unlink)

    response = await job_client.post(f"/api/v1/jobs/{job_id}/start")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "job_scene_unavailable"


async def test_disabled_runner_rejects_start_without_changing_ready_job(
    job_settings: Settings,
) -> None:
    app = create_app(job_settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/v1/jobs", json=job_payload())
            job_id = created.json()["id"]
            uploaded = await client.post(
                f"/api/v1/jobs/{job_id}/uploads",
                files={"file": ("scene.blend", b"BLENDER-v300", "application/octet-stream")},
            )
            assert uploaded.status_code == 200

            capabilities = await client.get("/api/v1/system/capabilities")
            response = await client.post(f"/api/v1/jobs/{job_id}/start")
            persisted = await client.get(f"/api/v1/jobs/{job_id}")
            async with app.state.database.session_factory() as session, session.begin():
                staged = await JobRepository(session).get(UUID(job_id))
                assert staged is not None
                staged.status = JobStatus.CANCELLED
            retry = await client.post(f"/api/v1/jobs/{job_id}/retry")
            persisted_after_retry = await client.get(f"/api/v1/jobs/{job_id}")

    assert capabilities.status_code == 200
    assert capabilities.json()["runner"] == {
        "available": False,
        "mode": "disabled",
        "message": "Render scheduler is disabled",
    }
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "runner_unavailable"
    assert persisted.json()["status"] == "ready"
    assert retry.status_code == 409
    assert retry.json()["error"]["code"] == "runner_unavailable"
    assert persisted_after_retry.json()["status"] == "cancelled"


async def test_unknown_job_uses_shared_error_contract(job_client: AsyncClient) -> None:
    response = await job_client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"


async def test_job_page_limits_history_without_breaking_list_endpoint(
    job_client: AsyncClient,
) -> None:
    for index in range(23):
        created = await job_client.post(
            "/api/v1/jobs",
            json=job_payload(f"Paginated job {index:02d}"),
        )
        assert created.status_code == 201

    first = await job_client.get("/api/v1/jobs/page?page=1&page_size=10")
    second = await job_client.get("/api/v1/jobs/page?page=2&page_size=10")
    third = await job_client.get("/api/v1/jobs/page?page=3&page_size=10")
    beyond = await job_client.get("/api/v1/jobs/page?page=4&page_size=10")
    oversized = await job_client.get("/api/v1/jobs/page?page=1&page_size=11")
    legacy = await job_client.get("/api/v1/jobs")

    assert first.status_code == 200
    assert first.json()["page"] == 1
    assert first.json()["page_size"] == 10
    assert first.json()["total"] == 23
    assert first.json()["pages"] == 3
    assert len(first.json()["items"]) == 10
    assert first.json()["items"][0]["name"] == "Paginated job 22"
    assert len(second.json()["items"]) == 10
    assert len(third.json()["items"]) == 3
    assert beyond.json()["items"] == []
    assert beyond.json()["pages"] == 3
    assert oversized.status_code == 422
    assert len(legacy.json()) == 23
