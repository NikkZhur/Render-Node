import { ALL_GPUS_PER_FRAME, FRAME_PER_GPU } from "./gpuRenderMode.js";

const isInteger = (value) => Number.isInteger(value);

const percentage = (value, fallback = null) => typeof value === "number" && Number.isFinite(value)
  ? Math.round(Math.min(Math.max(value, 0), 1) * 100)
  : fallback;

const mergeProgressSnapshot = (previous, event, progressField) => {
  const frame = isInteger(event.frame) ? event.frame : (previous?.frame ?? null);
  const changedFrame = previous !== undefined && frame !== previous.frame;
  const hasSamples = isInteger(event.sample)
    && isInteger(event.total_samples)
    && event.sample >= 0
    && event.total_samples > 0;

  return {
    frame,
    progress: percentage(event[progressField], previous?.progress ?? null),
    sample: hasSamples ? event.sample : (changedFrame ? null : (previous?.sample ?? null)),
    totalSamples: hasSamples
      ? event.total_samples
      : (changedFrame ? null : (previous?.totalSamples ?? null)),
  };
};

export function mergeRenderProgress(previous, event) {
  const overall = mergeProgressSnapshot(previous, event, "progress");
  const workersByGpu = { ...(previous?.workersByGpu ?? {}) };
  if (
    typeof event.gpu_uuid === "string"
    && event.gpu_uuid.length > 0
    && typeof event.worker_progress === "number"
    && Number.isFinite(event.worker_progress)
  ) {
    workersByGpu[event.gpu_uuid] = mergeProgressSnapshot(
      workersByGpu[event.gpu_uuid],
      event,
      "worker_progress",
    );
  }
  return { ...overall, workersByGpu };
}

const selectedGpuDevices = (job, devices) => (job.gpu_ids ?? [])
  .map((gpuId) => devices.find((device) => device.id === gpuId))
  .filter(Boolean);

const actualWorkerCount = (job, selectedDevices) => {
  if (job.frame_mode === "SINGLE") return Math.min(1, selectedDevices.length);
  if (
    job.frame_mode === "RANGE"
    && Number.isInteger(job.frame_start)
    && Number.isInteger(job.frame_end)
  ) {
    return Math.min(selectedDevices.length, Math.max(0, job.frame_end - job.frame_start + 1));
  }
  return selectedDevices.length;
};

const rowState = (status, snapshot) => {
  if (status === "completed") return "Completed";
  if (status === "cancelled") return "Cancelled";
  if (status === "failed") return "Failed";
  if (!snapshot) return "Waiting for update";
  return snapshot.progress === 100 ? "Completed" : "Rendering";
};

const overallSnapshot = (job, snapshot) => ({
  frame: snapshot?.frame ?? job.current_frame ?? null,
  progress: Math.max(job.progress ?? 0, snapshot?.progress ?? 0),
  sample: snapshot?.sample ?? null,
  totalSamples: snapshot?.totalSamples ?? null,
});

export function buildProgressRows(job, devices, snapshot = null) {
  const selectedDevices = selectedGpuDevices(job, devices);
  const workerDevices = selectedDevices.slice(0, actualWorkerCount(job, selectedDevices));
  const overall = overallSnapshot(job, snapshot);
  const hasLaunched = typeof job.started_at === "string" && job.started_at.length > 0;
  const distributed = hasLaunched
    && job.gpu_render_mode === FRAME_PER_GPU
    && workerDevices.length >= 2;
  if (distributed) {
    return {
      kind: "distributed",
      overall: {
        ...overall,
        key: "overall",
        label: "Overall",
        state: rowState(job.status, overall),
        accessibleLabel: "Overall render progress",
      },
      workers: workerDevices.map((device, index) => {
        const workerSnapshot = snapshot?.workersByGpu?.[device.uuid] ?? null;
        return {
          frame: workerSnapshot?.frame ?? null,
          progress: job.status === "completed" ? 100 : (workerSnapshot?.progress ?? 0),
          sample: workerSnapshot?.sample ?? null,
          totalSamples: workerSnapshot?.totalSamples ?? null,
          key: device.uuid,
          gpuUuid: device.uuid,
          label: `GPU ${index + 1}`,
          deviceName: device.name,
          state: rowState(job.status, workerSnapshot),
          accessibleLabel: `GPU ${index + 1} (${device.uuid}) render progress`,
        };
      }),
    };
  }

  const jointGpuProcess = hasLaunched
    && job.gpu_render_mode === ALL_GPUS_PER_FRAME
    && job.device !== "CPU"
    && selectedDevices.length >= 2;
  if (jointGpuProcess) {
    const uuids = selectedDevices.map((device) => device.uuid);
    return {
      kind: "all-gpus",
      overall: null,
      workers: [{
        ...overall,
        key: "all-gpus",
        label: "All GPUs",
        deviceName: selectedDevices.map((device) => device.name).join(", "),
        gpuUuid: uuids.join(", "),
        state: rowState(job.status, overall),
        accessibleLabel: `All GPUs (${uuids.join(", ")}) render progress`,
      }],
    };
  }

  return {
    kind: "single",
    overall: {
      ...overall,
      key: "overall",
      label: "Overall",
      state: rowState(job.status, overall),
      accessibleLabel: "Render progress",
    },
    workers: [],
  };
}
