from __future__ import annotations

import asyncio
import json
import runpy
import shutil
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.blender.command import ADAPTER_ROOT
from app.blender.devices import GpuDevice, GpuDiscovery, GpuScheduler
from app.blender.inspection import InspectionError, choose_configuration
from app.blender.inspection_schemas import DeviceCapabilities, SceneMetadata
from app.config import Settings
from app.jobs.analysis import SceneAnalysisService
from app.main import create_app
from tests.test_job_api import job_payload
from tests.test_job_manager import assert_process_stopped
from tests.test_uploads import zip_bytes

GPUS = [GpuDevice(0, "GPU-A", "GPU A", 12 * 1024**3), GpuDevice(1, "MIG-B", "GPU B", 24 * 1024**3)]
CAPABILITIES = {gpu.uuid: DeviceCapabilities(backends=["OPTIX", "CUDA"]) for gpu in GPUS}


def metadata(**changes: Any) -> SceneMetadata:
    return SceneMetadata.model_validate(
        {
            "engine": "CYCLES",
            "frame_start": 10,
            "frame_end": 18,
            "frame_step": 2,
            "resolution_x": 1920,
            "resolution_y": 1080,
            "resolution_percentage": 75,
            "camera": "Camera",
            "use_osl": False,
            "use_guiding": False,
            **changes,
        }
    )


def scene(**changes: Any) -> bytes:
    return ("BLENDER\n" + metadata(**changes).model_dump_json()).encode()


