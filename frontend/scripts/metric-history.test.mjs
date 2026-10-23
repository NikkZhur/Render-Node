import assert from "node:assert/strict";
import test from "node:test";
import { QueryClient } from "@tanstack/react-query";
import {
  appendMetricSample, METRIC_HISTORY_LIMIT, metricChartSegments, metricPercent, metricSeries,
} from "../src/metricHistory.js";

const gpu = { id: 0, uuid: "MIG-a", name: "Same GPU", utilization: 25, memoryUsed: 8, memoryTotal: 24, temperature: null };
const sample = (index, devices = [gpu]) => ({
  timestamp: new Date(1_000_000 + index * 1000).toISOString(),
  devices,
  processors: [{ id: 0, utilization: 0, memoryUsed: 4, memoryTotal: 16, temperature: null }],
  storages: [{ id: 0, mountPoint: "/workspace", totalGb: 100, freeGb: 40, readMbps: 0, writeMbps: 50, maxThroughputMbps: 1000 }],
});
const values = (result, resource = gpu) => metricSeries(result.history, "gpu", resource, "load").map((point) => point.value);

test("starts with one real sample and retains repeated measurements with new timestamps", () => {
  const first = appendMetricSample(undefined, sample(0));
  assert.equal(first.history.length, 1);
  assert.deepEqual(values(first), [25]);
  const next = appendMetricSample(first, sample(1));
  assert.deepEqual(values(next), [25, 25]);
  assert.equal(first.history.length, 1, "does not mutate the old cache");
  assert.deepEqual(metricSeries(next.history, "cpu", { id: 0 }, "load").map((p) => p.value), [0, 0]);
  assert.deepEqual(metricSeries(next.history, "storage", { mountPoint: "/workspace" }, "used").map((p) => p.value), [60, 60]);
});

test("bounds history, evicts removed identities, and rejects duplicate/late samples", () => {
  let current;
  for (let i = 0; i < 100; i++) current = appendMetricSample(current, sample(i, i === 0 ? [gpu] : []));
  assert.equal(current.history.length, METRIC_HISTORY_LIMIT);
  assert.equal(current.history[0].timestamp, Date.parse(sample(40).timestamp));
  assert(current.history.every((point) => !("gpu:MIG-a" in point.values)));
  assert.equal(appendMetricSample(current, sample(99)), current);
  assert.equal(appendMetricSample(current, sample(98)), current);
  assert.equal(appendMetricSample(current, { timestamp: "invalid" }), current);
});

test("follows UUID across GPU enumeration changes and separates identical names", () => {
  const other = { ...gpu, id: 1, uuid: "MIG-b", utilization: 90 };
  let current = appendMetricSample(undefined, sample(0, [gpu, other]));
  current = appendMetricSample(current, sample(1, [{ ...other, id: 0 }, { ...gpu, id: 1, utilization: 50 }]));
  assert.deepEqual(values(current), [25, 50]);
  assert.deepEqual(values(current, other), [90, 90]);
  current = appendMetricSample(current, sample(2, [other]));
  current = appendMetricSample(current, sample(3, [{ ...gpu, utilization: 0 }]));
  assert.deepEqual(values(current), [25, 50, null, 0]);
  assert.deepEqual(values(current, { ...gpu, uuid: "MIG-new" }), [null, null, null, null]);
});

test("unknown telemetry and invalid denominators stay null instead of becoming zero", () => {
  assert.equal(metricPercent(null, 10), null);
  assert.equal(metricPercent(1, 0), null);
  assert.equal(metricPercent(0, 10), 0);
  const current = appendMetricSample(undefined, sample(0, [{ ...gpu, utilization: null, memoryTotal: 0 }]));
  assert.deepEqual(values(current), [null]);
  assert.deepEqual(current.history[0].values["gpu:MIG-a"], { load: null, memory: null, temperature: null });
});

test("charts split unknown gaps, keep single samples finite, and use measurement times", () => {
  assert.deepEqual(metricChartSegments([]), []);
  assert.deepEqual(metricChartSegments([{ timestamp: 0, value: null }]), []);
  const one = metricChartSegments([{ timestamp: 0, value: 0 }]);
  assert.deepEqual(one, [[{ x: 240, y: 78 }]]);
  const segments = metricChartSegments([
    { timestamp: 0, value: 25 }, { timestamp: 1000, value: 50 },
    { timestamp: 2000, value: null }, { timestamp: 4000, value: 0 },
  ]);
  assert.deepEqual(segments.map((points) => points.length), [2, 1]);
  assert.equal(segments[0][1].x, 60);
  assert.equal(segments[1][0].y, 78);
});

test("disk throughput retains MB/s so different samples share one chart scale", () => {
  let current;
  for (const [index, speed] of [1000, 2000, 500].entries()) {
    const measurement = sample(index);
    measurement.storages[0].readMbps = speed;
    measurement.storages[0].writeMbps = 2000 - speed;
    measurement.storages[0].maxThroughputMbps = Math.max(1000, speed, 2000 - speed);
    current = appendMetricSample(current, measurement);
  }
  const storage = { mountPoint: "/workspace" };
  const read = metricSeries(current.history, "storage", storage, "read");
  const write = metricSeries(current.history, "storage", storage, "write");
  assert.deepEqual(read.map((point) => point.value), [1000, 2000, 500]);
  assert.deepEqual(write.map((point) => point.value), [1000, 0, 1500]);
  assert.deepEqual(metricChartSegments(read, 240, 88, 2000)[0].map((point) => point.y), [43, 8, 60.5]);
  assert.deepEqual(metricChartSegments(write, 240, 88, 2000)[0].map((point) => point.y), [43, 78, 25.5]);
});

test("REST refetch and WebSocket setQueryData append via the same query cache boundary", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const options = { queryKey: ["metrics"], structuralSharing: appendMetricSample };
  try {
    await client.fetchQuery({ ...options, queryFn: async () => sample(0) });
    client.setQueryData(["metrics"], sample(1));
    assert.deepEqual(values(client.getQueryData(["metrics"])), [25, 25]);
    let completeRequest;
    const inFlight = client.fetchQuery({ ...options, queryFn: () => new Promise((resolve) => { completeRequest = resolve; }) });
    client.setQueryData(["metrics"], sample(3));
    completeRequest(sample(2));
    await inFlight;
    assert.equal(client.getQueryData(["metrics"]).timestamp, sample(3).timestamp);
    assert.deepEqual(values(client.getQueryData(["metrics"])), [25, 25, 25]);
  } finally {
    client.clear();
  }
});
