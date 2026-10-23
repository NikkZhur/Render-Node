import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { chromium } from "playwright";

const target = process.env.TARGET_URL ?? "http://127.0.0.1:5173";
const output = process.env.QA_OUTPUT_DIR ?? "/tmp/render-node-scene-analysis-qa";
const id = "12345678-1234-4234-8234-123456789012";
const sceneMetadata = {
  engine: "CYCLES", frame_start: 32, frame_end: 38, frame_step: 2,
  resolution_x: 1920, resolution_y: 1080, resolution_percentage: 100,
  camera: "Camera", use_osl: false, use_guiding: false,
};

async function fixture(context) {
  const state = { jobs: [], sockets: [], uploads: 0, writes: [], errors: [], unexpected: [], polls: 0 };
  context.on("page", (page) => page.on("pageerror", (error) => state.errors.push(error.message)));
  await context.routeWebSocket("**/api/v1/events", (socket) => state.sockets.push(socket));
  await context.route("**/api/**", async (route) => {
    const request = route.request(), url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", ""), method = request.method();
    let json;
    if (path === "/access/claim") json = {};
    else if (path === "/jobs/page") {
      state.polls++;
      json = { items: state.jobs, page: 1, page_size: 10, total: state.jobs.length, pages: state.jobs.length ? 1 : 0 };
    } else if (path === "/jobs/summary") json = { queued_count: 0, rendering_count: 0 };
    else if (path === "/devices") json = [0, 1].map((id) => ({ id, uuid: `GPU-${id}`, name: `NVIDIA Test ${id}`, memory_total_bytes: 24 * 1024 ** 3 }));
    else if (path === "/system/metrics") json = { timestamp: new Date().toISOString(), cpus: [], gpus: [], storages: [] };
    else if (path === "/system/capabilities") json = { runner: { available: true, message: "Ready to render", mode: "local" } };
    else if (path === "/blender/versions") json = [{ version: "4.5.11", active: true, supported: true, state: "installed" }];
    else if (path === "/blender/releases") json = [];
    else if (path.endsWith("/frames")) json = { items: [], total: 0, pages: 0, page: 1, page_size: 50 };
    else if (path.endsWith("/artifacts")) json = [];
    else if (path.endsWith("/logs/blender/tail")) json = { lines: [] };
    else if (path === "/jobs" && method === "POST") {
      json = {
        ...request.postDataJSON(), id, status: "created", source_filename: null,
        created_at: new Date().toISOString(), progress: 0, current_frame: null,
        analysis_id: null, analysis_status: "none", analysis_error: null, auto_configured: false,
      };
      state.jobs = [json];
    } else if (path.endsWith("/uploads") && method === "POST") {
      assert.match(request.postData(), /name="auto_configure"\r\n\r\ntrue/);
      state.uploads++;
      json = Object.assign(state.jobs[0], {
        status: "ready", source_filename: `scene-${state.uploads}.blend`,
        analysis_id: `analysis-${state.uploads}`, analysis_status: "pending",
        analysis_error: null, scene_metadata: null, auto_configured: false,
      });
    } else if (path === `/jobs/${id}` && method === "PUT") {
      assert(!["pending", "running"].includes(state.jobs[0].analysis_status));
      const payload = request.postDataJSON();
      state.writes.push(payload);
      json = Object.assign(state.jobs[0], payload, { auto_configured: false });
    } else {
      state.unexpected.push(`${method} ${path}`);
      return route.fulfill({ status: 404, json: {} });
    }
    await route.fulfill({ json });
  });
  state.send = () => state.sockets.at(-1).send(JSON.stringify({ type: "job.analysis_updated", job_id: id }));
  return state;
}

