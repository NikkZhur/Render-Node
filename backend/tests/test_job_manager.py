from __future__ import annotations

import asyncio
import base64
import io
import shutil
import time
import zipfile
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.blender.cgroups import MemoryEvents
from app.blender.devices import GpuDevice, GpuDiscovery, GpuScheduler
from app.blender.runner import (
    FailureKind,
    RunnerError,
    RunResult,
    SandboxRunner,
    WorkerRunnerError,
)
from app.config import Environment, Settings
from app.jobs.manager import JobManager
from app.jobs.models import Job, utc_now
from app.jobs.repository import JobRepository
from app.jobs.types import ComputeDevice, FrameMode, JobStatus, RenderEngine
from app.main import create_app
from app.storage.database import Database

FAKE_BLENDER = Path(__file__).parent / "fixtures" / "fake-blender"
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture
def render_settings(tmp_path: Path) -> Settings:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'render.sqlite3'}"
    settings = Settings(
        env=Environment.TEST,
        workspace=tmp_path,
        database_url=database_url,
        bundled_blender_root=tmp_path / "bundled",
        render_scheduler_enabled=True,
        render_scheduler_poll_seconds=0.05,
        render_timeout_seconds=5,
        render_terminate_grace_seconds=0.2,
        max_render_output_gb=0.001,
        max_render_log_mb=1,
        worker_memory_gb=1,
    )
    for version in ("5.2.0", "4.1.1"):
        binary = settings.bundled_blender_root / version / "blender"
        binary.parent.mkdir(parents=True)
        if version == "5.2.0":
            shutil.copy2(FAKE_BLENDER, binary)
        else:
            binary.write_text(f"#!/bin/sh\nprintf 'Blender {version}\\n'\n")
        binary.chmod(0o755)
    alembic_config = Config("alembic.ini")
    alembic_config.attributes["database_url"] = database_url
    command.upgrade(alembic_config, "head")
    return settings


def job_payload(name: str) -> dict[str, object]:
    return {
        "name": name,
        "blender_version": "4.5.11",
        "engine": "CYCLES",
        "device": "CPU",
        "gpu_ids": [],
        "frame_mode": "SINGLE",
        "frame_start": 1,
        "frame_end": None,
    }


async def create_ready_job(client: AsyncClient, name: str, scene: bytes) -> UUID:
    activated = await client.post("/api/v1/blender/versions/5.2.0/activate")
    assert activated.status_code == 200
    created = await client.post("/api/v1/jobs", json=job_payload(name))
    assert created.status_code == 201
    job_id = UUID(created.json()["id"])
    uploaded = await client.post(
        f"/api/v1/jobs/{job_id}/uploads",
        files={"file": ("scene.blend", scene, "application/octet-stream")},
    )
    assert uploaded.status_code == 200
    return job_id


async def wait_for_status(
    client: AsyncClient, job_id: UUID, status: JobStatus, *, max_wait_seconds: float = 3
) -> dict[str, object]:
    async with asyncio.timeout(max_wait_seconds):
        while True:
            response = await client.get(f"/api/v1/jobs/{job_id}")
            assert response.status_code == 200
            body = cast(dict[str, object], response.json())
            if body["status"] == status.value:
                return body
            await asyncio.sleep(0.02)


async def assert_process_stopped(pid: int) -> None:
    for _ in range(50):
        try:
            raw_stat = await asyncio.to_thread(Path(f"/proc/{pid}/stat").read_text)
        except FileNotFoundError:
            return
        if raw_stat.split()[2] == "Z":
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"child process {pid} is still running")


async def wait_for_process_pid(client: AsyncClient, job_id: UUID) -> int:
    async with asyncio.timeout(2):
        while True:
            response = await client.get(f"/api/v1/jobs/{job_id}")
            process_pid = response.json()["process_pid"]
            if isinstance(process_pid, int):
                return process_pid
            await asyncio.sleep(0.02)


def read_when_created(path: Path, *, max_wait_seconds: float = 2) -> str:
    deadline = time.monotonic() + max_wait_seconds
    while time.monotonic() < deadline:
        try:
            return path.read_text()
        except FileNotFoundError:
            time.sleep(0.02)
    raise AssertionError(f"file was not created: {path}")


