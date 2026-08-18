const validFrame = (value) => Number.isInteger(value) && value >= 1;

const paddedFrame = (frame) => String(frame).padStart(4, "0");

export const frameSequencePresentation = (job, framePage, fallbackTotal = 0) => {
  const total = framePage?.total ?? fallbackTotal;
  const observedFrames = (framePage?.items ?? [])
    .map((item) => item.frame)
    .filter(validFrame);
  let firstFrame = null;
  let lastFrame = null;

  if (observedFrames.length === total && total > 0) {
    firstFrame = Math.min(...observedFrames);
    lastFrame = Math.max(...observedFrames);
  } else if (
    job?.frame_mode === "RANGE"
    && validFrame(job.frame_start)
    && validFrame(job.frame_end)
    && job.frame_end - job.frame_start + 1 === total
  ) {
    firstFrame = job.frame_start;
    lastFrame = job.frame_end;
  } else if (total === 1) {
    firstFrame = observedFrames[0]
      ?? (job?.frame_mode === "SINGLE" && validFrame(job.frame_start) ? job.frame_start : null);
    lastFrame = firstFrame;
  }

  const countText = `${total} ${total === 1 ? "frame" : "frames"}`;
  if (validFrame(firstFrame) && firstFrame === lastFrame) {
    return {
      countText,
      name: `frame_${paddedFrame(firstFrame)}`,
      title: `Frame ${firstFrame}`,
    };
  }
  if (validFrame(firstFrame) && validFrame(lastFrame)) {
    return {
      countText,
      name: `frames_${paddedFrame(firstFrame)}–${paddedFrame(lastFrame)}`,
      title: `Frames ${firstFrame}–${lastFrame}`,
    };
  }
  return {
    countText,
    name: `${total} rendered frames`,
    title: total > 0 ? `${total} rendered frames` : "Frames —",
  };
};
