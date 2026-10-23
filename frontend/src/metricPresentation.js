const bytesToGb = (bytes) => Number((bytes / 1024 ** 3).toFixed(1));
const roundedMetric = (value) => Number.isFinite(value) ? Math.round(value) : null;

// REST and WebSocket samples share this presentation boundary. Preserve unknown
// measurements as null so the UI can distinguish them from a measured zero.
export const presentMetrics = (metrics) => ({
  ...metrics,
  processors: metrics.cpus.map((cpu) => ({
    id: cpu.id,
    name: cpu.name,
    utilization: roundedMetric(cpu.utilization_percent),
    cores: cpu.cores,
    memoryUsed: bytesToGb(cpu.memory_used_bytes),
    memoryTotal: bytesToGb(cpu.memory_total_bytes),
    temperature: roundedMetric(cpu.temperature_celsius),
  })),
  devices: metrics.gpus.map((gpu) => ({
    id: gpu.id,
    uuid: gpu.uuid,
    name: gpu.name,
    utilization: roundedMetric(gpu.utilization_percent),
    memoryUsed: bytesToGb(gpu.memory_used_bytes),
    memoryTotal: bytesToGb(gpu.memory_total_bytes),
    temperature: roundedMetric(gpu.temperature_celsius),
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
