const isInteger = (value) => Number.isInteger(value);

export function mergeRenderProgress(previous, event) {
  const frame = isInteger(event.frame) ? event.frame : (previous?.frame ?? null);
  const changedFrame = previous !== undefined && frame !== previous.frame;
  const hasSamples = isInteger(event.sample)
    && isInteger(event.total_samples)
    && event.sample >= 0
    && event.total_samples > 0;
  const progress = typeof event.progress === "number"
    ? Math.round(Math.min(Math.max(event.progress, 0), 1) * 100)
    : (previous?.progress ?? null);

  return {
    frame,
    progress,
    sample: hasSamples ? event.sample : (changedFrame ? null : (previous?.sample ?? null)),
    totalSamples: hasSamples
      ? event.total_samples
      : (changedFrame ? null : (previous?.totalSamples ?? null)),
  };
}
