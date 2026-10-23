import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

// All API and WebSocket traffic is intercepted: this runs against a normal Vite
// server without using any backend, existing database, Blender or GPU.
const targetUrl = process.env.TARGET_URL ?? "http://127.0.0.1:15175";
const output = fileURLToPath(new URL("../test-results/", import.meta.url));
const gb = 1024 ** 3;
const metrics = (tick, load = 25, swap = false) => ({
  timestamp: new Date(1_800_000_000_000 + tick * 1000).toISOString(), cpus: [], storages: [], websocket_clients: 1,
  gpus: [load, 80].map((value, i) => ({ id: swap ? 1 - i : i, uuid: `MIG-${i}`, name: "NVIDIA Same MIG GPU", utilization_percent: value, memory_used_bytes: 4 * gb, memory_total_bytes: 24 * gb, temperature_celsius: null, power_watts: null })),
});
async function fixture(context, unknown = false) {
  const state = { pages: [], writes: [], unexpected: [], errors: [], sockets: [], metric: metrics(0) };
  state.jobs = Array.from({ length: 13 }, (_, i) => ({
    id: `00000000-0000-4000-8000-${String(i + 1).padStart(12, "0")}`, name: `Fixture job ${i + 1}`, source_filename: "fixture.blend",
    status: i === 10 ? "rendering" : i > 10 ? "queued" : "ready", blender_version: "4.5.11", engine: "CYCLES", device: "CUDA", gpu_ids: [0, 1],
    gpu_render_mode: "FRAME_PER_GPU", frame_mode: "ALL", frame_start: null, frame_end: null, current_frame: null, progress: 0,
    process_pid: null, created_at: new Date().toISOString(), started_at: null, finished_at: null, exit_code: null, error: null,
  }));
  context.on("page", (page) => page.on("pageerror", (error) => state.errors.push(error.message)));
  await context.routeWebSocket("**/api/v1/events", (socket) => state.sockets.push(socket));
  await context.route("**/api/**", async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname.replace("/api/v1", "");
    let json;
    if (path === "/access/claim") json = {};
    else if (path === "/jobs/summary") {
      if (unknown) return route.fulfill({ status: 503, json: { error: { message: "Fixture unavailable" } } });
      json = { queued_count: state.jobs.filter((j) => j.status === "queued").length, rendering_count: state.jobs.filter((j) => j.status === "rendering").length };
    } else if (path === "/jobs/page") {
      const page = Number(url.searchParams.get("page")), size = Number(url.searchParams.get("page_size"));
      state.pages.push(page);
      json = { items: state.jobs.slice((page - 1) * size, page * size), total: 13, page, page_size: size, pages: 2 };
    } else if (path === "/devices") json = metrics(0).gpus.map((gpu) => ({ ...gpu, available: true }));
    else if (path === "/system/metrics") json = state.metric;
    else if (path === "/system/capabilities") json = { runner: { available: true, mode: "local", message: "Ready to render" } };
    else if (path === "/blender/versions") json = ["4.5.11", "4.2.22"].map((version, i) => ({ version, state: "installed", active: i === 0, supported: true }));
    else if (path === "/blender/releases") json = [];
    else if (path.endsWith("/artifacts")) {
      assert.equal(url.searchParams.get("kind"), "blender_log", "Gallery fetched every artifact");
      json = [];
    } else if (path.endsWith("/frames")) json = { items: [], total: 0, pages: 0, page: 1, page_size: 50 };
    else if (path.endsWith("/logs/blender/tail")) json = { lines: [] };
    else if (request.method() === "PUT" && path.startsWith("/jobs/")) {
      const payload = request.postDataJSON(); state.writes.push(payload);
      json = state.jobs.find((job) => path.endsWith(job.id)); Object.assign(json, payload);
    } else if (request.method() === "POST" && path.endsWith("/start")) {
      json = state.jobs.find((job) => path.includes(job.id)); json.status = "queued";
    } else { state.unexpected.push(`${request.method()} ${path}`); return route.fulfill({ status: 404, json: {} }); }
    return route.fulfill({ json });
  });
  state.send = (event) => state.sockets.at(-1).send(JSON.stringify(event));
  return state;
}
const device = async (page, name) => {
  await page.getByRole("button", { name: /^Compute device:/ }).click();
  await page.getByRole("option", { name, exact: true }).click();
};
const samples = (page, count) => page.waitForFunction((count) => document.querySelector('[data-resource-id="gpu:MIG-0"] .metric-chart')?.dataset.samples === String(count), count);
const queued = (page, count) => page.locator(".summary-queue strong").getByText(String(count), { exact: true }).waitFor();
const fits = async (page) => assert(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth));
const browser = await chromium.launch({ headless: true });
try {
  await mkdir(output, { recursive: true });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const state = await fixture(context), page = await context.newPage();
  await page.goto(targetUrl, { waitUntil: "networkidle" });
  await queued(page, 2);
  assert(state.pages.every((page) => page === 1), "History prefetched");
  await page.locator(".version-button").click();
  assert(await page.getByRole("button", { name: "Make active" }).isDisabled());
  await page.getByRole("button", { name: "Close version manager" }).click();
  await page.getByRole("button", { name: "Next jobs page" }).click();
  await page.locator(".job-row").getByText("Fixture job 11", { exact: true }).waitFor(); await queued(page, 2);
  await page.getByRole("button", { name: "Previous jobs page" }).click();
  await page.locator(".job-row").getByText("Fixture job 1", { exact: true }).waitFor();
  assert.equal(await page.locator(".summary-time strong").textContent(), "—");
  await page.getByText("SCENE RANGE UNKNOWN", { exact: true }).waitFor();

  const chart = page.locator('[data-resource-id="gpu:MIG-0"] .metric-chart').first();
  await samples(page, 1); assert.equal(await chart.locator(".metric-line").count(), 0); assert.equal(await chart.locator("circle").count(), 1);
  state.send({ type: "system.metrics_updated", metrics: metrics(1, 75) }); await samples(page, 2);
  assert.equal(await chart.locator(".metric-line").getAttribute("d"), "M 0.0 60.5 L 240.0 25.5");
  state.send({ type: "system.metrics_updated", metrics: metrics(2, null) }); await samples(page, 3);
  await page.locator('[data-resource-id="gpu:MIG-0"] [aria-label="Load: Unavailable"]').waitFor();
  state.send({ type: "system.metrics_updated", metrics: metrics(3, 0) }); await samples(page, 4);
  assert.equal(await chart.locator(".metric-line").count(), 1); assert.equal(await chart.locator("circle").getAttribute("cy"), "78");
  state.send({ type: "system.metrics_updated", metrics: metrics(4, 50, true) }); await samples(page, 5);
  assert.equal(await chart.locator(".metric-line").count(), 2);
  assert.equal(await page.locator('[data-resource-id="gpu:MIG-0"] .resource-identity span').textContent(), "GPU 02");
  for (let tick = 5; tick < 70; tick++) state.send({ type: "system.metrics_updated", metrics: metrics(tick) });
  await samples(page, 60);
  const lastPath = await chart.locator(".metric-line").last().getAttribute("d");
  state.send({ type: "system.metrics_updated", metrics: metrics(1, 99) });
  await page.waitForTimeout(100); assert.equal(await chart.locator(".metric-line").last().getAttribute("d"), lastPath);

  const disk = page.locator('[data-resource-id="storage:/workspace"]');
  for (const [tick, readMbps, writeMbps] of [[70, 1000, 1000], [71, 2000, 0]]) {
    state.send({ type: "system.metrics_updated", metrics: {
      ...metrics(tick),
      storages: [{ id: 0, name: "Fast disk", mount_point: "/workspace", total_bytes: 1000 * gb, free_bytes: 500 * gb, read_mbps: readMbps, write_mbps: writeMbps, status: "healthy" }],
    } });
    await disk.locator(`[aria-label="Read: ${readMbps} MB/s"]`).waitFor();
  }
  const diskPaths = await disk.locator(".metric-line").evaluateAll((paths) => paths.map((path) => path.getAttribute("d")));
  // Used, read and write each retain the two observed samples. Read/write share
  // a 2000 MB/s scale instead of flattening both measurements to a full chart.
  assert.match(diskPaths[1], /^M [\d.]+ 43\.0 L 240\.0 8\.0$/);
  assert.match(diskPaths[2], /^M [\d.]+ 43\.0 L 240\.0 78\.0$/);

  const setup = page.locator(".setup-panel");
  await device(page, "CPU"); await setup.getByText("NOT USED · CPU MODE", { exact: true }).waitFor();
  assert.equal(await setup.locator(".gpu-option.selected").count(), 0);
  for (const button of await setup.locator(".gpu-option").all()) { assert(await button.isDisabled()); assert.equal(await button.getAttribute("aria-pressed"), "false"); }
  assert.equal(await setup.getByRole("group", { name: "GPU render mode", exact: true }).count(), 0);
  const saved = page.waitForResponse((response) => response.request().method() === "PUT");
  await setup.getByRole("button", { name: "Save changes" }).click(); await saved;
  assert.deepEqual(state.writes.at(-1).gpu_ids, []); assert.equal(state.writes.at(-1).device, "CPU");
  await device(page, "CUDA"); await setup.getByText("2 SELECTED", { exact: true }).waitFor();
  assert.equal(await setup.locator(".gpu-option.selected").count(), 2);
  await setup.locator(".gpu-option").last().click(); await setup.getByText("1 SELECTED", { exact: true }).waitFor();
  assert.equal(await setup.getByRole("group", { name: "GPU render mode", exact: true }).count(), 0);
  await device(page, "CPU"); await device(page, "OptiX"); assert.equal(await setup.locator(".gpu-option.selected").count(), 1);

  state.jobs.filter((job) => job.status === "queued").forEach((job) => { job.status = "cancelled"; });
  state.send({ type: "job.status_changed", job_id: state.jobs[11].id }); await queued(page, 0);
  await page.locator(".version-button").click(); assert(await page.getByRole("button", { name: "Make active" }).isDisabled());
  await page.getByRole("button", { name: "Close version manager" }).click();
  state.jobs[10].status = "completed"; state.metric = metrics(100);
  const before = state.sockets.length; state.sockets.at(-1).close({ code: 1012, reason: "Fixture reconnect" });
  await page.waitForTimeout(1200); assert(state.sockets.length > before);
  await page.locator(".version-button").click(); assert(await page.getByRole("button", { name: "Make active" }).isEnabled());
  await page.getByRole("button", { name: "Close version manager" }).click();
  await fits(page); await page.screenshot({ path: `${output}project-regressions-desktop.png`, fullPage: true });
  await setup.getByRole("button", { name: "Start render" }).click(); await queued(page, 1);
  assert.deepEqual(state.writes.at(-1).gpu_ids, [0]); assert.equal(state.writes.at(-1).device, "OPTIX");
  assert.deepEqual(state.errors, []); assert.deepEqual(state.unexpected, []); await context.close();

  const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const mobileState = await fixture(mobileContext), mobile = await mobileContext.newPage();
  await mobile.goto(targetUrl, { waitUntil: "networkidle" }); await device(mobile, "CPU");
  await mobile.getByText("NOT USED · CPU MODE", { exact: true }).waitFor(); await fits(mobile);
  await mobile.screenshot({ path: `${output}project-regressions-mobile.png`, fullPage: true });
  assert.deepEqual(mobileState.errors, []); assert.deepEqual(mobileState.unexpected, []); await mobileContext.close();

  const unknownContext = await browser.newContext(); await fixture(unknownContext, true);
  const unknown = await unknownContext.newPage(); await unknown.goto(targetUrl, { waitUntil: "networkidle" });
  assert.equal(await unknown.locator(".summary-queue strong").textContent(), "—");
  await unknown.locator(".version-button").click();
  await unknown.getByText("Queue status is unavailable. Waiting before switching versions.", { exact: true }).waitFor();
  assert(await unknown.getByRole("button", { name: "Make active" }).isDisabled()); await unknownContext.close();
  console.log("Regression QA passed: queue pages/events/reconnect/mutations, real bounded metrics and gaps, CPU/GPU selection and payloads, filtered gallery, unknown labels, desktop/mobile.");
} finally { await browser.close(); }
