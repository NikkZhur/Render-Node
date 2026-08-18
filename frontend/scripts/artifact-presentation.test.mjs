import assert from "node:assert/strict";
import test from "node:test";

import { frameSequencePresentation } from "../src/artifactPresentation.js";

test("uses the registered frame number for a partial one-frame result", () => {
  assert.deepEqual(
    frameSequencePresentation(
      { frame_mode: "RANGE", frame_start: 1, frame_end: 2 },
      { total: 1, items: [{ frame: 2 }] },
    ),
    {
      countText: "1 frame",
      name: "frame_0002",
      title: "Frame 2",
    },
  );
});

test("uses configured boundaries only for a complete contiguous range", () => {
  assert.deepEqual(
    frameSequencePresentation(
      { frame_mode: "RANGE", frame_start: 48, frame_end: 72 },
      { total: 25, items: [{ frame: 48 }] },
    ),
    {
      countText: "25 frames",
      name: "frames_0048–0072",
      title: "Frames 48–72",
    },
  );
});

test("does not invent boundaries for a sparse or unknown sequence", () => {
  assert.deepEqual(
    frameSequencePresentation(
      { frame_mode: "ALL", frame_start: null, frame_end: null },
      { total: 4, items: [{ frame: 2 }] },
    ),
    {
      countText: "4 frames",
      name: "4 rendered frames",
      title: "4 rendered frames",
    },
  );
});