def test_job_error_messages_distinguish_system_ram_vram_and_plain_abort() -> None:
    system_oom = RunResult(
        exit_code=-9,
        failure_kind=FailureKind.SYSTEM_MEMORY_OOM,
        memory_events=MemoryEvents(oom=1, oom_kill=1),
    )
    gpu_oom = RunResult(exit_code=-6, failure_kind=FailureKind.GPU_MEMORY_OOM)
    plain_abort = RunResult(exit_code=-6, failure_kind=FailureKind.BLENDER_ABORT)

    assert "cgroup system memory" in JobManager._failure_message(system_oom)
    assert "does not limit VRAM" in JobManager._failure_message(gpu_oom)
    assert "without a cgroup OOM event" in JobManager._failure_message(plain_abort)


def test_overall_progress_combines_current_sample_and_frame_range() -> None:
    job = Job(
        name="Samples",
        blender_version="4.1.1",
        engine=RenderEngine.CYCLES,
        device=ComputeDevice.OPTIX,
        gpu_ids=[0],
        frame_mode=FrameMode.RANGE,
        frame_start=1,
        frame_end=2,
    )

    assert JobManager._overall_progress(job, 1, 400 / 512) == pytest.approx(400 / 1024)
    assert JobManager._overall_progress(job, 2, 400 / 512) == pytest.approx(912 / 1024)


async def test_scheduler_runs_fake_blender_serially_and_persists_progress(
    render_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RENDER_NODE_TEST_SECRET", "must-not-leak")
    app = create_app(render_settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first_id = await create_ready_job(client, "First", b"BLENDER-v300 FAST")
            second_id = await create_ready_job(client, "Second", b"BLENDER-v300 FAST")
            assert (await client.post(f"/api/v1/jobs/{first_id}/start")).status_code == 200
            assert (await client.post(f"/api/v1/jobs/{second_id}/start")).status_code == 200

            manager = cast(JobManager, app.state.job_manager)
            first = await manager.wait_for_terminal(first_id, max_wait_seconds=5)
            second = await manager.wait_for_terminal(second_id, max_wait_seconds=5)

            frames = await client.get(f"/api/v1/jobs/{first_id}/frames")
            assert frames.status_code == 200
            assert frames.json()["total"] == 1
            assert frames.json()["items"][0]["preview_url"].endswith("/frames/1/preview")
            preview = await client.get(f"/api/v1/jobs/{first_id}/frames/1/preview")
            assert preview.status_code == 200
            assert preview.headers["content-type"] == "image/png"
            assert preview.content.startswith(b"\x89PNG")
            original = await client.get(f"/api/v1/jobs/{first_id}/frames/1/original")
            assert original.status_code == 200
            assert "attachment" in original.headers["content-disposition"]
            log = await client.get(f"/api/v1/jobs/{first_id}/logs/blender")
            assert log.status_code == 200
            assert "RENDER_NODE_PROGRESS" in log.text
            tail = await client.get(f"/api/v1/jobs/{first_id}/logs/blender/tail?lines=2")
            assert tail.status_code == 200
            assert len(tail.json()["lines"]) == 2
            archive = await client.get(f"/api/v1/jobs/{first_id}/frames.zip")
            assert archive.status_code == 200
            with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
                assert bundle.namelist() == ["frame_0001.png"]
            assert (
                await client.get(f"/api/v1/jobs/{first_id}/frames?page_size=51")
            ).status_code == 422

    assert first.status is JobStatus.COMPLETED
    assert second.status is JobStatus.COMPLETED
    assert first.progress == 1.0
    assert first.current_frame == 1
    assert first.finished_at is not None
    assert second.started_at is not None
    assert second.started_at >= first.finished_at
    first_root = render_settings.jobs_root / str(first_id)
    raw_log = await asyncio.to_thread((first_root / "logs" / "blender.log").read_text)
    arguments = await asyncio.to_thread((first_root / "logs" / "arguments.txt").read_text)
    assert "inherited-secret=unset" in raw_log
    assert "must-not-leak" not in raw_log
    assert "--factory-startup" in arguments
    assert "--disable-autoexec" in arguments
    assert (first_root / "output" / "frame_0001.png").is_file()


async def test_frame_pagination_is_persistent_and_bounded(render_settings: Settings) -> None:
    app = create_app(render_settings.model_copy(update={"render_scheduler_enabled": False}))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.post("/api/v1/blender/versions/5.2.0/activate")).status_code == 200
            created = await client.post("/api/v1/jobs", json=job_payload("Sequence"))
            job_id = UUID(created.json()["id"])
            missing_log = await client.get(f"/api/v1/jobs/{job_id}/logs/blender/tail")
            assert missing_log.status_code == 404
            assert missing_log.json()["error"]["code"] == "artifact_not_found"
            output = render_settings.jobs_root / str(job_id) / "output"
            subscription = await app.state.event_hub.connect()
            await app.state.event_hub.subscribe(subscription, {job_id})
            for frame in range(1, 52):
                path = output / f"frame_{frame:04d}.png"
                await asyncio.to_thread(path.write_bytes, PNG_1X1)
                await app.state.artifact_service.register_output(job_id, path)
                if frame == 1:
                    frame_events: list[dict[str, object]] = []
                    async with asyncio.timeout(2):
                        while len(frame_events) < 2:
                            event = await subscription.queue.get()
                            if str(event["type"]).startswith("render."):
                                frame_events.append(event)
                    assert {event["type"] for event in frame_events} == {
                        "render.preview_ready",
                        "render.frame_ready",
                    }
                    assert all("data" not in event for event in frame_events)
                    await app.state.event_hub.disconnect(subscription)

            first_page = await client.get(f"/api/v1/jobs/{job_id}/frames")
            second_page = await client.get(f"/api/v1/jobs/{job_id}/frames?page=2")

    assert first_page.status_code == 200
    assert first_page.json()["page_size"] == 50
    assert first_page.json()["total"] == 51
    assert first_page.json()["pages"] == 2
    assert len(first_page.json()["items"]) == 50
    assert [item["frame"] for item in second_page.json()["items"]] == [51]


async def test_artifact_reconciliation_registers_a_missed_completed_frame(
    render_settings: Settings,
) -> None:
    app = create_app(render_settings.model_copy(update={"render_scheduler_enabled": False}))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.post("/api/v1/blender/versions/5.2.0/activate")).status_code == 200
            created = await client.post("/api/v1/jobs", json=job_payload("Reconcile output"))
            job_id = UUID(created.json()["id"])
            output = render_settings.jobs_root / str(job_id) / "output"
            first = output / "frame_0001.png"
            second = output / "frame_0002.png"
            await asyncio.to_thread(first.write_bytes, PNG_1X1)
            await asyncio.to_thread(second.write_bytes, PNG_1X1)
            await asyncio.to_thread((output / "frame_0003.tmp").write_bytes, b"partial")
            await app.state.artifact_service.register_output(job_id, second)

            registered = await app.state.artifact_service.reconcile_outputs(job_id)
            frames = await client.get(f"/api/v1/jobs/{job_id}/frames")

    assert registered == 1
    assert frames.json()["total"] == 2
    assert [item["frame"] for item in frames.json()["items"]] == [1, 2]


