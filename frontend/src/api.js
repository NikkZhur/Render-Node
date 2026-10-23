import { presentJob } from "./jobPresentation";
import { presentMetrics } from "./metricPresentation";
import { withBrowserRequestPolicy } from "./requestPolicy";

export { presentJob } from "./jobPresentation";
export { presentMetrics } from "./metricPresentation";

const API_PREFIX = import.meta.env.VITE_RENDER_NODE_API_URL ?? "/api/v1";

export class ApiError extends Error {
  constructor(message, { code, details, status }) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.details = details;
    this.status = status;
  }
}

let recoveryPromise = null;

const request = async (path, options = {}, allowRecovery = true) => {
  const response = await fetch(
    `${API_PREFIX}${path}`,
    withBrowserRequestPolicy(options),
  );
  if (response.status === 204) return null;

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new ApiError(
      payload?.error?.message ?? `Render Node API returned ${response.status}`,
      {
        code: payload?.error?.code ?? "api_request_failed",
        details: payload?.error?.details ?? null,
        status: response.status,
      },
    );
    if (
      allowRecovery
      && error.code === "browser_session_required"
      && !path.startsWith("/access/")
    ) {
      recoveryPromise ??= accessApi.claim().finally(() => {
        recoveryPromise = null;
      });
      await recoveryPromise;
      return request(path, options, false);
    }
    throw error;
  }
  return payload;
};

const apiUrl = (path) => `${API_PREFIX}${path}`;

export const accessApi = {
  claim() {
    return request("/access/claim", { method: "POST" }, false);
  },

  pair(token) {
    return request(
      "/access/pair",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token }),
      },
      false,
    );
  },

  createInvitation() {
    return request("/access/invitations", { method: "POST" });
  },
};

export const jobsApi = {
  summary({ signal } = {}) {
    return request("/jobs/summary", { signal });
  },

  async list({ signal } = {}) {
    const jobs = await request("/jobs", { signal });
    return jobs.map(presentJob);
  },

  async page({ page = 1, pageSize = 10, signal } = {}) {
    const result = await request(`/jobs/page?page=${page}&page_size=${pageSize}`, { signal });
    return {
      ...result,
      items: result.items.map(presentJob),
    };
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
    form.append("auto_configure", "true");
    return presentJob(await request(`/jobs/${jobId}/uploads`, { method: "POST", body: form }));
  },

  async update(jobId, payload) {
    const configuration = { ...payload };
    delete configuration.blender_version;
    return presentJob(await request(`/jobs/${jobId}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(configuration),
    }));
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

  async rerender(jobId) {
    return presentJob(await request(`/jobs/${jobId}/rerender`, { method: "POST" }));
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

export const artifactsApi = {
  list(jobId, { signal, kind } = {}) {
    const query = kind ? `?kind=${encodeURIComponent(kind)}` : "";
    return request(`/jobs/${jobId}/artifacts${query}`, { signal });
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

export const systemApi = {
  async metrics({ signal } = {}) {
    return presentMetrics(await request("/system/metrics", { signal }));
  },

  capabilities({ signal } = {}) {
    return request("/system/capabilities", { signal });
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

  async deleteVersion(version) {
    await request(`/blender/versions/${version}`, { method: "DELETE" });
    return { version };
  },
};
