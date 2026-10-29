from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.blender.command import FRAME_DISTRIBUTION_SCRIPT, FrameWorker, build_render_command
from app.blender.devices import GpuDevice
from app.blender.inspection import choose_configuration
from app.blender.inspection_schemas import DeviceCapabilities
from app.jobs.models import Job
from app.jobs.types import GpuRenderMode
from tests.test_blender_adapters import ClampedSceneRange
from tests.test_scene_analysis import metadata


class ClampedSceneSettings(ClampedSceneRange):
    def __setattr__(self, name: str, value: int) -> None:
        if name == "frame_step":
            value = min(max(value, 0), self.max_frame)
        super().__setattr__(name, value)


@pytest.mark.parametrize("gpu_count", [0, 1, 2, 4, 20])
@pytest.mark.parametrize(
    "scene_range",
    [
        (-3, 9, 2),
        (-4, -1, 2),
        (-2, -2, 1),
        (-1_048_574, -1_048_573, 1),
        (0, 0, 1),
        (10, 11, 2),
        (0, 8, 3),
        (10, 18, 2),
        (1_048_573, 1_048_574, 1),
        (0, 1_048_574, 600_000),
        (0, 1_048_574, 524_287),
    ],
)
def test_auto_configuration_renders_saved_frames_without_rna_clamping(
    scene_range: tuple[int, int, int],
    gpu_count: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start, end, step = scene_range
    scene = metadata(frame_start=start, frame_end=end, frame_step=step)
    devices = [GpuDevice(index, f"GPU-{index}", "GPU", 12 * 1024**3) for index in range(gpu_count)]
    capabilities = {gpu.uuid: DeviceCapabilities(backends=["OPTIX"]) for gpu in devices}
    configuration = choose_configuration(
        scene, name="Saved frame range", devices=devices, capabilities=capabilities
    )
    job = Job(
        id=uuid4(),
        scene_path="input/scene.blend",
        blender_version="4.5.11",
        **configuration.model_dump(),
    )
    distributed = job.gpu_render_mode is GpuRenderMode.FRAME_PER_GPU
    count = len(job.gpu_ids) if distributed else 1
    rendered_frames: list[int] = []
    for index in range(count):
        loaded = ClampedSceneSettings(*scene_range)
        command = build_render_command(
            job,
            binary=Path("/trusted/blender"),
            jobs_root=tmp_path,
            worker=FrameWorker(index, count) if distributed else None,
        )
        if "--render-frame" in command.arguments:
            rendered_frames.append(
                int(command.arguments[command.arguments.index("--render-frame") + 1])
            )
            continue
        monkeypatch.setitem(
            sys.modules, "bpy", SimpleNamespace(context=SimpleNamespace(scene=loaded))
        )
        monkeypatch.setattr(sys, "argv", list(command.arguments))
        runpy.run_path(str(FRAME_DISTRIBUTION_SCRIPT))
        rendered_frames.extend(range(loaded.frame_start, loaded.frame_end + 1, loaded.frame_step))

    assert sorted(rendered_frames) == list(range(start, end + 1, step))
