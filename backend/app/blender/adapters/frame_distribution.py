"""Configure one Blender process with its deterministic round-robin frame slice."""

import sys

import bpy


def _value(arguments, name):
    return arguments[arguments.index(name) + 1]


arguments = sys.argv[sys.argv.index("--") + 1 :]
frame_mode = _value(arguments, "--render-node-frame-mode")
worker_index = int(_value(arguments, "--render-node-worker-index"))
worker_count = int(_value(arguments, "--render-node-worker-count"))
scene = bpy.context.scene
scene_step = max(int(scene.frame_step), 1)

if frame_mode == "RANGE":
    frame_start = int(_value(arguments, "--render-node-frame-start"))
    frame_end = int(_value(arguments, "--render-node-frame-end"))
else:
    frame_start = int(scene.frame_start)
    frame_end = int(scene.frame_end)

total = 0 if frame_end < frame_start else ((frame_end - frame_start) // scene_step) + 1
first_frame = frame_start + worker_index * scene_step
worker_step = scene_step * worker_count
assigned = 0 if first_frame > frame_end else ((frame_end - first_frame) // worker_step) + 1

print(
    "RENDER_NODE_FRAME_PLAN"
    f" total={total} worker={worker_index} assigned={assigned}"
    f" start={frame_start} end={frame_end} step={scene_step}",
    flush=True,
)

if assigned == 0:
    raise SystemExit(0)

scene.frame_start = first_frame
scene.frame_end = frame_end
scene.frame_step = worker_step
