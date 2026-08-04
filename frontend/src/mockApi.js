const wait = (milliseconds) =>
  new Promise((resolve) => window.setTimeout(resolve, milliseconds));

const BLENDER_ARCHIVE_URL = "https://download.blender.org/release/";

let versions = [
  {
    version: "5.2.0",
    channel: "Stable",
    source: "bundled",
    installed: true,
    supported: true,
    active: true,
    size: "367 MB",
  },
  {
    version: "4.1.1",
    channel: "Stable",
    source: "bundled",
    installed: true,
    supported: true,
    active: false,
    size: "284 MB",
  },
];

let officialVersions = [];
let manualVersions = [];
const downloadedVersions = new Set();

const compareVersions = (left, right) => {
  const leftParts = left.split(".").map(Number);
  const rightParts = right.split(".").map(Number);
  const length = Math.max(leftParts.length, rightParts.length);

  for (let index = 0; index < length; index += 1) {
    const difference = (rightParts[index] ?? 0) - (leftParts[index] ?? 0);
    if (difference !== 0) return difference;
  }

  return 0;
};

const parseOfficialVersions = (html) => {
  const documentNode = new DOMParser().parseFromString(html, "text/html");
  const branches = [...documentNode.querySelectorAll("a")]
    .map((link) => link.getAttribute("href") ?? "")
    .map((href) => href.match(/^Blender(\d+\.\d+)\/$/)?.[1])
    .filter(Boolean);

  return [...new Set(branches)]
    .filter((branch) => !versions.some((version) =>
      version.version === branch || version.version.startsWith(`${branch}.`)))
    .sort(compareVersions)
    .map((version) => ({
      version,
      channel: "Archive",
      source: "official",
      installed: false,
      downloaded: downloadedVersions.has(version),
      supported: false,
      active: false,
      archiveUrl: `${BLENDER_ARCHIVE_URL}Blender${version}/`,
    }));
};

const formatFileSize = (bytes) => {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  return `${Math.max(1, Math.round(bytes / 1024 ** 2))} MB`;
};

const devices = [
  {
    id: 0,
    name: "NVIDIA RTX 4090",
    utilization: 72,
    memoryUsed: 14.8,
    memoryTotal: 24,
    temperature: 64,
    available: true,
  },
  {
    id: 1,
    name: "NVIDIA RTX 4090",
    utilization: 4,
    memoryUsed: 1.2,
    memoryTotal: 24,
    temperature: 38,
    available: true,
  },
];

const processors = [
  {
    id: 0,
    name: "AMD EPYC 9354P",
    utilization: 18,
    cores: 32,
    memoryUsed: 21.4,
    memoryTotal: 64,
    temperature: 52,
  },
];

const storages = [
  {
    id: 0,
    name: "Workspace NVMe",
    mountPoint: "/workspace",
    totalGb: 1000,
    freeGb: 412,
    readMbps: 128,
    writeMbps: 86,
    maxThroughputMbps: 1000,
    status: "healthy",
  },
];

const frameCount = 240;

const initialJobs = [
  {
    id: "job-live",
    shortId: "8F2A",
    name: "Atrium lighting",
    file: "atrium_final.blend",
    status: "ready",
    progress: 0,
    frame: "1–240",
    engine: "Cycles",
    device: "OptiX",
    version: "4.5.11",
    created: "Just now",
  },
  {
    id: "job-queued",
    shortId: "B17C",
    name: "Product turntable",
    file: "headphones.blend",
    status: "ready",
    progress: 0,
    frame: "1–72",
    engine: "Cycles",
    device: "CUDA",
    version: "4.5.11",
    created: "8 min ago",
  },
  {
    id: "job-complete",
    shortId: "41DE",
    name: "Loft still",
    file: "loft_camera_03.blend",
    status: "completed",
    progress: 100,
    frame: "Frame 48",
    engine: "Cycles",
    device: "OptiX",
    version: "4.2.22",
    created: "Yesterday",
  },
  {
    id: "job-failed",
    shortId: "90AA",
    name: "Forest study",
    file: "forest_v12.blend",
    status: "failed",
    progress: 36,
    frame: "1–240",
    engine: "Cycles",
    device: "OptiX",
    version: "4.1.1",
    created: "2 days ago",
  },
];

const logLines = [
  ["09:41:12", "Scene loaded in 2.8s"],
  ["09:41:13", "Cycles: using NVIDIA RTX 4090 (OptiX)"],
  ["09:41:14", "Synchronizing object | Atrium_Glass_04"],
  ["09:41:16", "Loading render kernels (may take a few minutes)"],
  ["09:41:18", "Fra: 001 | Mem: 4.21G | Time: 00:00.82"],
  ["09:41:20", "Path Tracing Sample 48 / 512"],
];

