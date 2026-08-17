import assert from "node:assert/strict";
import test from "node:test";

import { mergeRenderProgress } from "../src/renderProgress.js";

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
    },
  );
});

test("keeps samples through same-frame updates without a sample payload", () => {
  const previous = { frame: 8, progress: 39, sample: 200, totalSamples: 512 };

  assert.deepEqual(
    mergeRenderProgress(previous, { frame: 8, progress: 0.5 }),
    { frame: 8, progress: 50, sample: 200, totalSamples: 512 },
  );
});

test("clears stale samples when Blender advances to another frame", () => {
  const previous = { frame: 8, progress: 50, sample: 256, totalSamples: 512 };

  assert.deepEqual(
    mergeRenderProgress(previous, { frame: 9, progress: 0.51 }),
    { frame: 9, progress: 51, sample: null, totalSamples: null },
  );
});

test("clamps out-of-range overall progress", () => {
  assert.equal(mergeRenderProgress(undefined, { progress: 1.5 }).progress, 100);
  assert.equal(mergeRenderProgress(undefined, { progress: -0.5 }).progress, 0);
});
