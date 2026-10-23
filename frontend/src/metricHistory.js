export const METRIC_HISTORY_LIMIT = 60;

export const resourceKey = (kind, resource) => `${kind}:${
  kind === "gpu" ? (resource.uuid || resource.id)
    : kind === "storage" ? (resource.mountPoint || resource.id) : resource.id
}`;

export const metricPercent = (value, total) => (
  Number.isFinite(value) && Number.isFinite(total) && total > 0 ? value / total * 100 : null
);

const finite = (value) => Number.isFinite(value) ? value : null;

// This is also the metrics query's structuralSharing function: both REST results
// and WebSocket cache writes pass through it, including identical measurements.
export function appendMetricSample(previous, sample) {
  const timestamp = Date.parse(sample.timestamp);
  if (!Number.isFinite(timestamp)) return previous ?? { ...sample, history: [] };
  const history = previous?.history ?? [];
  if (history.length && timestamp <= history.at(-1).timestamp) return previous;
  const values = {};
  for (const [kind, resources] of [["gpu", sample.devices], ["cpu", sample.processors]]) {
    for (const resource of resources ?? []) {
      values[resourceKey(kind, resource)] = {
        load: finite(resource.utilization),
        memory: metricPercent(resource.memoryUsed, resource.memoryTotal),
        temperature: finite(resource.temperature),
      };
    }
  }
  for (const storage of sample.storages ?? []) {
    values[resourceKey("storage", storage)] = {
      used: Number.isFinite(storage.freeGb)
        ? metricPercent(storage.totalGb - storage.freeGb, storage.totalGb) : null,
      // Keep physical units: a per-sample denominator would flatten fast disks
      // at 100% and even let changing write speed distort a constant read speed.
      read: finite(storage.readMbps),
      write: finite(storage.writeMbps),
    };
  }
  return { ...sample, history: [...history, { timestamp, values }].slice(-METRIC_HISTORY_LIMIT) };
}

export const metricSeries = (history, kind, resource, metric) => history.map((sample) => ({
  timestamp: sample.timestamp,
  value: sample.values[resourceKey(kind, resource)]?.[metric] ?? null,
}));

// Missing measurements split paths. A single real observation is shown as a dot.
export function metricChartSegments(series, width = 240, height = 88, maximum = 100) {
  if (!series.length) return [];
  const start = series[0].timestamp;
  const duration = series.at(-1).timestamp - start;
  const segments = [];
  let segment = null;
  for (const sample of series) {
    if (!Number.isFinite(sample.value)) {
      segment = null;
      continue;
    }
    if (!segment) {
      segment = [];
      segments.push(segment);
    }
    segment.push({
      x: duration > 0 ? ((sample.timestamp - start) / duration) * width : width,
      y: 8 + (1 - Math.max(0, Math.min(maximum, sample.value)) / maximum) * (height - 18),
    });
  }
  return segments;
}
