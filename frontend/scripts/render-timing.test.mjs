import assert from "node:assert/strict";
import test from "node:test";

import {
  EtaSmoother,
  formatDuration,
  rawEtaSeconds,
  renderDurationSeconds,
  renderTimingPresentation,
} from "../src/renderTiming.js";

const startedAt = "2026-08-18T12:00:00.000Z";

test("formats compact render durations", () => {
  assert.equal(formatDuration(12.9), "12s");
  assert.equal(formatDuration(222), "3m 42s");
  assert.equal(formatDuration(4080), "1h 08m");
});

test("computes running and fixed terminal duration from UTC timestamps", () => {
  const running = { status: "rendering", started_at: startedAt };
  assert.equal(renderDurationSeconds(running, Date.parse("2026-08-18T12:03:42Z")), 222);

  const terminal = {
    status: "completed",
    started_at: startedAt,
    finished_at: "2026-08-18T12:00:12.750Z",
  };
  assert.equal(renderDurationSeconds(terminal, Date.parse("2026-08-19T00:00:00Z")), 12.75);
});

test("rejects missing, incomplete and negative timestamp differences", () => {
  assert.equal(renderDurationSeconds({ status: "ready" }, Date.now()), null);
  assert.equal(renderDurationSeconds({ status: "failed", started_at: startedAt }), null);
  assert.equal(renderDurationSeconds({
    status: "cancelled",
    started_at: "2026-08-18T12:01:00Z",
    finished_at: startedAt,
  }), null);
  assert.equal(formatDuration(Number.NaN), "—");
});

test("guards raw ETA against unstable and non-finite inputs", () => {
  assert.equal(rawEtaSeconds(4.99, 0.5), null);
  assert.equal(rawEtaSeconds(10, 0), null);
  assert.equal(rawEtaSeconds(10, 0.019), null);
  assert.equal(rawEtaSeconds(10, Number.NaN), null);
  assert.equal(rawEtaSeconds(Number.POSITIVE_INFINITY, 0.5), null);
  assert.equal(rawEtaSeconds(10, 0.5), 10);
});

test("ETA uses whole-job progress after the first stepped frame completes", () => {
  for (const device of ["CPU", "CUDA", "OptiX"]) {
    for (const frameMode of ["ALL", "RANGE"]) {
      const timing = renderTimingPresentation({
        id: "stepped", status: "rendering", started_at: startedAt,
        device, frame_mode: frameMode, gpu_render_mode: "ALL_GPUS_PER_FRAME",
        progress: 100 / 3, current_frame: 1,
      }, Date.parse(startedAt) + 10_000, new EtaSmoother());
      assert(Math.abs(timing.remainingSeconds - 20) < 1e-9);
      assert.equal(timing.remaining, "~20s remaining");
    }
  }
});

test("EWMA is deterministic and ignores a brief progress regression", () => {
  const smoother = new EtaSmoother(0.2);
  const first = smoother.update({
    elapsedSeconds: 10,
    key: "job:start",
    progress: 0.25,
    status: "rendering",
  });
  const regressed = smoother.update({
    elapsedSeconds: 12,
    key: "job:start",
    progress: 0.2,
    status: "rendering",
  });
  assert.equal(first, 30);
  assert.equal(regressed, 31.2);
  assert.equal(smoother.monotonicProgress, 0.25);
});

test("presentation covers estimating, ETA and terminal states", () => {
  const smoother = new EtaSmoother();
  const job = { id: "job", status: "rendering", started_at: startedAt, progress: 1 };
  assert.deepEqual(
    renderTimingPresentation(job, Date.parse("2026-08-18T12:00:04Z"), smoother),
    { elapsed: "4s", remaining: "Estimating…", remainingSeconds: null },
  );

  const estimated = renderTimingPresentation(
    { ...job, progress: 20 },
    Date.parse("2026-08-18T12:00:10Z"),
    smoother,
  );
  assert.equal(estimated.elapsed, "10s");
  assert.equal(estimated.remaining, "~40s remaining");

  assert.deepEqual(renderTimingPresentation({
    ...job,
    status: "completed",
    progress: 100,
    finished_at: "2026-08-18T12:00:12Z",
  }, Date.parse("2026-08-18T13:00:00Z"), smoother), {
    elapsed: "12s",
    remaining: "Completed",
    remainingSeconds: null,
  });
  assert.equal(smoother.monotonicProgress, 0);
  assert.equal(renderTimingPresentation({ ...job, status: "cancelled" }, Date.now(), smoother).remaining, "Cancelled");
  assert.equal(renderTimingPresentation({ ...job, status: "failed" }, Date.now(), smoother).remaining, "Failed");
});
