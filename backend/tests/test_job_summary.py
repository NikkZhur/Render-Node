from __future__ import annotations

from datetime import timedelta

from fastapi import FastAPI
from httpx import AsyncClient

from app.jobs.models import Job, utc_now
from app.jobs.types import ComputeDevice, FrameMode, JobStatus, RenderEngine
from app.storage.database import Database


async def test_queue_summary_includes_jobs_outside_history_page(
    job_client: AsyncClient, job_app: FastAPI
) -> None:
    response = await job_client.get("/api/v1/jobs/summary")
    assert response.status_code == 200
    assert response.json() == {"queued_count": 0, "rendering_count": 0}

    database: Database = job_app.state.database
    now = utc_now()
    statuses = [JobStatus.QUEUED, JobStatus.QUEUED, JobStatus.RENDERING]
    statuses += [JobStatus.COMPLETED] * 12
    statuses += [JobStatus.CREATED, JobStatus.READY, JobStatus.FAILED, JobStatus.CANCELLED]
    async with database.session_factory() as session, session.begin():
        jobs = [
            Job(
                name=f"Summary job {index}",
                blender_version="5.2.0",
                engine=RenderEngine.CYCLES,
                device=ComputeDevice.CPU,
                frame_mode=FrameMode.SINGLE,
                frame_start=1,
                status=status,
                created_at=now + timedelta(seconds=index),
            )
            for index, status in enumerate(statuses)
        ]
        session.add_all(jobs)

    page = await job_client.get("/api/v1/jobs/page?page=1&page_size=10")
    assert page.status_code == 200
    assert len(page.json()["items"]) == 10
    assert page.json()["total"] == len(statuses)
    assert all(item["status"] not in {"queued", "rendering"} for item in page.json()["items"])

    summary = await job_client.get("/api/v1/jobs/summary")
    assert summary.json() == {"queued_count": 2, "rendering_count": 1}

    # Cancellation through the public API updates the aggregate immediately.
    cancelled = await job_client.post(f"/api/v1/jobs/{jobs[0].id}/cancel")
    assert cancelled.status_code == 200
    assert (await job_client.get("/api/v1/jobs/summary")).json() == {
        "queued_count": 1,
        "rendering_count": 1,
    }

    async with database.session_factory() as session, session.begin():
        rendering = await session.get(Job, jobs[2].id)
        queued = await session.get(Job, jobs[1].id)
        assert rendering is not None and queued is not None
        rendering.status = JobStatus.COMPLETED
        queued.status = JobStatus.CANCELLED

    assert (await job_client.get("/api/v1/jobs/summary")).json() == {
        "queued_count": 0,
        "rendering_count": 0,
    }
