import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const targetUrl = process.env.TARGET_URL ?? "http://127.0.0.1:5173";
const outputDirectory = fileURLToPath(new URL("../test-results/", import.meta.url));
const browser = await chromium.launch({ headless: true });

const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

const readyJob = {
  id: "00000000-0000-4000-8000-000000000001",
  name: "Runner capability check",
  source_filename: "capability-check.blend",
  status: "ready",
  blender_version: "4.5.11",
  engine: "BLENDER_EEVEE",
  device: "CPU",
  gpu_ids: [],
  frame_mode: "SINGLE",
  frame_start: 1,
  frame_end: null,
  current_frame: null,
  progress: 0,
  process_pid: null,
  created_at: new Date().toISOString(),
  started_at: null,
  finished_at: null,
  exit_code: null,
  error: null,
};

const routeReadyJob = (page) => page.route("**/api/v1/jobs", (route) => {
  if (route.request().method() !== "GET") return route.continue();
  return route.fulfill({ contentType: "application/json", json: [readyJob] });
});

const assertFit = async (page, label) => {
  const fit = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  assert(fit.scrollWidth <= fit.clientWidth, `${label} has horizontal overflow`);
};

try {
  await mkdir(outputDirectory, { recursive: true });

  const availableContext = await browser.newContext({ viewport: { width: 1600, height: 900 } });
  const availablePage = await availableContext.newPage();
  await routeReadyJob(availablePage);
  await availablePage.route(`**/api/v1/jobs/${readyJob.id}`, (route) => {
    if (route.request().method() === "PUT") {
      return route.fulfill({ contentType: "application/json", json: readyJob });
    }
    return route.continue();
  });
  await availablePage.route(`**/api/v1/jobs/${readyJob.id}/start`, (route) => route.fulfill({
    status: 409,
    contentType: "application/json",
    json: {
      error: {
        code: "job_blender_not_active",
        message: "The Blender version recorded for this job is not active",
        request_id: "runner-capability-qa",
      },
    },
  }));
  await availablePage.goto(targetUrl, { waitUntil: "networkidle" });
  const availableSetup = availablePage.locator(".setup-panel");
  await availableSetup.getByText("Ready to render trusted Blender scenes", { exact: true }).waitFor();
  const startButton = availableSetup.getByRole("button", { name: "Start render" });
  assert(await startButton.isEnabled(), "Available runner did not enable Start render");
  const panelBeforeError = await availableSetup.boundingBox();
  const runtimeBeforeError = await availableSetup.locator(".active-runtime").boundingBox();
  const actionBeforeError = await startButton.boundingBox();
  await startButton.click();
  const setupError = availableSetup.getByRole("alert");
  await setupError.getByText("The Blender version recorded for this job is not active", { exact: true }).waitFor();
  const panelAfterError = await availableSetup.boundingBox();
  const runtimeAfterError = await availableSetup.locator(".active-runtime").boundingBox();
  const actionAfterError = await startButton.boundingBox();
  const errorBox = await setupError.boundingBox();
  assert(panelBeforeError && panelAfterError && panelBeforeError.height === panelAfterError.height, "Setup panel grew when the error appeared");
  assert(runtimeBeforeError && runtimeAfterError && Math.abs(runtimeBeforeError.y - runtimeAfterError.y) <= 1, "Runtime row moved instead of using the free space above it");
  assert(actionBeforeError && actionAfterError && Math.abs(actionBeforeError.y - actionAfterError.y) <= 1, "Setup actions moved when the error appeared");
  assert(errorBox && runtimeAfterError && errorBox.y + errorBox.height < runtimeAfterError.y, "Setup error was not placed above Runtime");
  await assertFit(availablePage, "Available runner desktop");
  await availablePage.screenshot({ path: path.join(outputDirectory, "runner-error-layout-desktop.png"), scale: "css" });
  await availableContext.close();

  const unavailableContext = await browser.newContext({ viewport: { width: 1600, height: 900 } });
  const unavailablePage = await unavailableContext.newPage();
  await routeReadyJob(unavailablePage);
  await unavailablePage.route("**/api/v1/system/capabilities", (route) => route.fulfill({
    contentType: "application/json",
    json: {
      runner: {
        available: false,
        mode: "disabled",
        message: "Local trusted runner is disabled in configuration",
      },
    },
  }));
  await unavailablePage.goto(targetUrl, { waitUntil: "networkidle" });
  const unavailableSetup = unavailablePage.locator(".setup-panel");
  await unavailableSetup.getByText("Local trusted runner is disabled in configuration", { exact: true }).waitFor();
  assert(await unavailableSetup.getByRole("button", { name: "Start render" }).isDisabled(), "Unavailable runner left Start render enabled");
  await assertFit(unavailablePage, "Unavailable runner desktop");
  await unavailablePage.screenshot({ path: path.join(outputDirectory, "runner-unavailable-desktop.png"), scale: "css" });
  await unavailableContext.close();

  const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const mobilePage = await mobileContext.newPage();
  await routeReadyJob(mobilePage);
  await mobilePage.route("**/api/v1/system/capabilities", (route) => route.fulfill({
    contentType: "application/json",
    json: { runner: { available: false, mode: "disabled", message: "Local trusted runner is disabled in configuration" } },
  }));
  await mobilePage.goto(targetUrl, { waitUntil: "networkidle" });
  await mobilePage.locator(".setup-panel").getByText("Local trusted runner is disabled in configuration", { exact: true }).waitFor();
  await assertFit(mobilePage, "Unavailable runner mobile");
  await mobilePage.screenshot({ path: path.join(outputDirectory, "runner-unavailable-mobile.png"), fullPage: true, scale: "css" });
  await mobileContext.close();

  const errorContext = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const errorPage = await errorContext.newPage();
  await routeReadyJob(errorPage);
  await errorPage.route("**/api/v1/system/capabilities", (route) => route.abort());
  await errorPage.goto(targetUrl, { waitUntil: "networkidle" });
  const errorSetup = errorPage.locator(".setup-panel");
  await errorSetup.getByText("Render runner status is unavailable", { exact: true }).waitFor();
  assert(await errorSetup.getByRole("button", { name: "Start render" }).isDisabled(), "Capability API error left Start render enabled");
  await errorContext.close();

  console.log("Runner capability QA passed: ready/unavailable/error states, Start protection, and desktop/mobile fit.");
} finally {
  await browser.close();
}
