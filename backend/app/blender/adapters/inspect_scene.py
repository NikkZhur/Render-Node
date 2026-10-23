"""Read the loaded scene without changing or saving its render settings."""

import json

import bpy

if tuple(bpy.data.version[:2]) > tuple(bpy.app.version[:2]):
    raise RuntimeError("Scene was saved with a newer Blender version")

scene = bpy.context.scene
cycles = getattr(scene, "cycles", None)
print(
    "RENDER_NODE_INSPECTION "
    + json.dumps(
        {
            "engine": scene.render.engine,
            "frame_start": scene.frame_start,
            "frame_end": scene.frame_end,
            "frame_step": max(scene.frame_step, 1),
            "resolution_x": scene.render.resolution_x,
            "resolution_y": scene.render.resolution_y,
            "resolution_percentage": scene.render.resolution_percentage,
            "camera": scene.camera.name if scene.camera else None,
            "use_osl": bool(getattr(cycles, "shading_system", False)),
            "use_guiding": bool(getattr(cycles, "use_guiding", False)),
        }
    ),
    flush=True,
)
