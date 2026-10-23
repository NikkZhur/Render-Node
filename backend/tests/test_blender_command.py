from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from app.blender.command import (
    BlenderExecutableResolver,
    CommandBuildError,
    FrameWorker,
    build_render_command,
)
from app.blender.sandbox import build_worker_environment
from app.blender.types import RuntimeSource
from app.jobs.models import Job
from app.jobs.types import ComputeDevice, FrameMode, JobStatus, RenderEngine


def make_job(*, version: str = "4.5.11", scene_path: str = "input/scene.blend") -> Job:
    return Job(
        id=uuid4(),
        name="Command test",
        scene_path=scene_path,
        status=JobStatus.QUEUED,
        blender_version=version,
        engine=RenderEngine.CYCLES,
        device=ComputeDevice.CUDA,
        gpu_ids=[0, 2],
        frame_mode=FrameMode.RANGE,
        frame_start=3,
        frame_end=5,
    )


def test_command_is_backend_owned_and_contains_mandatory_safety_flags(tmp_path: Path) -> None:
    job = make_job()
    job_root = tmp_path / str(job.id)
    (job_root / "input").mkdir(parents=True)
    (job_root / "input" / "scene.blend").write_bytes(b"BLENDER")
    for child in ("output", "logs", "temp"):
        (job_root / child).mkdir()

    command = build_render_command(job, binary=Path("/trusted/blender"), jobs_root=tmp_path)

    assert command.arguments[0] == "/trusted/blender"
    assert "--factory-startup" in command.arguments
    assert "--disable-autoexec" in command.arguments
    assert ("--python-exit-code", "1") == command.arguments[7:9]
    assert "--enable-autoexec" not in command.arguments
    assert "--python-expr" not in command.arguments
    assert command.arguments[-4:] == (
        "--render-node-engine",
        "CYCLES",
        "--cycles-device",
        "CUDA",
    )
    assert command.arguments[command.arguments.index("--frame-start") + 1] == "3"
    assert command.arguments[command.arguments.index("--frame-end") + 1] == "5"


def test_command_rejects_unverified_adapter_and_escaping_scene(tmp_path: Path) -> None:
    unsupported = make_job(version="4.5.12")
    (tmp_path / str(unsupported.id)).mkdir()
    with pytest.raises(CommandBuildError, match="no verified adapter"):
        build_render_command(unsupported, binary=Path("/blender"), jobs_root=tmp_path)

    escaping = make_job(scene_path="../neighbor/scene.blend")
    (tmp_path / str(escaping.id)).mkdir()
    with pytest.raises(ValueError, match="escapes"):
        build_render_command(escaping, binary=Path("/blender"), jobs_root=tmp_path)


def test_frame_worker_has_private_log_and_backend_owned_round_robin_arguments(
    tmp_path: Path,
) -> None:
    job = make_job()
    job_root = tmp_path / str(job.id)
    (job_root / "input").mkdir(parents=True)
    (job_root / "input" / "scene.blend").write_bytes(b"BLENDER")
    for child in ("output", "logs", "temp"):
        (job_root / child).mkdir()

    command = build_render_command(
        job,
        binary=Path("/trusted/blender"),
        jobs_root=tmp_path,
        worker=FrameWorker(index=1, count=2),
    )

    assert command.log_path.name == "blender-gpu-1.log"
    assert "frame_distribution.py" in " ".join(command.arguments)
    assert "--frame-start" not in command.arguments
    assert command.arguments[command.arguments.index("--render-node-worker-index") + 1] == "1"
    assert command.arguments[command.arguments.index("--render-node-worker-count") + 1] == "2"
    assert command.arguments[command.arguments.index("--render-node-frame-start") + 1] == "3"
    assert command.arguments[command.arguments.index("--render-node-frame-end") + 1] == "5"


def test_executable_resolver_uses_the_selected_runtime_version(tmp_path: Path) -> None:
    bundled_root = tmp_path / "bundled"
    installed_root = tmp_path / "installed"
    resolver = BlenderExecutableResolver(
        bundled_root=bundled_root,
        installed_root=installed_root,
    )

    assert resolver.binary("4.1.1", RuntimeSource.BUNDLED) == bundled_root / "4.1.1/blender"
    assert resolver.binary("5.2.0", RuntimeSource.BUNDLED) == bundled_root / "5.2.0/blender"
    assert resolver.binary("4.5.11", RuntimeSource.OFFICIAL) == (installed_root / "4.5.11/blender")


@pytest.mark.parametrize("gpu_uuids", [["GPU-first", "GPU-second"], ["MIG-first", "MIG-second"]])
async def test_worker_environment_is_allowlisted_and_gpu_scoped(
    tmp_path: Path, gpu_uuids: list[str]
) -> None:
    job_root = tmp_path / "job"
    (job_root / "temp").mkdir(parents=True)

    environment = await build_worker_environment(
        job_root,
        device=ComputeDevice.OPTIX,
        gpu_uuids=gpu_uuids,
    )

    assert environment["CUDA_VISIBLE_DEVICES"] == ",".join(gpu_uuids)
    assert environment["PYTHONPATH"] == ""
    assert environment["PYTHONHOME"] == ""
    assert "DATABASE_URL" not in environment
    assert "TOKEN" not in environment
    assert await asyncio.to_thread(Path(environment["HOME"]).is_dir)
