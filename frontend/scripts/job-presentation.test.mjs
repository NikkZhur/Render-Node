import assert from "node:assert/strict";
import test from "node:test";

import {
  ENGINE_API_VALUES,
  applyJobConfiguration,
  presentJob,
} from "../src/jobPresentation.js";

const job = {
  id: "abcd-1234",
  name: "Scene",
  source_filename: "scene.blend",
  progress: 0.256,
  frame_mode: "RANGE",
  frame_start: 3,
  frame_end: 17,
  frame_step: 2,
  engine: "CYCLES",
  device: "OPTIX",
  blender_version: "5.2.0",
  gpu_ids: [0, 1],
  gpu_render_mode: "FRAME_PER_GPU",
  status: "ready",
  created_at: "2026-08-18T12:00:00Z",
};

test("API presentation preserves stored fields and existing display conversions", (t) => {
  t.mock.method(Date, "now", () => Date.parse("2026-08-18T12:02:00Z"));
  assert.deepEqual(presentJob(job), {
    ...job,
    shortId: "ABCD",
    file: "scene.blend",
    progress: 26,
    frame: "3–17",
    engine: "Cycles",
    device: "OptiX",
    version: "5.2.0",
    created: "2 min ago",
  });
  assert.equal(job.progress, 0.256);
  assert.equal(job.engine, "CYCLES");
});

test("API and local edits retain the same engine and device labels and unknown values", () => {
  const engines = [
    ["CYCLES", "Cycles"],
    ["BLENDER_EEVEE", "Eevee"],
    ["BLENDER_WORKBENCH", "Workbench"],
    ["FUTURE_ENGINE", "FUTURE_ENGINE"],
  ];
  const devices = [["CPU", "CPU"], ["CUDA", "CUDA"], ["OPTIX", "OptiX"], ["FUTURE_DEVICE", "FUTURE_DEVICE"]];
  for (const [engine, engineLabel] of engines) {
    for (const [device, deviceLabel] of devices) {
      const configuration = { ...job, engine, device };
      for (const result of [presentJob(configuration), applyJobConfiguration(job, configuration)]) {
        assert.equal(result.engine, engineLabel);
        assert.equal(result.device, deviceLabel);
      }
    }
  }
  assert.deepEqual(ENGINE_API_VALUES, {
    Cycles: "CYCLES",
    Eevee: "BLENDER_EEVEE",
    Workbench: "BLENDER_WORKBENCH",
  });
});

test("API and local edits preserve single, range, all and unknown frame mode formatting", () => {
  for (const [frame_mode, expected] of [
    ["SINGLE", "Frame 3"], ["RANGE", "3–17"], ["ALL", "All frames"], ["UNKNOWN", "All frames"],
  ]) {
    const configuration = { ...job, frame_mode };
    assert.equal(presentJob(configuration).frame, expected);
    assert.equal(applyJobConfiguration(job, configuration).frame, expected);
  }
});

test("local edits change only editable display fields and keep job identity and results", () => {
  const existing = { ...job, file: "original.zip", progress: 47, version: "4.1.1", created: "Yesterday", hasArtifacts: true };
  const configuration = {
    name: "Updated scene",
    engine: "BLENDER_EEVEE",
    device: "CUDA",
    gpu_ids: [1],
    gpu_render_mode: "ALL_GPUS_PER_FRAME",
    frame_mode: "SINGLE",
    frame_start: 7,
    frame_end: null,
    blender_version: "5.2.0",
  };
  assert.deepEqual(applyJobConfiguration(existing, configuration), {
    ...existing,
    name: "Updated scene",
    engine: "Eevee",
    device: "CUDA",
    gpu_ids: [1],
    gpu_render_mode: "ALL_GPUS_PER_FRAME",
    frame: "Frame 7",
    frame_mode: "SINGLE",
    frame_start: 7,
    frame_end: null,
  });
  assert.equal(existing.name, "Scene");
  assert.deepEqual(existing.gpu_ids, [0, 1]);
});

test("API presentation keeps filename fallback and relative creation time boundaries", (t) => {
  const createdAt = Date.parse(job.created_at);
  for (const [seconds, expected] of [
    [-60, "Just now"], [59, "Just now"], [60, "1 min ago"],
    [3599, "59 min ago"], [3600, "1 hr ago"], [86400, "1 days ago"],
  ]) {
    const clock = t.mock.method(Date, "now", () => createdAt + seconds * 1000);
    assert.equal(presentJob(job).created, expected);
    clock.mock.restore();
  }
  assert.equal(presentJob({ ...job, source_filename: null }).file, "Choose a .blend or ZIP");
  assert.equal(presentJob({ ...job, source_filename: "" }).file, "");
});
