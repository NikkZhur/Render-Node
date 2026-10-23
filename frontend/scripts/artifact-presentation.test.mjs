import assert from "node:assert/strict";
import test from "node:test";

import { frameSequencePresentation, paddedFrame } from "../src/artifactPresentation.js";

test("preserves negative and zero scene frames with sign-aware labels", () => {
  assert.deepEqual(
    frameSequencePresentation({ frame_mode: "ALL" }, { total: 3, items: [{ frame: -2 }, { frame: 0 }, { frame: 2 }] }),
    { countText: "3 frames", name: "frames_-002–0002", title: "Frames -2–2" },
  );
  assert.equal(frameSequencePresentation({ frame_mode: "ALL" }, { total: 1, items: [{ frame: 0 }] }).name, "frame_0000");
  assert.equal(paddedFrame(-2, 3), "-02");
  assert.equal(paddedFrame(0, 3), "000");
});

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
