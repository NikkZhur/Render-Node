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
            (14, 18, 8, "total=3 worker=1 assigned=1 start=10 end=18 step=4"),
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