@pytest.fixture
async def analysis_client(
    job_settings: Settings,
    job_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    binary = job_settings.bundled_blender_root / "5.2.0" / "blender"
    shutil.copy2(Path(__file__).parent / "fixtures" / "fake-inspector", binary)
    binary.chmod(0o755)
    monkeypatch.setattr(GpuDiscovery, "discover", lambda _self: list(GPUS))
    async with job_app.router.lifespan_context(job_app):
        await cast(GpuScheduler, job_app.state.gpu_scheduler).initialize()
        async with AsyncClient(
            transport=ASGITransport(app=job_app), base_url="http://test"
        ) as client:
            assert (await client.post("/api/v1/blender/versions/5.2.0/activate")).status_code == 200
            yield client


async def upload(client: AsyncClient, content: bytes, job_id: str | None = None) -> dict[str, Any]:
    if job_id is None:
        response = await client.post("/api/v1/jobs", json=job_payload())
        assert response.status_code == 201
        job_id = response.json()["id"]
    response = await client.post(
        f"/api/v1/jobs/{job_id}/uploads",
        data={"auto_configure": "true"},
        files={"file": ("scene.blend", content, "application/octet-stream")},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


async def wait_analysis(client: AsyncClient, job_id: str) -> dict[str, Any]:
    async with asyncio.timeout(6):
        while True:
            job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
            if job["analysis_status"] not in {"pending", "running"}:
                return cast(dict[str, Any], job)
            await asyncio.sleep(0.02)


async def child_pid(settings: Settings, job_id: str) -> int:
    marker = settings.jobs_root / job_id / "inspection-child.pid"
    async with asyncio.timeout(4):
        while not marker.exists():  # noqa: ASYNC110 - marker is written by an external process
            await asyncio.sleep(0.02)
    return int(marker.read_text())


@pytest.mark.parametrize(
    ("changes", "devices", "caps", "device", "ids", "mode", "frames"),
    [
        ({}, GPUS, CAPABILITIES, "OPTIX", [1, 0], "FRAME_PER_GPU", "RANGE"),
        ({"frame_end": 10}, GPUS, CAPABILITIES, "OPTIX", [1, 0], "ALL_GPUS_PER_FRAME", "SINGLE"),
        ({"frame_end": 11}, GPUS, CAPABILITIES, "OPTIX", [1, 0], "ALL_GPUS_PER_FRAME", "SINGLE"),
        ({}, [], {}, "CPU", [], "ALL_GPUS_PER_FRAME", "RANGE"),
        ({"use_guiding": True}, GPUS, CAPABILITIES, "CPU", [], "ALL_GPUS_PER_FRAME", "RANGE"),
        (
            {"use_osl": True},
            GPUS,
            {"GPU-A": DeviceCapabilities(backends=["CUDA"])},
            "CPU",
            [],
            "ALL_GPUS_PER_FRAME",
            "RANGE",
        ),
        (
            {},
            GPUS,
            {"GPU-A": DeviceCapabilities(backends=["CUDA"])},
            "CUDA",
            [0],
            "ALL_GPUS_PER_FRAME",
            "RANGE",
        ),
        (
            {"engine": "BLENDER_EEVEE_NEXT"},
            GPUS,
            CAPABILITIES,
            "CPU",
            [],
            "ALL_GPUS_PER_FRAME",
            "RANGE",
        ),
        ({"frame_start": -2}, GPUS, CAPABILITIES, "OPTIX", [1, 0], "FRAME_PER_GPU", "ALL"),
        ({"frame_start": 0}, GPUS, CAPABILITIES, "OPTIX", [1, 0], "FRAME_PER_GPU", "ALL"),
    ],
)
def test_configuration_rules(
    changes: dict[str, Any],
    devices: list[GpuDevice],
    caps: dict[str, DeviceCapabilities],
    device: str,
    ids: list[int],
    mode: str,
    frames: str,
) -> None:
    result = choose_configuration(
        metadata(**changes), name="Scene", devices=devices, capabilities=caps
    )
    assert result.device == device
    assert result.gpu_ids == ids
    assert result.gpu_render_mode == mode
    assert result.frame_mode == frames


def test_custom_engine_is_not_silently_replaced() -> None:
    with pytest.raises(InspectionError, match="engine"):
        choose_configuration(metadata(engine="CUSTOM"), name="Scene", devices=[], capabilities={})


async def test_upload_auto_configures_and_manual_changes_persist(
    analysis_client: AsyncClient,
    job_settings: Settings,
) -> None:
    uploaded = await upload(analysis_client, scene())
    assert uploaded["analysis_status"] == "pending"
    job_id = uploaded["id"]
    result = await wait_analysis(analysis_client, job_id)
    assert result["analysis_status"] == "completed", result
    assert result["auto_configured"] is True
    assert result["gpu_ids"] == [1, 0]
    assert result["device"] == "OPTIX"
    assert result["frame_start"] == 10 and result["frame_end"] == 18
    assert result["scene_metadata"]["frame_step"] == 2
    assert (job_settings.jobs_root / job_id / "input/scene.blend").read_bytes() == scene()
    assert list((job_settings.jobs_root / job_id / "temp").iterdir()) == []
    settings = {
        key: result[key]
        for key in (
            "name",
            "engine",
            "device",
            "gpu_ids",
            "gpu_render_mode",
            "frame_mode",
            "frame_start",
            "frame_end",
        )
    }
    settings.update(device="CPU", gpu_ids=[], gpu_render_mode="ALL_GPUS_PER_FRAME")
    changed = await analysis_client.put(f"/api/v1/jobs/{job_id}", json=settings)
    assert changed.status_code == 200
    assert changed.json()["auto_configured"] is False
    reloaded = (await analysis_client.get(f"/api/v1/jobs/{job_id}")).json()
    assert reloaded["device"] == "CPU"
    assert reloaded["scene_metadata"] == result["scene_metadata"]


async def test_replacement_cancels_old_reader_and_blocks_start_and_edits(
    analysis_client: AsyncClient,
    job_settings: Settings,
) -> None:
    first = await upload(analysis_client, b"BLENDER\nHANG")
    job_id = first["id"]
    pid = await child_pid(job_settings, job_id)
    for method, suffix, payload in (("POST", "/start", None), ("PUT", "", job_payload())):
        response = await analysis_client.request(
            method, f"/api/v1/jobs/{job_id}{suffix}", json=payload
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "scene_analysis_pending"
    second = await upload(analysis_client, scene(frame_start=41, frame_end=41), job_id)
    assert second["analysis_id"] != first["analysis_id"]
    await assert_process_stopped(pid)
    result = await wait_analysis(analysis_client, job_id)
    assert result["analysis_status"] == "completed"
    assert result["analysis_id"] == second["analysis_id"]
    assert result["frame_mode"] == "SINGLE" and result["frame_start"] == 41


async def test_delete_stops_analysis_before_removing_input(
    analysis_client: AsyncClient,
    job_settings: Settings,
) -> None:
    job_id = (await upload(analysis_client, b"BLENDER\nHANG"))["id"]
    pid = await child_pid(job_settings, job_id)
    response = await analysis_client.delete(f"/api/v1/jobs/{job_id}")
    assert response.status_code == 204
    await assert_process_stopped(pid)
    assert not (job_settings.jobs_root / job_id).exists()


async def test_rejected_replacement_resumes_analysis_of_original_file(
    analysis_client: AsyncClient,
    job_settings: Settings,
    job_app: FastAPI,
) -> None:
    first = await upload(analysis_client, b"BLENDER\nHANG")
    job_id = first["id"]
    pid = await child_pid(job_settings, job_id)
    rejected = await analysis_client.post(
        f"/api/v1/jobs/{job_id}/uploads",
        data={"auto_configure": "true"},
        files={"file": ("bad.blend", b"not a blend file", "application/octet-stream")},
    )
    assert rejected.status_code == 422
    await assert_process_stopped(pid)
    service = cast(SceneAnalysisService, job_app.state.scene_analysis_service)
    assert UUID(job_id) in service._tasks
    saved = (await analysis_client.get(f"/api/v1/jobs/{job_id}")).json()
    assert saved["analysis_id"] == first["analysis_id"]
    assert (job_settings.jobs_root / job_id / "input/scene.blend").read_bytes() == b"BLENDER\nHANG"
    result = await upload(analysis_client, scene(), job_id)
    assert (await wait_analysis(analysis_client, result["id"]))["analysis_status"] == "completed"


async def test_nested_zip_scene_is_analyzed_without_losing_assets(
    analysis_client: AsyncClient,
    job_settings: Settings,
) -> None:
    created = await analysis_client.post("/api/v1/jobs", json=job_payload())
    job_id = created.json()["id"]
    response = await analysis_client.post(
        f"/api/v1/jobs/{job_id}/uploads",
        data={"auto_configure": "true"},
        files={
            "file": (
                "scene.zip",
                zip_bytes(
                    [
                        ("project/main.blend", scene(engine="BLENDER_EEVEE_NEXT")),
                        ("project/textures/asset", b"texture"),
                    ]
                ),
                "application/zip",
            )
        },
    )
    assert response.status_code == 200, response.text
    result = await wait_analysis(analysis_client, job_id)
    assert result["analysis_status"] == "completed", result
    assert result["engine"] == "BLENDER_EEVEE" and result["gpu_ids"] == []
    assert (
        job_settings.jobs_root / job_id / "input/project/textures/asset"
    ).read_bytes() == b"texture"


@pytest.mark.parametrize("content", [b"BLENDER\nFAIL", b"BLENDER\nMALFORMED"])
async def test_analysis_failure_preserves_upload_and_allows_manual_setup(
    analysis_client: AsyncClient,
    job_settings: Settings,
    content: bytes,
) -> None:
    job_id = (await upload(analysis_client, content))["id"]
    result = await wait_analysis(analysis_client, job_id)
    assert result["analysis_status"] == "failed"
    assert result["status"] == "ready" and result["analysis_error"]
    assert (job_settings.jobs_root / job_id / "input/scene.blend").read_bytes() == content
    assert (
        await analysis_client.put(f"/api/v1/jobs/{job_id}", json=job_payload())
    ).status_code == 200


async def test_timeout_cleans_up_process_and_temporary_files(
    analysis_client: AsyncClient,
    job_settings: Settings,
    job_app: FastAPI,
) -> None:
    service = cast(SceneAnalysisService, job_app.state.scene_analysis_service)
    service._inspector._timeout_seconds = 0.5
    job_id = (await upload(analysis_client, b"BLENDER\nHANG"))["id"]
    pid = await child_pid(job_settings, job_id)
    result = await wait_analysis(analysis_client, job_id)
    assert result["analysis_status"] == "failed"
    assert "timed out" in result["analysis_error"]
    await assert_process_stopped(pid)
    assert list((job_settings.jobs_root / job_id / "temp").iterdir()) == []


async def test_shutdown_and_restart_recover_pending_analysis(
    analysis_client: AsyncClient,
    job_settings: Settings,
    job_app: FastAPI,
) -> None:
    job_id = (await upload(analysis_client, b"BLENDER\nHANG"))["id"]
    pid = await child_pid(job_settings, job_id)
    await cast(SceneAnalysisService, job_app.state.scene_analysis_service).shutdown()
    await assert_process_stopped(pid)
    restarted = create_app(job_settings)
    async with restarted.router.lifespan_context(restarted):
        async with AsyncClient(
            transport=ASGITransport(app=restarted), base_url="http://test"
        ) as client:
            result = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert result["analysis_status"] == "failed"
    assert "restart" in result["analysis_error"]
    assert (job_settings.jobs_root / job_id / "input/scene.blend").exists()


def test_scene_script_reads_settings_without_modifying_scene(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    loaded = SimpleNamespace(
        render=SimpleNamespace(
            engine="CYCLES", resolution_x=2048, resolution_y=1024, resolution_percentage=50
        ),
        cycles=SimpleNamespace(shading_system=True, use_guiding=False),
        frame_start=-3,
        frame_end=9,
        frame_step=2,
        camera=SimpleNamespace(name="Camera"),
    )
    bpy = SimpleNamespace(
        context=SimpleNamespace(scene=loaded),
        data=SimpleNamespace(version=(4, 5, 11)),
        app=SimpleNamespace(version=(4, 5, 11)),
    )
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    runpy.run_path(str(ADAPTER_ROOT / "inspect_scene.py"))
    result = json.loads(capsys.readouterr().out.removeprefix("RENDER_NODE_INSPECTION "))
    assert result["frame_start"] == -3 and result["use_osl"] is True
    assert loaded.render.resolution_percentage == 50
    assert loaded.frame_start == -3 and loaded.frame_step == 2
    bpy.data.version = (5, 2, 0)
    with pytest.raises(RuntimeError, match="newer"):
        runpy.run_path(str(ADAPTER_ROOT / "inspect_scene.py"))
