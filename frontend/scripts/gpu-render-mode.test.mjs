import assert from "node:assert/strict";
import test from "node:test";

import {
  ALL_GPUS_PER_FRAME,
  FRAME_PER_GPU,
  normalizeGpuRenderMode,
  supportsFramePerGpu,
} from "../src/gpuRenderMode.js";

test("frame-per-GPU is available only for CUDA or OptiX with at least two GPUs", () => {
  assert.equal(supportsFramePerGpu("OptiX", [0, 1]), true);
  assert.equal(supportsFramePerGpu("CUDA", [0, 1, 2]), true);
  assert.equal(supportsFramePerGpu("CPU", [0, 1]), false);
  assert.equal(supportsFramePerGpu("OPTIX", [0]), false);
});

test("hidden incompatible render modes normalize before entering an API payload", () => {
  assert.equal(normalizeGpuRenderMode("CPU", [], FRAME_PER_GPU), ALL_GPUS_PER_FRAME);
  assert.equal(normalizeGpuRenderMode("CUDA", [0], FRAME_PER_GPU), ALL_GPUS_PER_FRAME);
  assert.equal(
    normalizeGpuRenderMode("OptiX", [0, 1], FRAME_PER_GPU),
    FRAME_PER_GPU,
  );
  assert.equal(
    normalizeGpuRenderMode("CUDA", [0, 1], ALL_GPUS_PER_FRAME),
    ALL_GPUS_PER_FRAME,
  );
});
