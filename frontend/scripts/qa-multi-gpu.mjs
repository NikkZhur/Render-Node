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

const assertFit = async (page, label) => {
  const fit = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  assert(fit.scrollWidth <= fit.clientWidth, `${label} has horizontal overflow`);
};

try {
  await mkdir(outputDirectory, { recursive: true });

  const desktopContext = await browser.newContext({ viewport: { width: 1600, height: 900 } });
  const desktop = await desktopContext.newPage();
  watchErrors(desktop);
  await desktop.goto(targetUrl, { waitUntil: "networkidle" });
  const setup = desktop.locator(".setup-panel");
  let mode = setup.getByRole("group", { name: "GPU render mode" });
  assert(await mode.isVisible(), "Multi-GPU OptiX job does not show GPU render mode");
  assert(
    await mode.getByRole("button", { name: "Frame per GPU" }).getAttribute("aria-pressed") === "true",
    "Frame per GPU is not the default saved mode",
  );
  const modeBox = await mode.boundingBox();
  const allocationLabelBox = await setup.getByText("GPU allocation", { exact: true }).boundingBox();
  assert(
    modeBox && allocationLabelBox && modeBox.y + modeBox.height <= allocationLabelBox.y,
    "GPU render mode is not directly above GPU allocation",
  );

  await mode.getByRole("button", { name: "All GPUs per frame" }).click();
  const secondGpu = setup.locator(".gpu-option").nth(1);
  await secondGpu.click();
  assert(await mode.isHidden(), "GPU render mode remains visible with one selected GPU");
  await setup.getByRole("button", { name: "Save changes" }).click();
  await secondGpu.click();
  mode = setup.getByRole("group", { name: "GPU render mode" });
  assert(await mode.isVisible(), "GPU render mode did not return after selecting two GPUs");
  assert(
    await mode.getByRole("button", { name: "All GPUs per frame" }).getAttribute("aria-pressed") === "true",
    "Existing job did not restore its saved GPU render mode",
  );

  await desktop.getByRole("button", { name: "New job", exact: true }).click();
  mode = setup.getByRole("group", { name: "GPU render mode" });
  assert(
    await mode.getByRole("button", { name: "Frame per GPU" }).getAttribute("aria-pressed") === "true",
    "New draft does not default to Frame per GPU",
  );
  await setup.locator(".gpu-option").nth(1).click();
  assert(await mode.isHidden(), "Draft mode remains visible after removing the second GPU");
  await setup.locator(".gpu-option").nth(1).click();
  assert(
    await mode.getByRole("button", { name: "Frame per GPU" }).getAttribute("aria-pressed") === "true",
    "Draft did not return to Frame per GPU after restoring two GPUs",
  );
  await setup.getByRole("button", { name: "Compute device: OptiX" }).click();
  await desktop.getByRole("listbox", { name: "Compute device" }).getByRole("option", { name: "CPU" }).click();
  assert(await mode.isHidden(), "GPU render mode remains visible for CPU");
  await setup.getByRole("button", { name: "Compute device: CPU" }).click();
  await desktop.getByRole("listbox", { name: "Compute device" }).getByRole("option", { name: "OptiX" }).click();
  assert(
    await mode.getByRole("button", { name: "Frame per GPU" }).getAttribute("aria-pressed") === "true",
    "Draft did not normalize back to Frame per GPU after returning from CPU",
  );
  await assertFit(desktop, "Multi-GPU desktop");
  await desktop.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await desktop.screenshot({
    path: path.join(outputDirectory, "multi-gpu-desktop.png"),
    fullPage: true,
    scale: "css",
  });
  await desktopContext.close();

  const mobileContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    isMobile: true,
  });
  const mobile = await mobileContext.newPage();
  watchErrors(mobile);
  await mobile.goto(targetUrl, { waitUntil: "networkidle" });
  const mobileSetup = mobile.locator(".setup-panel");
  const mobileMode = mobileSetup.getByRole("group", { name: "GPU render mode" });
  assert(await mobileMode.isVisible(), "GPU render mode is not visible on mobile");
  const mobileModeBox = await mobileMode.boundingBox();
  const mobileSetupBox = await mobileSetup.boundingBox();
  assert(
    mobileModeBox && mobileSetupBox
      && mobileModeBox.x >= mobileSetupBox.x
      && mobileModeBox.x + mobileModeBox.width <= mobileSetupBox.x + mobileSetupBox.width,
    "Mobile GPU render mode is clipped by Job setup",
  );
  await assertFit(mobile, "Multi-GPU mobile");
  await mobile.screenshot({
    path: path.join(outputDirectory, "multi-gpu-mobile.png"),
    fullPage: true,
    scale: "css",
  });
  await mobileContext.close();

  if (browserErrors.length > 0) throw new Error(`Browser errors:\n${browserErrors.join("\n")}`);
  console.log("Multi-GPU QA passed: visibility, defaults, normalization, persistence, and responsive fit.");
} finally {
  await browser.close();
}
