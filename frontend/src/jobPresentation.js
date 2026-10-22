export const ENGINE_API_VALUES = {
  Cycles: "CYCLES",
  Eevee: "BLENDER_EEVEE",
  Workbench: "BLENDER_WORKBENCH",
};
const ENGINE_DISPLAY_VALUES = Object.fromEntries(
  Object.entries(ENGINE_API_VALUES).map(([label, value]) => [value, label]),
);
const DEVICE_DISPLAY_VALUES = { CPU: "CPU", CUDA: "CUDA", OPTIX: "OptiX" };

const formatFrameSelection = (job) => {
  if (job.frame_mode === "SINGLE") return `Frame ${job.frame_start}`;
  if (job.frame_mode === "RANGE") return `${job.frame_start}–${job.frame_end}`;
  return "All frames";
};

const formatCreatedAt = (createdAt) => {
  const elapsedSeconds = Math.max(0, (Date.now() - new Date(createdAt).getTime()) / 1000);
  if (elapsedSeconds < 60) return "Just now";
  if (elapsedSeconds < 3600) return `${Math.floor(elapsedSeconds / 60)} min ago`;
  if (elapsedSeconds < 86400) return `${Math.floor(elapsedSeconds / 3600)} hr ago`;
  return `${Math.floor(elapsedSeconds / 86400)} days ago`;
};

export const presentJob = (job) => ({
  ...job,
  shortId: job.id.slice(0, 4).toUpperCase(),
  file: job.source_filename ?? "Choose a .blend or ZIP",
  progress: Math.round(job.progress * 100),
  frame: formatFrameSelection(job),
  engine: ENGINE_DISPLAY_VALUES[job.engine] ?? job.engine,
  device: DEVICE_DISPLAY_VALUES[job.device] ?? job.device,
  version: job.blender_version,
  created: formatCreatedAt(job.created_at),
});

export function applyJobConfiguration(job, configuration) {
  return {
    ...job,
    name: configuration.name,
    engine: ENGINE_DISPLAY_VALUES[configuration.engine] ?? configuration.engine,
    device: DEVICE_DISPLAY_VALUES[configuration.device] ?? configuration.device,
    gpu_ids: configuration.gpu_ids,
    gpu_render_mode: configuration.gpu_render_mode,
    frame: formatFrameSelection(configuration),
    frame_mode: configuration.frame_mode,
    frame_start: configuration.frame_start,
    frame_end: configuration.frame_end,
  };
}
