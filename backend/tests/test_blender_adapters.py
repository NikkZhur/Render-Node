from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.blender.command import VERSION_SCRIPTS


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
