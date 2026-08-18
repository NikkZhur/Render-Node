export const FRAME_PER_GPU = "FRAME_PER_GPU";
export const ALL_GPUS_PER_FRAME = "ALL_GPUS_PER_FRAME";

export const supportsFramePerGpu = (device, gpuIds) => (
  (device === "CUDA" || device === "OptiX" || device === "OPTIX")
  && gpuIds.length >= 2
);

export const normalizeGpuRenderMode = (device, gpuIds, preferredMode) => (
  supportsFramePerGpu(device, gpuIds) ? preferredMode : ALL_GPUS_PER_FRAME
);
