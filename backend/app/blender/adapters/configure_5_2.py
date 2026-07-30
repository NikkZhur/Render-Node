"""Trusted Render Node adapter for Blender 5.2.0."""

import sys

import bpy

arguments = sys.argv[sys.argv.index("--") + 1 :]
engine = arguments[arguments.index("--render-node-engine") + 1]
device = arguments[arguments.index("--cycles-device") + 1]
bpy.context.scene.render.engine = engine
if engine == "CYCLES":
    bpy.context.scene.cycles.device = "CPU" if device == "CPU" else "GPU"
    if device != "CPU":
        preferences = bpy.context.preferences.addons["cycles"].preferences
        preferences.compute_device_type = device
        preferences.get_devices()
        enabled = False
        for available_device in preferences.devices:
            available_device.use = available_device.type == device
            enabled = enabled or available_device.use
        if not enabled:
            raise RuntimeError(f"No visible {device} Cycles device")