export const mockApi = {
  frameCount,
  initialJobs,
  logLines,

  async getVersions() {
    await wait(140);
    return versions.map((version) => ({ ...version }));
  },

  async getOfficialVersions({ signal } = {}) {
    const response = await fetch(BLENDER_ARCHIVE_URL, { signal });
    if (!response.ok) {
      throw new Error(`Official archive returned ${response.status}`);
    }

    const archiveVersions = parseOfficialVersions(await response.text());
    officialVersions = [...manualVersions, ...archiveVersions]
      .filter((version, index, collection) =>
        collection.findIndex((candidate) => candidate.version === version.version) === index,
      )
      .sort((left, right) => compareVersions(left.version, right.version));
    return officialVersions.map((version) => ({ ...version }));
  },

  async uploadVersion(file) {
    const match = file.name.match(/^blender-(\d+\.\d+\.\d+)-linux-(?:x64|amd64)\.tar\.(?:xz|bz2)$/i);
    if (!match) {
      throw new Error("Choose a Blender Linux x64 .tar.xz or .tar.bz2 archive");
    }
    if (file.size <= 0 || file.size > 2 * 1024 ** 3) {
      throw new Error("Installer archive must be between 1 byte and 2 GB");
    }

    const versionNumber = match[1];
    if (versions.some((version) => version.version === versionNumber)) {
      throw new Error(`Blender ${versionNumber} is already installed`);
    }

    await wait(350);
    const uploadedVersion = {
      version: versionNumber,
      channel: "Manual",
      source: "manual",
      installed: false,
      downloaded: true,
      supported: false,
      active: false,
      size: formatFileSize(file.size),
      fileName: file.name,
    };
    downloadedVersions.add(versionNumber);
    manualVersions = [
      uploadedVersion,
      ...manualVersions.filter((version) => version.version !== versionNumber),
    ];
    officialVersions = [
      uploadedVersion,
      ...officialVersions.filter((version) => version.version !== versionNumber),
    ].sort((left, right) => compareVersions(left.version, right.version));
    return { ...uploadedVersion };
  },

  async downloadVersion(versionNumber) {
    const version = officialVersions.find((candidate) => candidate.version === versionNumber);
    if (!version) throw new Error("Version is not present in the official archive");

    await wait(700);
    downloadedVersions.add(versionNumber);
    officialVersions = officialVersions.map((candidate) =>
      candidate.version === versionNumber ? { ...candidate, downloaded: true } : candidate,
    );
    return { version: versionNumber };
  },

  async installVersion(versionNumber) {
    if (!downloadedVersions.has(versionNumber)) {
      throw new Error("Download the version before installation");
    }

    const version = officialVersions.find((candidate) => candidate.version === versionNumber);
    if (!version) throw new Error("Downloaded version metadata is unavailable");

    await wait(900);
    versions = [
      ...versions,
      {
        ...version,
        source: version.source === "manual" ? "manual" : "downloaded",
        installed: true,
        downloaded: true,
      },
    ].sort((left, right) => compareVersions(left.version, right.version));
    officialVersions = officialVersions.filter((candidate) => candidate.version !== versionNumber);
    manualVersions = manualVersions.filter((candidate) => candidate.version !== versionNumber);
    return { version: versionNumber };
  },

  async activateVersion(versionNumber) {
    await wait(450);
    versions = versions.map((version) => ({
      ...version,
      active: version.version === versionNumber,
    }));
    return { version: versionNumber };
  },

  async deleteVersion(versionNumber) {
    const version = versions.find((candidate) => candidate.version === versionNumber)
      ?? officialVersions.find((candidate) => candidate.version === versionNumber);
    if (!version) throw new Error("Blender version is not installed or downloaded");
    if (version.source === "bundled") throw new Error("Bundled versions cannot be deleted");
    if (version.active) throw new Error("The active Blender version cannot be deleted");

    await wait(300);
    versions = versions.filter((candidate) => candidate.version !== versionNumber);
    officialVersions = officialVersions.filter(
      (candidate) => candidate.version !== versionNumber,
    );
    manualVersions = manualVersions.filter((candidate) => candidate.version !== versionNumber);
    downloadedVersions.delete(versionNumber);
    return { version: versionNumber };
  },

  async getDevices() {
    await wait(120);
    return devices.map((device) => ({ ...device }));
  },

  async getProcessors() {
    await wait(120);
    return processors.map((processor) => ({ ...processor }));
  },

  async getStorages() {
    await wait(120);
    return storages.map((storage) => ({ ...storage }));
  },

  async getFrames({ page, pageSize }) {
    await wait(90);
    const start = (page - 1) * pageSize;
    const count = Math.max(0, Math.min(pageSize, frameCount - start));
    return {
      items: Array.from({ length: count }, (_, index) => {
        const frame = start + index + 1;
        return {
          frame,
          name: `frame_${String(frame).padStart(4, "0")}.png`,
        };
      }),
      page,
      pageSize,
      total: frameCount,
    };
  },
};