const browser = await chromium.launch({ headless: true });
try {
  await mkdir(output, { recursive: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const state = await fixture(context), page = await context.newPage();
  await page.goto(target, { waitUntil: "networkidle" });
  const setup = page.locator(".setup-panel");
  const upload = async () => {
    const next = state.uploads + 1;
    const response = page.waitForResponse((response) => response.url().endsWith("/uploads"));
    await setup.locator('input[type="file"]').setInputFiles({ name: "scene.blend", mimeType: "application/octet-stream", buffer: Buffer.from("BLENDER fixture") });
    assert.equal((await response).status(), 200);
    await setup.locator(".file-copy strong").getByText(`scene-${next}.blend`, { exact: true }).waitFor();
  };
  const complete = (values = {}) => Object.assign(state.jobs[0], {
    engine: "CYCLES", device: "OPTIX", gpu_ids: [1], gpu_render_mode: "ALL_GPUS_PER_FRAME",
    frame_mode: "RANGE", frame_start: 32, frame_end: 38, scene_metadata: sceneMetadata,
    analysis_status: "completed", auto_configured: true, ...values,
  });
  await upload();
  await setup.getByText("Analyzing scene…", { exact: true }).waitFor();
  assert(await setup.getByRole("button", { name: "Start render", exact: true }).isDisabled());
  assert(await setup.getByRole("button", { name: /^Render engine:/ }).isDisabled());
  assert(await setup.locator(".file-drop").isEnabled());
  await page.screenshot({ path: `${output}/analyzing.png`, fullPage: true });
  complete(); state.send();
  await setup.getByText("Settings selected automatically. You can change them below.").waitFor();
  assert.equal(await setup.getByLabel("Start", { exact: true }).inputValue(), "32");
  assert.equal(await setup.getByLabel("End", { exact: true }).inputValue(), "38");
  assert.equal(await setup.locator(".gpu-option.selected .gpu-index").textContent(), "01");
  await setup.getByText("32–38 · STEP 2", { exact: true }).waitFor();
  assert(await setup.getByRole("button", { name: "Start render", exact: true }).isEnabled());
  await page.screenshot({ path: `${output}/configured.png`, fullPage: true });

  await setup.getByRole("button", { name: /^Compute device:/ }).click();
  await page.getByRole("option", { name: "CUDA", exact: true }).click();
  await setup.getByLabel("Start", { exact: true }).fill("35");
  const before = state.polls; state.send();
  while (state.polls === before) await page.waitForTimeout(20);
  await setup.getByRole("button", { name: "Save changes", exact: true }).click();
  await page.waitForFunction(() => document.querySelector(".setup-actions .secondary-action")?.disabled);
  assert.equal(state.writes.at(-1).frame_start, 35);
  assert.equal(state.writes.at(-1).device, "CUDA");
  await page.reload({ waitUntil: "networkidle" });
  assert.equal(await setup.getByLabel("Start", { exact: true }).inputValue(), "35");

  await upload();
  await setup.getByText("Analyzing scene…", { exact: true }).waitFor();
  // Replacement remains available while a previous analysis is running.
  await upload();
  assert.equal(state.uploads, 3);
  complete({ engine: "BLENDER_EEVEE", device: "CPU", gpu_ids: [], frame_mode: "SINGLE", frame_start: 7, frame_end: null,
    scene_metadata: { ...sceneMetadata, engine: "BLENDER_EEVEE_NEXT", frame_start: 7, frame_end: 7 } });
  // Deliberately omit the event: polling must complete setup even with a live WS.
  await setup.getByRole("button", { name: "Render engine: Eevee", exact: true }).waitFor();
  assert(await setup.getByRole("button", { name: "Compute device: Automatic", exact: true }).isDisabled());
  assert.equal(await setup.getByLabel("Frame", { exact: true }).inputValue(), "7");
  await setup.getByText("MANAGED BY BLENDER", { exact: true }).waitFor();

  await upload();
  await setup.getByText("Analyzing scene…", { exact: true }).waitFor();
  Object.assign(state.jobs[0], { analysis_status: "failed", auto_configured: false, analysis_error: "Blender could not read the scene." });
  state.send();
  await setup.getByText(/Automatic setup unavailable/).waitFor();
  assert(await setup.getByRole("button", { name: "Start render", exact: true }).isEnabled());
  await page.setViewportSize({ width: 390, height: 844 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await page.screenshot({ path: `${output}/failure-mobile.png`, fullPage: true });
  assert.deepEqual(state.errors, []); assert.deepEqual(state.unexpected, []);
  console.log("Scene analysis QA passed: upload, async setup, manual edits/reload, replacement, polling, Eevee, failure and mobile.");
  await context.close();
} finally {
  await browser.close();
}
