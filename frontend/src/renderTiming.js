const TERMINAL_STATUSES = new Set(["completed", "cancelled", "failed"]);

const timestampMilliseconds = (value) => {
  if (typeof value !== "string" && !(value instanceof Date)) return null;
  const milliseconds = new Date(value).getTime();
  return Number.isFinite(milliseconds) ? milliseconds : null;
};

const safeSeconds = (milliseconds) => {
  const seconds = milliseconds / 1000;
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
};

export function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const wholeSeconds = Math.floor(seconds);
  if (wholeSeconds < 60) return `${wholeSeconds}s`;

  const minutes = Math.floor(wholeSeconds / 60);
  if (minutes < 60) {
    return `${minutes}m ${String(wholeSeconds % 60).padStart(2, "0")}s`;
  }

  const hours = Math.floor(minutes / 60);
  return `${hours}h ${String(minutes % 60).padStart(2, "0")}m`;
}

export function renderDurationSeconds(job, now = Date.now()) {
  const startedAt = timestampMilliseconds(job?.started_at);
  if (startedAt === null) return null;

  if (job.status === "rendering") {
    const nowMilliseconds = now instanceof Date ? now.getTime() : now;
    return Number.isFinite(nowMilliseconds) ? safeSeconds(nowMilliseconds - startedAt) : null;
  }

  if (!TERMINAL_STATUSES.has(job.status)) return null;
  const finishedAt = timestampMilliseconds(job.finished_at);
  return finishedAt === null ? null : safeSeconds(finishedAt - startedAt);
}

export function rawEtaSeconds(elapsedSeconds, progress) {
  if (
    !Number.isFinite(elapsedSeconds)
    || elapsedSeconds < 5
    || !Number.isFinite(progress)
    || progress < 0.02
    || progress <= 0
    || progress > 1
  ) {
    return null;
  }
  const remaining = elapsedSeconds * (1 - progress) / progress;
  return Number.isFinite(remaining) && remaining >= 0 ? remaining : null;
}

export function roundedEtaSeconds(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  if (seconds < 60) return Math.round(seconds / 5) * 5;
  if (seconds < 3600) return Math.round(seconds / 10) * 10;
  return Math.round(seconds / 60) * 60;
}

export class EtaSmoother {
  constructor(alpha = 0.2) {
    if (!Number.isFinite(alpha) || alpha <= 0 || alpha > 1) {
      throw new RangeError("ETA smoothing alpha must be in (0, 1]");
    }
    this.alpha = alpha;
    this.reset();
  }

  reset() {
    this.key = null;
    this.monotonicProgress = 0;
    this.smoothedRemaining = null;
  }

  update({ elapsedSeconds, key, progress, status }) {
    if (status !== "rendering") {
      this.reset();
      return null;
    }
    if (this.key !== key) {
      this.reset();
      this.key = key;
    }
    if (Number.isFinite(progress)) {
      this.monotonicProgress = Math.max(this.monotonicProgress, Math.min(Math.max(progress, 0), 1));
    }
    const rawRemaining = rawEtaSeconds(elapsedSeconds, this.monotonicProgress);
    if (rawRemaining === null) return null;

    this.smoothedRemaining = this.smoothedRemaining === null
      ? rawRemaining
      : (this.alpha * rawRemaining) + ((1 - this.alpha) * this.smoothedRemaining);
    return this.smoothedRemaining;
  }
}

export function renderTimingPresentation(job, now, smoother) {
  const durationSeconds = renderDurationSeconds(job, now);
  if (TERMINAL_STATUSES.has(job?.status)) {
    smoother.reset();
    const terminalLabels = {
      cancelled: "Cancelled",
      completed: "Completed",
      failed: "Failed",
    };
    return {
      elapsed: formatDuration(durationSeconds),
      remaining: terminalLabels[job.status],
      remainingSeconds: null,
    };
  }

  if (job?.status !== "rendering" || durationSeconds === null) {
    smoother.reset();
    return { elapsed: "—", remaining: "—", remainingSeconds: null };
  }

  const progress = Number.isFinite(job.progress) ? job.progress / 100 : Number.NaN;
  const remainingSeconds = smoother.update({
    elapsedSeconds: durationSeconds,
    key: `${job.id ?? "job"}:${job.started_at}`,
    progress,
    status: job.status,
  });
  const roundedRemaining = roundedEtaSeconds(remainingSeconds);
  return {
    elapsed: formatDuration(durationSeconds),
    remaining: roundedRemaining === null ? "Estimating…" : `~${formatDuration(roundedRemaining)} remaining`,
    remainingSeconds,
  };
}
