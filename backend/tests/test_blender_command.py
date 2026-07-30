from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from app.blender.command import CommandBuildError, build_render_command
from app.blender.sandbox import (
    SandboxPolicy,
    SandboxUnavailableError,
    build_worker_environment,
)
from app.config import Environment
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


async def test_worker_environment_is_allowlisted_and_gpu_scoped(tmp_path: Path) -> None:
    job_root = tmp_path / "job"
    (job_root / "temp").mkdir(parents=True)

    environment = await build_worker_environment(
        job_root,
        device=ComputeDevice.OPTIX,
        gpu_ids=[1, 3],
    )

    assert environment["CUDA_VISIBLE_DEVICES"] == "1,3"
    assert environment["PYTHONPATH"] == ""
    assert environment["PYTHONHOME"] == ""
    assert "DATABASE_URL" not in environment
    assert "TOKEN" not in environment
    assert await asyncio.to_thread(Path(environment["HOME"]).is_dir)


def test_production_sandbox_is_fail_closed() -> None:
    policy = SandboxPolicy(environment=Environment.PRODUCTION, allow_unsandboxed=False)
    with pytest.raises(SandboxUnavailableError, match="Production render sandbox"):
        policy.ensure_startup_ready(scheduler_enabled=True)
    with pytest.raises(SandboxUnavailableError, match="Local runner is disabled"):
        policy.ensure_local_runner_allowed()

    development = SandboxPolicy(environment=Environment.DEVELOPMENT, allow_unsandboxed=True)
    development.ensure_local_runner_allowed()