async def test_retry_clears_previous_runtime_files_and_artifacts(
    render_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def start_without_scheduler(manager: JobManager) -> None:
        await manager._recover_interrupted()

    monkeypatch.setattr(JobManager, "start", start_without_scheduler)
    app = create_app(render_settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            job_id = await create_ready_job(client, "Retry cleanup", b"BLENDER-v300")
            assert (await client.post(f"/api/v1/jobs/{job_id}/start")).status_code == 200
            cancelled = await client.post(f"/api/v1/jobs/{job_id}/cancel")
            assert cancelled.json()["status"] == "cancelled"

            job_root = render_settings.jobs_root / str(job_id)
            output_path = job_root / "output" / "frame_0001.png"
            log_path = job_root / "logs" / "blender.log"
            await asyncio.to_thread(output_path.write_bytes, PNG_1X1)
            await asyncio.to_thread(log_path.write_text, "old render log", encoding="utf-8")
            await asyncio.to_thread((job_root / "temp" / "scratch").write_bytes, b"old")
            await app.state.artifact_service.register_output(job_id, output_path)
            await app.state.artifact_service.register_log(job_id)
            assert len((await client.get(f"/api/v1/jobs/{job_id}/artifacts")).json()) == 3

            retried = await client.post(f"/api/v1/jobs/{job_id}/retry")

            assert retried.status_code == 200
            assert retried.json()["status"] == "queued"
            assert (job_root / "input" / "scene.blend").is_file()
            for name in ("output", "preview", "logs", "temp"):
                assert list((job_root / name).iterdir()) == []
            assert (await client.get(f"/api/v1/jobs/{job_id}/artifacts")).json() == []


async def test_rendering_cancel_stops_fake_blender_process_group(
    render_settings: Settings,
) -> None:
    app = create_app(render_settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            job_id = await create_ready_job(client, "Slow", b"BLENDER-v300 SLOW")
            assert (await client.post(f"/api/v1/jobs/{job_id}/start")).status_code == 200
            await wait_for_status(client, job_id, JobStatus.RENDERING)
            assert await wait_for_process_pid(client, job_id) > 0
            child_file = render_settings.jobs_root / str(job_id) / "child.pid"
            child_pid = int(await asyncio.to_thread(read_when_created, child_file))

            cancelled = await client.post(f"/api/v1/jobs/{job_id}/cancel")

    assert cancelled.status_code == 200
    body = cancelled.json()
    assert body["status"] == "cancelled"
    assert body["process_pid"] is None
    assert body["exit_code"] != 0
    await assert_process_stopped(child_pid)


async def test_startup_recovers_interrupted_render_as_failed(
    render_settings: Settings,
) -> None:
    database = Database(render_settings.database_url)
    interrupted = Job(
        name="Interrupted",
        source_filename="scene.blend",
        scene_path="input/scene.blend",
        status=JobStatus.RENDERING,
        blender_version="4.5.11",
        engine=RenderEngine.CYCLES,
        device=ComputeDevice.CPU,
        gpu_ids=[],
        frame_mode=FrameMode.SINGLE,
        frame_start=1,
        frame_end=None,
        progress=0.4,
        process_pid=999_999,
        started_at=utc_now(),
    )
    async with database.session_factory() as session, session.begin():
        await JobRepository(session).add(interrupted)
    await database.dispose()

    recovery_settings = render_settings.model_copy(update={"render_scheduler_enabled": False})
    app: FastAPI = create_app(recovery_settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/jobs/{interrupted.id}")

    assert response.status_code == 200
    recovered = response.json()
    assert recovered["status"] == "failed"
    assert recovered["process_pid"] is None
    assert recovered["error"] == "Render was interrupted by a service restart"


async def test_unavailable_gpu_fails_job_without_starting_blender(
    render_settings: Settings,
) -> None:
    app = create_app(render_settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.post("/api/v1/blender/versions/5.2.0/activate")).status_code == 200
            payload = job_payload("Missing GPU")
            payload.update({"device": "CUDA", "gpu_ids": [999_999]})
            created = await client.post("/api/v1/jobs", json=payload)
            job_id = UUID(created.json()["id"])
            uploaded = await client.post(
                f"/api/v1/jobs/{job_id}/uploads",
                files={
                    "file": (
                        "scene.blend",
                        b"BLENDER-v300 FAST",
                        "application/octet-stream",
                    )
                },
            )
            assert uploaded.status_code == 200
            assert (await client.post(f"/api/v1/jobs/{job_id}/start")).status_code == 200
            manager = cast(JobManager, app.state.job_manager)
            failed = await manager.wait_for_terminal(job_id, max_wait_seconds=3)

    assert failed.status is JobStatus.FAILED
    assert failed.error == "Requested GPU is unavailable"
    assert failed.process_pid is None
    log_path = render_settings.jobs_root / str(job_id) / "logs" / "blender.log"
    assert not await asyncio.to_thread(log_path.exists)


@pytest.mark.parametrize("uuid_prefix", ["GPU", "MIG"])
async def test_multi_gpu_job_uses_uuid_visibility_and_releases_every_gpu(
    render_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    uuid_prefix: str,
) -> None:
    first_uuid, second_uuid = f"{uuid_prefix}-stable-a", f"{uuid_prefix}-stable-b"
    devices = [
        GpuDevice(id=0, uuid=first_uuid, name="GPU A", memory_total_bytes=24 * 1024**3),
        GpuDevice(id=1, uuid=second_uuid, name="GPU B", memory_total_bytes=48 * 1024**3),
    ]
    monkeypatch.setattr(GpuDiscovery, "discover", lambda self: devices)
    app = create_app(render_settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.post("/api/v1/blender/versions/5.2.0/activate")).status_code == 200
            payload = job_payload("Multi GPU")
            payload.update(
                {
                    "device": "OPTIX",
                    "gpu_ids": [0, 1],
                    "gpu_render_mode": "ALL_GPUS_PER_FRAME",
                }
            )
            created = await client.post("/api/v1/jobs", json=payload)
            job_id = UUID(created.json()["id"])
            uploaded = await client.post(
                f"/api/v1/jobs/{job_id}/uploads",
                files={"file": ("scene.blend", b"BLENDER-v300 FAST", "application/octet-stream")},
            )
            assert uploaded.status_code == 200
            subscription = await app.state.event_hub.connect()
            await app.state.event_hub.subscribe(subscription, {job_id})
            assert (await client.post(f"/api/v1/jobs/{job_id}/start")).status_code == 200
            manager = cast(JobManager, app.state.job_manager)
            completed = await manager.wait_for_terminal(job_id, max_wait_seconds=3)
            scheduler = cast(GpuScheduler, app.state.gpu_scheduler)
            events: list[dict[str, object]] = []
            while not subscription.queue.empty():
                events.append(subscription.queue.get_nowait())
            await app.state.event_hub.disconnect(subscription)

    assert completed.status is JobStatus.COMPLETED
    raw_log = await asyncio.to_thread(
        (render_settings.jobs_root / str(job_id) / "logs" / "blender.log").read_text
    )
    assert f"visible-gpus={first_uuid},{second_uuid}" in raw_log
    joint_progress = [event for event in events if event["type"] == "render.progress"]
    assert joint_progress
    assert all("worker_progress" not in event for event in joint_progress)
    assert all("gpu_uuid" not in event for event in joint_progress)
    assert scheduler.reserved_uuids == set()


@pytest.mark.parametrize("uuid_prefix", ["GPU", "MIG"])
async def test_frame_per_gpu_renders_two_frames_concurrently_with_monotonic_events(
    render_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    uuid_prefix: str,
) -> None:
    first_uuid, second_uuid = f"{uuid_prefix}-stable-a", f"{uuid_prefix}-stable-b"
    devices = [
        GpuDevice(id=0, uuid=first_uuid, name="GPU A", memory_total_bytes=24 * 1024**3),
        GpuDevice(id=1, uuid=second_uuid, name="GPU B", memory_total_bytes=48 * 1024**3),
    ]
    monkeypatch.setattr(GpuDiscovery, "discover", lambda self: devices)
    app = create_app(render_settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.post("/api/v1/blender/versions/5.2.0/activate")).status_code == 200
            payload = job_payload("Parallel frames")
            payload.update(
                {
                    "device": "OPTIX",
                    "gpu_ids": [0, 1],
                    "frame_mode": "RANGE",
                    "frame_start": 1,
                    "frame_end": 2,
                }
            )
            created = await client.post("/api/v1/jobs", json=payload)
            assert created.status_code == 201
            assert created.json()["gpu_render_mode"] == "FRAME_PER_GPU"
            job_id = UUID(created.json()["id"])
            assert (
                await client.post(
                    f"/api/v1/jobs/{job_id}/uploads",
                    files={
                        "file": (
                            "scene.blend",
                            b"BLENDER-v300 BARRIER UNEQUAL SAMPLES",
                            "application/octet-stream",
                        )
                    },
                )
            ).status_code == 200
            subscription = await app.state.event_hub.connect()
            await app.state.event_hub.subscribe(subscription, {job_id})
            assert (await client.post(f"/api/v1/jobs/{job_id}/start")).status_code == 200
            manager = cast(JobManager, app.state.job_manager)
            completed = await manager.wait_for_terminal(job_id, max_wait_seconds=5)
            frames = await client.get(f"/api/v1/jobs/{job_id}/frames")
            scheduler = cast(GpuScheduler, app.state.gpu_scheduler)
            events: list[dict[str, object]] = []
            while not subscription.queue.empty():
                events.append(subscription.queue.get_nowait())
            await app.state.event_hub.disconnect(subscription)

    assert completed.status is JobStatus.COMPLETED
    assert completed.progress == 1.0
    assert frames.json()["total"] == 2
    assert [item["frame"] for item in frames.json()["items"]] == [1, 2]
    job_root = render_settings.jobs_root / str(job_id)
    first_log = (job_root / "logs" / "blender-gpu-0.log").read_text()
    second_log = (job_root / "logs" / "blender-gpu-1.log").read_text()
    aggregate = (job_root / "logs" / "blender.log").read_text()
    assert f"visible-gpus={first_uuid}" in first_log
    assert f"visible-gpus={second_uuid}" in second_log
    assert first_uuid in aggregate and second_uuid in aggregate
    assert sorted(path.name for path in (job_root / "output").glob("frame_*.png")) == [
        "frame_0001.png",
        "frame_0002.png",
    ]
    progress_values = [
        float(cast(float | int, event["progress"]))
        for event in events
        if event["type"] == "render.progress"
    ]
    assert progress_values == sorted(progress_values)
    progress_events = [event for event in events if event["type"] == "render.progress"]
    assert progress_events
    assert all(
        event.get("gpu_uuid") in {first_uuid, second_uuid}
        and isinstance(event.get("worker_progress"), int | float)
        for event in progress_events
    )
    for gpu_uuid, expected_frame in ((first_uuid, 1), (second_uuid, 2)):
        worker_events = [event for event in progress_events if event["gpu_uuid"] == gpu_uuid]
        assert worker_events
        assert {event["frame"] for event in worker_events} == {expected_frame}
        worker_values = [
            float(cast(float | int, event["worker_progress"])) for event in worker_events
        ]
        assert worker_values == sorted(worker_values)
        sample_events = [event for event in worker_events if event["sample"] is not None]
        assert sample_events
        assert all(event["total_samples"] == 512 for event in sample_events)
    first_finished = next(
        event
        for event in progress_events
        if event["gpu_uuid"] == first_uuid and event["worker_progress"] == 1.0
    )
    assert float(cast(float | int, first_finished["progress"])) < 1.0
    frame_ready = [
        cast(int, event["frame"]) for event in events if event["type"] == "render.frame_ready"
    ]
    assert sorted(frame_ready) == [1, 2]
    assert len(frame_ready) == len(set(frame_ready))
    live_logs = [event for event in events if event["type"] == "render.log"]
    assert live_logs and all("gpu_uuid" in event for event in live_logs)
    assert scheduler.reserved_uuids == set()


@pytest.mark.parametrize(
    ("frame_configuration", "scene", "expected_frames"),
    [
        (
            {"frame_mode": "RANGE", "frame_start": 1, "frame_end": 7},
            b"BLENDER-v300 STEP=2",
            [1, 3, 5, 7],
        ),
        (
            {"frame_mode": "ALL", "frame_start": None, "frame_end": None},
            b"BLENDER-v300 SCENE_START=2 SCENE_END=8 STEP=3",
            [2, 5, 8],
        ),
    ],
)
async def test_frame_per_gpu_respects_range_all_and_scene_step(
    render_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    frame_configuration: dict[str, object],
    scene: bytes,
    expected_frames: list[int],
) -> None:
    devices = [
        GpuDevice(id=0, uuid="GPU-stable-a", name="GPU A", memory_total_bytes=24 * 1024**3),
        GpuDevice(id=1, uuid="GPU-stable-b", name="GPU B", memory_total_bytes=48 * 1024**3),
    ]
    monkeypatch.setattr(GpuDiscovery, "discover", lambda self: devices)
    app = create_app(render_settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.post("/api/v1/blender/versions/5.2.0/activate")).status_code == 200
            payload = job_payload("Frame sequence")
            payload.update({"device": "CUDA", "gpu_ids": [0, 1], **frame_configuration})
            created = await client.post("/api/v1/jobs", json=payload)
            job_id = UUID(created.json()["id"])
            assert (
                await client.post(
                    f"/api/v1/jobs/{job_id}/uploads",
                    files={"file": ("scene.blend", scene, "application/octet-stream")},
                )
            ).status_code == 200
            assert (await client.post(f"/api/v1/jobs/{job_id}/start")).status_code == 200
            manager = cast(JobManager, app.state.job_manager)
            completed = await manager.wait_for_terminal(job_id, max_wait_seconds=5)
            frames = await client.get(f"/api/v1/jobs/{job_id}/frames")

    assert completed.status is JobStatus.COMPLETED
    assert [item["frame"] for item in frames.json()["items"]] == expected_frames


async def test_frame_per_gpu_single_uses_only_first_selected_gpu(
    render_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    devices = [
        GpuDevice(id=0, uuid="GPU-stable-a", name="GPU A", memory_total_bytes=24 * 1024**3),
        GpuDevice(id=1, uuid="GPU-stable-b", name="GPU B", memory_total_bytes=48 * 1024**3),
    ]
    monkeypatch.setattr(GpuDiscovery, "discover", lambda self: devices)
    app = create_app(render_settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.post("/api/v1/blender/versions/5.2.0/activate")).status_code == 200
            payload = job_payload("Single distributed")
            payload.update({"device": "OPTIX", "gpu_ids": [0, 1]})
            created = await client.post("/api/v1/jobs", json=payload)
            job_id = UUID(created.json()["id"])
            assert (
                await client.post(
                    f"/api/v1/jobs/{job_id}/uploads",
                    files={"file": ("scene.blend", b"BLENDER-v300", "application/octet-stream")},
                )
            ).status_code == 200
            subscription = await app.state.event_hub.connect()
            await app.state.event_hub.subscribe(subscription, {job_id})
            assert (await client.post(f"/api/v1/jobs/{job_id}/start")).status_code == 200
            manager = cast(JobManager, app.state.job_manager)
            completed = await manager.wait_for_terminal(job_id, max_wait_seconds=5)
            events: list[dict[str, object]] = []
            while not subscription.queue.empty():
                events.append(subscription.queue.get_nowait())
            await app.state.event_hub.disconnect(subscription)

    assert completed.status is JobStatus.COMPLETED
    logs = render_settings.jobs_root / str(job_id) / "logs"
    assert "visible-gpus=GPU-stable-a" in (logs / "blender-gpu-0.log").read_text()
    assert not (logs / "blender-gpu-1.log").exists()
    progress_events = [event for event in events if event["type"] == "render.progress"]
    assert progress_events
    assert {event["gpu_uuid"] for event in progress_events} == {"GPU-stable-a"}
    assert all(isinstance(event.get("worker_progress"), int | float) for event in progress_events)


async def test_frame_worker_failure_stops_peers_and_releases_all_gpus(
    render_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    devices = [
        GpuDevice(id=0, uuid="GPU-stable-a", name="GPU A", memory_total_bytes=24 * 1024**3),
        GpuDevice(id=1, uuid="GPU-stable-b", name="GPU B", memory_total_bytes=48 * 1024**3),
    ]
    monkeypatch.setattr(GpuDiscovery, "discover", lambda self: devices)
    app = create_app(render_settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.post("/api/v1/blender/versions/5.2.0/activate")).status_code == 200
            payload = job_payload("Fail one GPU")
            payload.update(
                {
                    "device": "OPTIX",
                    "gpu_ids": [0, 1],
                    "frame_mode": "RANGE",
                    "frame_start": 1,
                    "frame_end": 2,
                }
            )
            created = await client.post("/api/v1/jobs", json=payload)
            job_id = UUID(created.json()["id"])
            assert (
                await client.post(
                    f"/api/v1/jobs/{job_id}/uploads",
                    files={
                        "file": (
                            "scene.blend",
                            b"BLENDER-v300 FAIL_GPU=GPU-stable-b",
                            "application/octet-stream",
                        )
                    },
                )
            ).status_code == 200
            assert (await client.post(f"/api/v1/jobs/{job_id}/start")).status_code == 200
            manager = cast(JobManager, app.state.job_manager)
            failed = await manager.wait_for_terminal(job_id, max_wait_seconds=5)
            scheduler = cast(GpuScheduler, app.state.gpu_scheduler)

    assert failed.status is JobStatus.FAILED
    assert failed.error is not None and "GPU-stable-b" in failed.error
    child_pid = int((render_settings.jobs_root / str(job_id) / "child-gpu-0.pid").read_text())
    await assert_process_stopped(child_pid)
    assert scheduler.reserved_uuids == set()


async def test_frame_worker_launch_error_releases_all_gpus(
    render_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    devices = [
        GpuDevice(id=0, uuid="GPU-stable-a", name="GPU A", memory_total_bytes=24 * 1024**3),
        GpuDevice(id=1, uuid="GPU-stable-b", name="GPU B", memory_total_bytes=48 * 1024**3),
    ]
    monkeypatch.setattr(GpuDiscovery, "discover", lambda self: devices)

    async def fail_workers(self: SandboxRunner, *args: object, **kwargs: object) -> None:
        raise WorkerRunnerError("GPU-stable-b", "synthetic process startup error")

    monkeypatch.setattr(SandboxRunner, "run_workers", fail_workers)
    app = create_app(render_settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.post("/api/v1/blender/versions/5.2.0/activate")).status_code == 200
            payload = job_payload("Launch failure")
            payload.update(
                {
                    "device": "CUDA",
                    "gpu_ids": [0, 1],
                    "frame_mode": "RANGE",
                    "frame_start": 1,
                    "frame_end": 2,
                }
            )
            created = await client.post("/api/v1/jobs", json=payload)
            job_id = UUID(created.json()["id"])
            assert (
                await client.post(
                    f"/api/v1/jobs/{job_id}/uploads",
                    files={"file": ("scene.blend", b"BLENDER-v300", "application/octet-stream")},
                )
            ).status_code == 200
            assert (await client.post(f"/api/v1/jobs/{job_id}/start")).status_code == 200
            manager = cast(JobManager, app.state.job_manager)
            failed = await manager.wait_for_terminal(job_id, max_wait_seconds=5)
            scheduler = cast(GpuScheduler, app.state.gpu_scheduler)

    assert failed.status is JobStatus.FAILED
    assert failed.error is not None and "GPU-stable-b" in failed.error
    assert scheduler.reserved_uuids == set()


@pytest.mark.parametrize("cancel_explicitly", [True, False])
async def test_frame_workers_cancel_or_timeout_stop_every_process_and_release_gpus(
    render_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    cancel_explicitly: bool,
) -> None:
    devices = [
        GpuDevice(id=0, uuid="GPU-stable-a", name="GPU A", memory_total_bytes=24 * 1024**3),
        GpuDevice(id=1, uuid="GPU-stable-b", name="GPU B", memory_total_bytes=48 * 1024**3),
    ]
    monkeypatch.setattr(GpuDiscovery, "discover", lambda self: devices)
    configured = (
        render_settings
        if cancel_explicitly
        else render_settings.model_copy(update={"render_timeout_seconds": 0.5})
    )
    app = create_app(configured)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.post("/api/v1/blender/versions/5.2.0/activate")).status_code == 200
            payload = job_payload("Stop all workers")
            payload.update(
                {
                    "device": "OPTIX",
                    "gpu_ids": [0, 1],
                    "frame_mode": "RANGE",
                    "frame_start": 1,
                    "frame_end": 2,
                }
            )
            created = await client.post("/api/v1/jobs", json=payload)
            job_id = UUID(created.json()["id"])
            assert (
                await client.post(
                    f"/api/v1/jobs/{job_id}/uploads",
                    files={
                        "file": ("scene.blend", b"BLENDER-v300 SLOW", "application/octet-stream")
                    },
                )
            ).status_code == 200
            assert (await client.post(f"/api/v1/jobs/{job_id}/start")).status_code == 200
            await wait_for_status(client, job_id, JobStatus.RENDERING)
            job_root = configured.jobs_root / str(job_id)
            child_pids = [
                int(await asyncio.to_thread(read_when_created, job_root / f"child-gpu-{index}.pid"))
                for index in range(2)
            ]
            if cancel_explicitly:
                response = await client.post(f"/api/v1/jobs/{job_id}/cancel")
                assert response.status_code == 200
                terminal = await cast(JobManager, app.state.job_manager).wait_for_terminal(
                    job_id, max_wait_seconds=5
                )
            else:
                terminal = await cast(JobManager, app.state.job_manager).wait_for_terminal(
                    job_id, max_wait_seconds=5
                )
            scheduler = cast(GpuScheduler, app.state.gpu_scheduler)

    assert terminal.status is (JobStatus.CANCELLED if cancel_explicitly else JobStatus.FAILED)
    if not cancel_explicitly:
        assert terminal.error is not None and "wall-time limit" in terminal.error
    for child_pid in child_pids:
        await assert_process_stopped(child_pid)
    assert scheduler.reserved_uuids == set()


async def test_gpu_reservation_is_released_when_runner_setup_fails(
    render_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    devices = [GpuDevice(id=0, uuid="GPU-stable-a", name="GPU A", memory_total_bytes=24 * 1024**3)]
    monkeypatch.setattr(GpuDiscovery, "discover", lambda self: devices)

    async def fail_run(self: SandboxRunner, *args: object, **kwargs: object) -> None:
        raise RunnerError("synthetic cgroup setup failure")

    monkeypatch.setattr(SandboxRunner, "run", fail_run)
    app = create_app(render_settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.post("/api/v1/blender/versions/5.2.0/activate")).status_code == 200
            payload = job_payload("GPU setup failure")
            payload.update({"device": "CUDA", "gpu_ids": [0]})
            created = await client.post("/api/v1/jobs", json=payload)
            job_id = UUID(created.json()["id"])
            assert (
                await client.post(
                    f"/api/v1/jobs/{job_id}/uploads",
                    files={
                        "file": ("scene.blend", b"BLENDER-v300 FAST", "application/octet-stream")
                    },
                )
            ).status_code == 200
            assert (await client.post(f"/api/v1/jobs/{job_id}/start")).status_code == 200
            manager = cast(JobManager, app.state.job_manager)
            failed = await manager.wait_for_terminal(job_id, max_wait_seconds=3)
            scheduler = cast(GpuScheduler, app.state.gpu_scheduler)

    assert failed.status is JobStatus.FAILED
    assert failed.error == "synthetic cgroup setup failure"
    assert scheduler.reserved_uuids == set()


async def test_wall_time_limit_fails_job_and_clears_process(
    render_settings: Settings,
) -> None:
    timeout_settings = render_settings.model_copy(update={"render_timeout_seconds": 1})
    app = create_app(timeout_settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            job_id = await create_ready_job(client, "Timeout", b"BLENDER-v300 SLOW")
            assert (await client.post(f"/api/v1/jobs/{job_id}/start")).status_code == 200
            manager = cast(JobManager, app.state.job_manager)
            failed = await manager.wait_for_terminal(job_id, max_wait_seconds=4)

    assert failed.status is JobStatus.FAILED
    assert failed.error == "Render exceeded its wall-time limit"
    assert failed.process_pid is None
    assert failed.exit_code is not None and failed.exit_code != 0
