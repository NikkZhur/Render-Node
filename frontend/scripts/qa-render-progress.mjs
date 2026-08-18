import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const targetUrl = process.env.TARGET_URL ?? "http://127.0.0.1:5173";
const outputDirectory = fileURLToPath(new URL("../test-results/", import.meta.url));
const browser = await chromium.launch({ headless: true });
const browserErrors = [];

const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

const watchErrors = (page) => {
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(`console: ${message.text()}`);
  });
  page.on("pageerror", (error) => browserErrors.push(`page: ${error.message}`));
};

const jobRow = (page, name) => page.locator(".job-row").filter({ hasText: name });

const assertViewportFit = async (page, label) => {
  const fit = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  assert(fit.scrollWidth <= fit.clientWidth, `${label} has horizontal overflow`);
};

const previewGeometry = (page) => page.locator(".preview-panel").evaluate((panel) => {
  const panelBounds = panel.getBoundingClientRect();
  const viewportBounds = panel.querySelector(".render-viewport").getBoundingClientRect();
  const progressBounds = panel.querySelector(".render-progress-block").getBoundingClientRect();
  const actions = panel.querySelector(".preview-frame-actions")?.getBoundingClientRect() ?? null;
  return {
    panel: { top: panelBounds.top, bottom: panelBounds.bottom, height: panelBounds.height },
    viewport: { top: viewportBounds.top, bottom: viewportBounds.bottom, height: viewportBounds.height },
    progress: { top: progressBounds.top, bottom: progressBounds.bottom, height: progressBounds.height },
    actions: actions && { top: actions.top, bottom: actions.bottom },
  };
});

const assertProgressbarContract = async (block, expectedCount) => {
  const bars = block.getByRole("progressbar");
  assert(await bars.count() === expectedCount, `Expected ${expectedCount} progress bars`);
  const labels = await bars.evaluateAll((elements) => elements.map((element) => ({
    label: element.getAttribute("aria-label"),
    max: element.getAttribute("aria-valuemax"),
    min: element.getAttribute("aria-valuemin"),
    now: element.getAttribute("aria-valuenow"),
  })));
  assert(new Set(labels.map(({ label }) => label)).size === labels.length, "Progressbar labels are not unique");
  assert(labels.every(({ label, max, min, now }) => (
    label && max === "100" && min === "0" && Number.isFinite(Number(now))
  )), "A progressbar is missing its accessible numeric contract");
};

const startInitialDistributedJob = async (page, label) => {
  const previewBefore = await previewGeometry(page);
  const setupBefore = await page.locator(".setup-panel").boundingBox();
  const rightRailBefore = await page.locator(".right-rail").boundingBox();
  const duration = jobRow(page, "Atrium lighting").locator(".job-duration");
  assert(await duration.textContent() === "—", `${label} ready duration is not empty`);

  await page.getByRole("button", { name: "Start render" }).click();
  const block = page.locator(".render-progress-distributed");
  await block.waitFor();
  await block.getByText("Overall", { exact: true }).waitFor();
  await block.getByText(/~.+ remaining/).waitFor();

  const workerRows = block.locator(".worker-progress-list .progress-row");
  assert(await workerRows.count() === 2, `${label} does not show two actual workers`);
  assert(await workerRows.nth(0).getByText("GPU 1", { exact: true }).isVisible(), "GPU 1 order changed");
  assert(await workerRows.nth(1).getByText("GPU 2", { exact: true }).isVisible(), "GPU 2 order changed");
  const workerFrames = await workerRows.locator(".progress-row-detail > span:first-child").allTextContents();
  assert(workerFrames[0].endsWith("1"), "GPU 1 frame is missing");
  assert(workerFrames[1].endsWith("2"), "GPU 2 frame is missing");
  const workerPercents = await workerRows.locator(".progress-row-copy > b").allTextContents();
  assert(workerPercents[0] !== workerPercents[1], "Independent workers show identical progress");
  const workerSamples = await workerRows.locator(".progress-row-detail > span:nth-child(2)").allTextContents();
  assert(workerSamples.every((samples) => /\d+ \/ 512$/.test(samples)), "Worker samples are missing");
  assert(await block.locator(":scope > .progress-row-overall").count() === 1, "Overall is not the final row");
  await assertProgressbarContract(block, 3);
  assert(await block.getByRole("progressbar", { name: /GPU 1 \(GPU-mock-a\)/ }).count() === 1, "GPU 1 UUID is not accessible");
  assert(await block.getByRole("progressbar", { name: /GPU 2 \(GPU-mock-b\)/ }).count() === 1, "GPU 2 UUID is not accessible");

  const previewAfter = await previewGeometry(page);
  const setupAfter = await page.locator(".setup-panel").boundingBox();
  const rightRailAfter = await page.locator(".right-rail").boundingBox();
  assert(Math.abs(previewAfter.panel.height - previewBefore.panel.height) <= 1, `${label} outer preview panel height changed`);
  assert(previewAfter.progress.height > previewBefore.progress.height, `${label} Current task did not grow`);
  assert(
    previewAfter.viewport.height < previewBefore.viewport.height,
    `${label} Preview did not shrink: ${JSON.stringify({ previewBefore, previewAfter })}`,
  );
  assert(Math.abs(previewAfter.progress.bottom - previewBefore.progress.bottom) <= 1, `${label} Current task bottom moved`);
  assert(previewAfter.viewport.bottom <= previewAfter.progress.top + 1, `${label} Preview overlaps Current task`);
  if (previewAfter.actions) {
    assert(previewAfter.actions.bottom <= previewAfter.viewport.bottom, `${label} preview controls are clipped`);
  }
  if (setupBefore && setupAfter) {
    assert(Math.abs(setupAfter.y - setupBefore.y) <= 1, `${label} Job Setup moved`);
  }
  if (rightRailBefore && rightRailAfter) {
    assert(Math.abs(rightRailAfter.y - rightRailBefore.y) <= 1, `${label} Jobs/Artifacts moved`);
  }

  const firstDuration = await duration.textContent();
  await page.waitForTimeout(1150);
  const secondDuration = await duration.textContent();
  assert(firstDuration !== secondDuration, `${label} running duration did not update`);
  assert(await duration.getAttribute("aria-label") === `Render duration ${secondDuration}`, "Duration has no accessible value");
  await assertViewportFit(page, label);
  return { block, duration, previewAfter };
};

