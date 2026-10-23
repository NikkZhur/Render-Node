import assert from "node:assert/strict";
import test from "node:test";

import { presentMetrics } from "../src/metricPresentation.js";

const gpu = {
  id: 1,
  uuid: "MIG-test",
  name: "NVIDIA Test GPU MIG 1g.24gb",
  utilization_percent: null,
  memory_used_bytes: 0.5 * 1024 ** 3,
  memory_total_bytes: 23.62 * 1024 ** 3,
  temperature_celsius: null,
  power_watts: null,
};

test("keeps MIG identity and memory when optional telemetry is unavailable", () => {
  const result = presentMetrics({ cpus: [], gpus: [gpu], storages: [] });

  assert.deepEqual(result.devices, [{
    id: 1,
    uuid: "MIG-test",
    name: gpu.name,
    utilization: null,
    memoryUsed: 0.5,
    memoryTotal: 23.6,
    temperature: null,
    available: true,
  }]);
});

test("distinguishes a measured zero from missing telemetry for every GPU type", () => {
  const result = presentMetrics({
    cpus: [],
    gpus: [
      gpu,
      { ...gpu, id: 2, uuid: "GPU-idle", utilization_percent: 0, temperature_celsius: 0 },
      { ...gpu, id: 3, uuid: "GPU-busy", utilization_percent: 72.7, temperature_celsius: 60.2 },
    ],
    storages: [],
  });

  assert.deepEqual(result.devices.map((device) => device.utilization), [null, 0, 73]);
  assert.deepEqual(result.devices.map((device) => device.temperature), [null, 0, 60]);
});

test("keeps unknown CPU temperature consistent with GPU telemetry", () => {
  const result = presentMetrics({
    cpus: [{
      id: 0, name: "CPU", cores: 16, utilization_percent: 0,
      memory_used_bytes: 8 * 1024 ** 3, memory_total_bytes: 64 * 1024 ** 3,
      temperature_celsius: null,
    }],
    gpus: [],
    storages: [],
  });

  assert.equal(result.processors[0].temperature, null);
  assert.equal(result.processors[0].utilization, 0);
  assert.equal(result.processors[0].memoryUsed, 8);
});
