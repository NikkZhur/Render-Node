"""Probe one UUID selected through CUDA_VISIBLE_DEVICES in a clean runtime."""

import json
import logging

import bpy

backends = []
preferences = bpy.context.preferences.addons["cycles"].preferences
for backend in ("OPTIX", "CUDA"):
    try:
        preferences.compute_device_type = backend
        preferences.get_devices()
        if any(device.type == backend for device in preferences.devices):
            backends.append(backend)
    except Exception:
        # Driver/backend failures must not disguise a working alternative.
        logging.debug("Device backend %s is unavailable", backend, exc_info=True)
print("RENDER_NODE_INSPECTION " + json.dumps({"backends": backends}), flush=True)
