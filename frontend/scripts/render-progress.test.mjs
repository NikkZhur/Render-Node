import assert from "node:assert/strict";
import test from "node:test";

import { buildProgressRows, mergeRenderProgress } from "../src/renderProgress.js";

test("maps backend sample progress to the current frame snapshot", () => {
  assert.deepEqual(
    mergeRenderProgress(undefined, {
      frame: 8,
      progress: 0.390625,
      sample: 200,
      total_samples: 512,
    }),
    {
      frame: 8,
      progress: 39,
      sample: 200,
      totalSamples: 512,
      workersByGpu: {},
    },
  );
});

test("keeps samples through same-frame updates without a sample payload", () => {
  const previous = { frame: 8, progress: 39, sample: 200, totalSamples: 512 };

  assert.deepEqual(
    mergeRenderProgress(previous, { frame: 8, progress: 0.5 }),
    { frame: 8, progress: 50, sample: 200, totalSamples: 512, workersByGpu: {} },
  );
});

test("clears stale samples when Blender advances to another frame", () => {
  const previous = { frame: 8, progress: 50, sample: 256, totalSamples: 512 };

  assert.deepEqual(
    mergeRenderProgress(previous, { frame: 9, progress: 0.51 }),
    { frame: 9, progress: 51, sample: null, totalSamples: null, workersByGpu: {} },
  );
});

test("keeps independent worker snapshots mapped by stable GPU UUID", () => {
  const first = mergeRenderProgress(undefined, {
    gpu_uuid: "GPU-b",
    frame: 2,
    progress: 0.2,
    worker_progress: 0.5,
    sample: 128,
    total_samples: 512,
  });
  const second = mergeRenderProgress(first, {
    gpu_uuid: "GPU-a",
    frame: 1,
    progress: 0.3,
    worker_progress: 0.75,
  });

  assert.equal(second.progress, 30);
  assert.deepEqual(second.workersByGpu["GPU-b"], {
    frame: 2,
    progress: 50,
    sample: 128,
    totalSamples: 512,
  });
  assert.equal(second.workersByGpu["GPU-a"].progress, 75);
});

const devices = [
  { id: 0, uuid: "GPU-a", name: "GPU A" },
  { id: 1, uuid: "GPU-b", name: "GPU B" },
];
const baseJob = {
  status: "rendering",
  progress: 30,
  current_frame: 2,
  device: "OptiX",
  gpu_ids: [0, 1],
  frame_mode: "RANGE",
  frame_start: 1,
  frame_end: 8,
  started_at: "2026-08-18T12:00:00Z",
};

test("uses one compact overall bar for CPU or one actual worker", () => {
  const cpu = buildProgressRows({
    ...baseJob,
    device: "CPU",
    gpu_ids: [],
    gpu_render_mode: "ALL_GPUS_PER_FRAME",
  }, devices);
  assert.equal(cpu.kind, "single");
  assert.equal(cpu.workers.length, 0);
  assert.equal(cpu.overall.accessibleLabel, "Render progress");

  const singleFrame = buildProgressRows({
    ...baseJob,
    frame_mode: "SINGLE",
    frame_end: null,
    gpu_render_mode: "FRAME_PER_GPU",
  }, devices);
  assert.equal(singleFrame.kind, "single");
});

test("uses one truthful All GPUs row for a joint Blender process", () => {
  const rows = buildProgressRows({ ...baseJob, gpu_render_mode: "ALL_GPUS_PER_FRAME" }, devices);
  assert.equal(rows.kind, "all-gpus");
  assert.equal(rows.workers.length, 1);
  assert.equal(rows.workers[0].label, "All GPUs");
  assert.match(rows.workers[0].accessibleLabel, /GPU-a, GPU-b/);
  assert.equal(rows.overall, null);
});

test("orders independent worker rows by selected GPUs and appends Overall", () => {
  const snapshot = {
    frame: 2,
    progress: 40,
    workersByGpu: {
      "GPU-b": { frame: 2, progress: 25, sample: 64, totalSamples: 512 },
      "GPU-a": { frame: 1, progress: 75, sample: 384, totalSamples: 512 },
    },
  };
  const rows = buildProgressRows({ ...baseJob, gpu_render_mode: "FRAME_PER_GPU" }, devices, snapshot);
  assert.equal(rows.kind, "distributed");
  assert.deepEqual(rows.workers.map((worker) => worker.gpuUuid), ["GPU-a", "GPU-b"]);
  assert.deepEqual(rows.workers.map((worker) => worker.progress), [75, 25]);
  assert.equal(rows.overall.label, "Overall");
  assert.equal(rows.overall.progress, 40);
  assert.notEqual(rows.workers[0].accessibleLabel, rows.workers[1].accessibleLabel);
});

test("restores Overall from REST and leaves known workers waiting after reload", () => {
  const rows = buildProgressRows({
    ...baseJob,
    progress: 42,
    gpu_render_mode: "FRAME_PER_GPU",
  }, devices);
  assert.equal(rows.overall.progress, 42);
  assert.deepEqual(rows.workers.map((worker) => worker.state), [
    "Waiting for update",
    "Waiting for update",
  ]);
});

test("clamps out-of-range overall progress", () => {
  assert.equal(mergeRenderProgress(undefined, { progress: 1.5 }).progress, 100);
  assert.equal(mergeRenderProgress(undefined, { progress: -0.5 }).progress, 0);
});