try {
  await mkdir(outputDirectory, { recursive: true });

  const desktopContext = await browser.newContext({ viewport: { width: 1600, height: 900 } });
  const desktop = await desktopContext.newPage();
  watchErrors(desktop);
  await desktop.goto(targetUrl, { waitUntil: "networkidle" });

  const { duration } = await startInitialDistributedJob(desktop, "Desktop FRAME_PER_GPU");
  await desktop.screenshot({
    animations: "disabled",
    path: path.join(outputDirectory, "render-progress-desktop.png"),
    fullPage: true,
    scale: "css",
  });

  await desktop.getByRole("button", { name: "Cancel render" }).click();
  await jobRow(desktop, "Atrium lighting").getByText("Cancelled", { exact: true }).waitFor();
  const terminalDuration = await duration.textContent();
  await desktop.waitForTimeout(1150);
  assert(await duration.textContent() === terminalDuration, "Terminal duration continues to change");
  assert(await desktop.locator(".progress-row-overall").getByText("Cancelled", { exact: true }).isVisible(), "Overall does not reflect cancellation");

  await jobRow(desktop, "Product turntable").click();
  const deviceButton = desktop.getByRole("button", { name: /Compute device:/ });
  await deviceButton.click();
  await desktop.getByRole("listbox", { name: "Compute device" }).getByRole("option", { name: "CPU" }).click();
  await desktop.getByRole("button", { name: "Save changes" }).click();
  await desktop.getByRole("button", { name: "Start render" }).click();
  const single = desktop.locator(".render-progress-single");
  await single.getByText(/~.+ remaining/).waitFor();
  await assertProgressbarContract(single, 1);
  assert(await single.getByText("ELAPSED", { exact: true }).isVisible(), "CPU elapsed time is missing");
  assert(await single.getByText("ETA", { exact: true }).isVisible(), "CPU ETA is missing");
  await desktop.getByRole("button", { name: "Cancel render" }).click();

  await jobRow(desktop, "Loft still").click();
  await desktop.getByRole("button", { name: "Rerender as new job" }).click();
  await desktop.getByRole("button", { name: "Start render" }).click();
  const allGpus = desktop.locator(".render-progress-all-gpus");
  await allGpus.getByText("All GPUs", { exact: true }).waitFor();
  await allGpus.getByText(/~.+ remaining/).waitFor();
  assert(await allGpus.locator("[data-progress-row='all-gpus']").count() === 1, "All GPUs row is missing");
  assert(await allGpus.locator("[data-progress-row^='GPU-']").count() === 0, "ALL_GPUS_PER_FRAME exposes false independent rows");
  assert(await allGpus.locator(".progress-row-overall").count() === 0, "All GPUs duplicates its overall bar");
  await assertProgressbarContract(allGpus, 1);
  await desktop.screenshot({
    animations: "disabled",
    path: path.join(outputDirectory, "render-progress-all-gpus.png"),
    fullPage: true,
    scale: "css",
  });
  await desktop.getByRole("button", { name: "Cancel render" }).click();
  await desktopContext.close();

  const mobileContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    isMobile: true,
  });
  const mobile = await mobileContext.newPage();
  watchErrors(mobile);
  await mobile.goto(targetUrl, { waitUntil: "networkidle" });
  const mobileResult = await startInitialDistributedJob(mobile, "Mobile FRAME_PER_GPU");
  const currentTaskFit = await mobileResult.block.evaluate((block) => {
    const bounds = block.getBoundingClientRect();
    return [...block.querySelectorAll(".progress-row, .render-progress-heading")].every((element) => {
      const child = element.getBoundingClientRect();
      return child.left >= bounds.left && child.right <= bounds.right
        && child.top >= bounds.top && child.bottom <= bounds.bottom;
    });
  });
  assert(currentTaskFit, "Mobile Current task content is clipped");
  await mobile.screenshot({
    animations: "disabled",
    path: path.join(outputDirectory, "render-progress-mobile.png"),
    fullPage: true,
    scale: "css",
  });
  await mobileContext.close();

  if (browserErrors.length > 0) throw new Error(`Browser errors:\n${browserErrors.join("\n")}`);
  console.log("Render progress QA passed: duration, ETA, strategy rows, stable geometry, responsive fit, and accessibility.");
} finally {
  await browser.close();
}
