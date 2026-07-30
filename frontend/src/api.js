const API_PREFIX = import.meta.env.VITE_RENDER_NODE_API_URL ?? "/api/v1";

export const isMockMode = import.meta.env.VITE_RENDER_NODE_MOCK === "true";

const engineLabels = {
  CYCLES: "Cycles",
  BLENDER_EEVEE: "Eevee",
  BLENDER_WORKBENCH: "Workbench",
};

const deviceLabels = {
  CPU: "CPU",
  CUDA: "CUDA",
  OPTIX: "OptiX",
};

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
  engine: engineLabels[job.engine] ?? job.engine,
  device: deviceLabels[job.device] ?? job.device,
  version: job.blender_version,
  created: formatCreatedAt(job.created_at),
});

const request = async (path, options = {}) => {
  const response = await fetch(`${API_PREFIX}${path}`, options);
  if (response.status === 204) return null;

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(payload?.error?.message ?? `Render Node API returned ${response.status}`);
  }
  return payload;
};

const apiUrl = (path) => `${API_PREFIX}${path}`;

export const jobsApi = {
  async list({ signal } = {}) {
    const jobs = await request("/jobs", { signal });
    return jobs.map(presentJob);
  },

  async create(payload) {
    return request("/jobs", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  async upload(jobId, file) {
    const form = new FormData();
    form.append("file", file);
    return presentJob(await request(`/jobs/${jobId}/uploads`, { method: "POST", body: form }));
  },

  async start(jobId) {
    return presentJob(await request(`/jobs/${jobId}/start`, { method: "POST" }));
  },

  async cancel(jobId) {
    return presentJob(await request(`/jobs/${jobId}/cancel`, { method: "POST" }));
  },

  async retry(jobId) {
    return presentJob(await request(`/jobs/${jobId}/retry`, { method: "POST" }));
  },

  async delete(jobId) {
    return request(`/jobs/${jobId}`, { method: "DELETE" });
  },
};

export const devicesApi = {
  async list({ signal } = {}) {
    const devices = await request("/devices", { signal });
    return devices.map((device) => ({
      ...device,
      memoryTotal: Number((device.memory_total_bytes / 1024 ** 3).toFixed(1)),
    }));
  },
};

const bytesToGb = (bytes) => Number((bytes / 1024 ** 3).toFixed(1));

export const artifactsApi = {
  list(jobId, { signal } = {}) {
    return request(`/jobs/${jobId}/artifacts`, { signal });
  },

  frames(jobId, { page = 1, pageSize = 50, signal } = {}) {
    return request(`/jobs/${jobId}/frames?page=${page}&page_size=${pageSize}`, { signal });
  },

  logTail(jobId, { lines = 100, signal } = {}) {
    return request(`/jobs/${jobId}/logs/blender/tail?lines=${lines}`, { signal });
  },

  previewUrl(jobId, frame) {
    return apiUrl(`/jobs/${jobId}/frames/${frame}/preview`);
  },

  originalUrl(jobId, frame) {
    return apiUrl(`/jobs/${jobId}/frames/${frame}/original`);
  },

  framesZipUrl(jobId) {
    return apiUrl(`/jobs/${jobId}/frames.zip`);
  },

  logUrl(jobId) {
    return apiUrl(`/jobs/${jobId}/logs/blender`);
  },
};

export const presentMetrics = (metrics) => ({
      ...metrics,
      processors: metrics.cpus.map((cpu) => ({
        id: cpu.id,
        name: cpu.name,
        utilization: Math.round(cpu.utilization_percent),
        cores: cpu.cores,
        memoryUsed: bytesToGb(cpu.memory_used_bytes),
        memoryTotal: bytesToGb(cpu.memory_total_bytes),
        temperature: Math.round(cpu.temperature_celsius ?? 0),
      })),
      devices: metrics.gpus.map((gpu) => ({
        id: gpu.id,
        name: gpu.name,
        utilization: Math.round(gpu.utilization_percent),
        memoryUsed: bytesToGb(gpu.memory_used_bytes),
        memoryTotal: bytesToGb(gpu.memory_total_bytes),
        temperature: Math.round(gpu.temperature_celsius ?? 0),
        available: true,
      })),
      storages: metrics.storages.map((storage) => ({
        id: storage.id,
        name: storage.name,
        mountPoint: storage.mount_point,
        totalGb: bytesToGb(storage.total_bytes),
        freeGb: bytesToGb(storage.free_bytes),
        readMbps: Number(storage.read_mbps.toFixed(1)),
        writeMbps: Number(storage.write_mbps.toFixed(1)),
        maxThroughputMbps: Math.max(1, storage.read_mbps, storage.write_mbps, 1000),
        status: storage.status,
      })),
});

export const systemApi = {
  async metrics({ signal } = {}) {
    return presentMetrics(await request("/system/metrics", { signal }));
  },
};

export const eventsApi = {
  url() {
    const url = new URL(apiUrl("/events"), window.location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    return url.toString();
  },
};

const channelForVersion = (version) => {
  if (["4.5.11", "4.2.22", "3.6.23"].includes(version)) return "LTS";
  return "Stable";
};

const presentRuntime = (runtime) => ({
  ...runtime,
  channel: channelForVersion(runtime.version),
  installed: runtime.state === "installed",
  downloaded: ["downloaded", "installing", "installed"].includes(runtime.state),
  fileName: runtime.archive_filename,
});

const presentRelease = (release) => ({
  ...release,
  channel: channelForVersion(release.version),
  source: release.source ?? "official",
  fileName: release.filename,
});

const waitForOperation = async (operationId) => {
  while (true) {
    const operation = await request(`/blender/operations/${operationId}`);
    if (operation.state === "completed") return operation;
    if (operation.state === "failed") {
      throw new Error(operation.error || "Blender operation failed");
    }
    await new Promise((resolve) => window.setTimeout(resolve, 400));
  }
};

export const blenderApi = {
  async getVersions({ signal } = {}) {
    const runtimes = await request("/blender/versions", { signal });
    return runtimes.map(presentRuntime);
  },

  async getOfficialVersions({ signal } = {}) {
    const releases = await request("/blender/releases", { signal });
    return releases.filter((release) => !release.installed).map(presentRelease);
  },

  async downloadVersion(version) {
    const accepted = await request(`/blender/releases/${version}/download`, { method: "POST" });
    await waitForOperation(accepted.operation_id);
    return { version };
  },

  async uploadVersion(file) {
    const form = new FormData();
    form.append("file", file);
    const accepted = await request("/blender/versions/upload", { method: "POST", body: form });
    const operation = await waitForOperation(accepted.operation_id);
    const releases = await blenderApi.getOfficialVersions();
    return releases.find((release) => release.version === operation.version) ?? {
      version: operation.version,
      channel: "Stable",
      source: "manual",
      downloaded: true,
      installed: false,
      supported: false,
      active: false,
      fileName: file.name,
    };
  },

  async installVersion(version) {
    const accepted = await request(`/blender/versions/${version}/install`, { method: "POST" });
    await waitForOperation(accepted.operation_id);
    return { version };
  },

  async activateVersion(version) {
    return presentRuntime(
      await request(`/blender/versions/${version}/activate`, { method: "POST" }),
    );
  },
};
