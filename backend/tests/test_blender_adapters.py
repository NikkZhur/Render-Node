from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.blender.command import FRAME_DISTRIBUTION_SCRIPT, VERSION_SCRIPTS


class FakeCyclesPreferences:
    def __init__(self) -> None:
        self.compute_device_type = "NONE"
        self.devices = [
            SimpleNamespace(type="CPU", use=True),
            SimpleNamespace(type="CUDA", use=False),
            SimpleNamespace(type="OPTIX", use=False),
        ]
        self.refreshed = False

    def get_devices(self) -> None:
        self.refreshed = True


class ClampedSceneRange:
    """Model Blender RNA setters; loaded/CLI bounds may lie outside their range."""

    max_frame = 1_048_574

    def __init__(self, start: int, end: int, step: int) -> None:
        self._start = start
        self._end = end
        self.frame_step = step

    @property
    def frame_start(self) -> int:
        return self._start

    @frame_start.setter
    def frame_start(self, value: int) -> None:
        self._start = min(max(value, 0), self.max_frame)
        self._end = max(self._start, self._end)

    @property
    def frame_end(self) -> int:
        return self._end

    @frame_end.setter
    def frame_end(self, value: int) -> None:
        self._end = min(max(value, 0), self.max_frame)
        self._start = min(self._start, self._end)


def fake_bpy() -> tuple[SimpleNamespace, FakeCyclesPreferences]:
    preferences = FakeCyclesPreferences()
    module = SimpleNamespace(
        context=SimpleNamespace(
            scene=SimpleNamespace(
                render=SimpleNamespace(engine=""),
                cycles=SimpleNamespace(device=""),
                frame_start=1,
                frame_end=250,
                frame_step=1,
            ),
            preferences=SimpleNamespace(
                addons={"cycles": SimpleNamespace(preferences=preferences)}
            ),
        )
    )
    return module, preferences


@pytest.mark.parametrize("script", VERSION_SCRIPTS.values())
def test_version_adapter_selects_only_requested_visible_gpu(
    script: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bpy, preferences = fake_bpy()
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "blender",
            "--",
            "--render-node-engine",
            "CYCLES",
            "--cycles-device",
            "OPTIX",
        ],
    )

    runpy.run_path(str(script))

    assert bpy.context.scene.render.engine == "CYCLES"
    assert bpy.context.scene.cycles.device == "GPU"
    assert preferences.compute_device_type == "OPTIX"
    assert preferences.refreshed is True
    assert [device.use for device in preferences.devices] == [False, False, True]


@pytest.mark.parametrize("script", VERSION_SCRIPTS.values())
def test_version_adapter_cpu_mode_does_not_initialize_gpu_backend(
    script: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bpy, preferences = fake_bpy()
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "blender",
            "--",
            "--render-node-engine",
            "CYCLES",
            "--cycles-device",
            "CPU",
        ],
    )

    runpy.run_path(str(script))

    assert bpy.context.scene.cycles.device == "CPU"
    assert preferences.compute_device_type == "NONE"
    assert preferences.refreshed is False


def test_blender_4_5_adapter_maps_stable_eevee_engine_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bpy, preferences = fake_bpy()
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "blender",
            "--",
            "--render-node-engine",
            "BLENDER_EEVEE",
            "--cycles-device",
            "CPU",
        ],
    )

    runpy.run_path(str(VERSION_SCRIPTS["4.5.11"]))

    assert bpy.context.scene.render.engine == "BLENDER_EEVEE_NEXT"
    assert preferences.compute_device_type == "NONE"
    assert preferences.refreshed is False


@pytest.mark.parametrize(
    ("mode_arguments", "scene_range", "expected"),
    [
        (
            [
                "--render-node-frame-mode",
                "RANGE",
                "--render-node-frame-start",
                "1",
                "--render-node-frame-end",
                "8",
            ],
            (20, 40, 2),
            (3, 8, 4, "total=4 worker=1 assigned=2 start=1 end=8 step=2"),
        ),
        (
            ["--render-node-frame-mode", "ALL"],
            (10, 18, 4),
            (14, 14, 8, "total=3 worker=1 assigned=1 start=10 end=18 step=4"),
        ),
    ],
)
def test_frame_distribution_uses_scene_step_without_gaps_or_overlap(
    mode_arguments: list[str],
    scene_range: tuple[int, int, int],
    expected: tuple[int, int, int, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bpy, _ = fake_bpy()
    bpy.context.scene.frame_start, bpy.context.scene.frame_end, bpy.context.scene.frame_step = (
        scene_range
    )
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "blender",
            "--",
            *mode_arguments,
            "--render-node-worker-index",
            "1",
            "--render-node-worker-count",
            "2",
        ],
    )

    runpy.run_path(str(FRAME_DISTRIBUTION_SCRIPT))

    start, end, step, marker = expected
    assert (
        bpy.context.scene.frame_start,
        bpy.context.scene.frame_end,
        bpy.context.scene.frame_step,
    ) == (
        start,
        end,
        step,
    )
    assert marker in capsys.readouterr().out


