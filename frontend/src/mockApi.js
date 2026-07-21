const wait = (milliseconds) =>
  new Promise((resolve) => window.setTimeout(resolve, milliseconds));

let versions = [
  {
    version: "5.2.0",
    channel: "Stable",
    source: "bundled",
    installed: true,
    supported: true,
    active: false,
    size: "367 MB",
  },
  {
    version: "4.5.11",
    channel: "LTS",
    source: "bundled",
    installed: true,
    supported: true,
    active: true,
    size: "360 MB",
  },
  {
    version: "4.2.22",
    channel: "LTS",
    source: "bundled",
    installed: true,
    supported: true,
    active: false,
    size: "335 MB",
  },
  {
    version: "4.1.1",
    channel: "Legacy",
    source: "bundled",
    installed: true,
    supported: true,
    active: false,
    size: "284 MB",
  },
  {
    version: "3.6.23",
    channel: "LTS",
    source: "bundled",
    installed: true,
    supported: true,
    active: false,
    size: "260 MB",
  },
  {
    version: "4.4.3",
    channel: "Archive",
    source: "official",
    installed: false,
    supported: false,
    active: false,
    size: "340 MB",
  },
];

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

const frameCount = 240;

export const mockApi = {
  async getVersions() {
    await wait(140);
    return versions.map((version) => ({ ...version }));
  },

  async installVersion(versionNumber) {
    await wait(1_100);
    versions = versions.map((version) =>
      version.version === versionNumber
        ? { ...version, installed: true, source: "downloaded" }
        : version,
    );
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

  async getDevices() {
    await wait(120);
    return devices.map((device) => ({ ...device }));
  },

  async getProcessors() {
    await wait(120);
    return processors.map((processor) => ({ ...processor }));
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