@pytest.mark.parametrize(
    ("mode_arguments", "scene_range", "expected_range", "marker"),
    [
        (
            ["--render-node-frame-mode", "ALL"],
            (-2, 3, 2),
            (-2, 3, 2),
            "total=3 worker=0 assigned=3 start=-2 end=3 step=2",
        ),
        (
            [
                "--render-node-frame-mode",
                "RANGE",
                "--render-node-frame-start",
                "2",
                "--render-node-frame-end",
                "9",
            ],
            (1, 250, 3),
            (2, 9, 3),
            "total=3 worker=0 assigned=3 start=2 end=9 step=3",
        ),
    ],
)
def test_single_process_reports_real_scene_plan_before_animation(
    mode_arguments: list[str],
    scene_range: tuple[int, int, int],
    expected_range: tuple[int, int, int],
    marker: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bpy, _ = fake_bpy()
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end, scene.frame_step = scene_range
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setattr(sys, "argv", ["blender", "--", *mode_arguments])

    runpy.run_path(str(FRAME_DISTRIBUTION_SCRIPT))

    assert (scene.frame_start, scene.frame_end, scene.frame_step) == expected_range
    assert marker in capsys.readouterr().out


@pytest.mark.parametrize("bounds", [(-2, 3), (-4, -1)])
def test_all_frames_does_not_reassign_and_clamp_loaded_scene_bounds(
    bounds: tuple[int, int], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scene = ClampedSceneRange(*bounds, step=2)
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace(context=SimpleNamespace(scene=scene)))
    monkeypatch.setattr(sys, "argv", ["blender", "--", "--render-node-frame-mode", "ALL"])

    runpy.run_path(str(FRAME_DISTRIBUTION_SCRIPT))

    assert (scene.frame_start, scene.frame_end) == bounds
    total = (bounds[1] - bounds[0]) // scene.frame_step + 1
    assert f"total={total} worker=0 assigned={total}" in capsys.readouterr().out


@pytest.mark.parametrize("bounds", [(-3, 9), (-4, -1)])
@pytest.mark.parametrize("worker_index", [0, 1])
def test_distributed_negative_scene_frames_fail_before_changing_bounds(
    bounds: tuple[int, int], worker_index: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = ClampedSceneRange(*bounds, step=2)
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace(context=SimpleNamespace(scene=scene)))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "blender",
            "--",
            "--render-node-frame-mode",
            "ALL",
            "--render-node-worker-index",
            str(worker_index),
            "--render-node-worker-count",
            "2",
        ],
    )

    with pytest.raises(RuntimeError, match="All GPUs per frame"):
        runpy.run_path(str(FRAME_DISTRIBUTION_SCRIPT))

    assert (scene.frame_start, scene.frame_end, scene.frame_step) == (*bounds, 2)


def test_range_frame_plan_uses_bounds_after_blender_clamps_them(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scene = ClampedSceneRange(1, 250, step=3)
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace(context=SimpleNamespace(scene=scene)))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "blender",
            "--",
            "--render-node-frame-mode",
            "RANGE",
            "--render-node-frame-start",
            str(scene.max_frame - 1),
            "--render-node-frame-end",
            str(scene.max_frame + 5),
        ],
    )

    runpy.run_path(str(FRAME_DISTRIBUTION_SCRIPT))

    assert (scene.frame_start, scene.frame_end, scene.frame_step) == (
        scene.max_frame - 1,
        scene.max_frame,
        3,
    )
    assert (
        f"total=1 worker=0 assigned=1 start={scene.max_frame - 1} end={scene.max_frame} step=3"
        in capsys.readouterr().out
    )
