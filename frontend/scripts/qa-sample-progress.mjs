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

const checkViewport = async (viewport, screenshotName) => {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const browserErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(`console: ${message.text()}`);
  });
  page.on("pageerror", (error) => browserErrors.push(`page: ${error.message}`));

  await page.goto(targetUrl, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: /Start render/ }).click();

  const sampleStat = page.locator(".progress-stat-samples");
  await sampleStat.getByText(/\d+ \/ 512/).waitFor();
  assert(await sampleStat.getByText("SAMPLES", { exact: true }).isVisible(), "Sample label is hidden");
  assert(await sampleStat.isVisible(), "Sample progress is hidden");

  const layout = await page.locator(".render-progress-block").evaluate((block) => {
    const bounds = block.getBoundingClientRect();
    const sampleBounds = block.querySelector(".progress-stat-samples").getBoundingClientRect();
    return {
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: document.documentElement.clientWidth,
      sampleBottom: sampleBounds.bottom,
      sampleLeft: sampleBounds.left,
      sampleRight: sampleBounds.right,
      sampleTop: sampleBounds.top,
      blockBottom: bounds.bottom,
      blockLeft: bounds.left,
      blockRight: bounds.right,
      blockTop: bounds.top,
    };
  });
  assert(layout.documentWidth <= layout.viewportWidth, "Page has horizontal overflow");
  assert(
    layout.sampleLeft >= layout.blockLeft
      && layout.sampleRight <= layout.blockRight
      && layout.sampleTop >= layout.blockTop
      && layout.sampleBottom <= layout.blockBottom,
    "Sample progress overflows the Current task panel",
  );
  assert(browserErrors.length === 0, browserErrors.join("\n"));

  await page.screenshot({ path: path.join(outputDirectory, screenshotName), fullPage: true });
  await context.close();
};

try {
  await mkdir(outputDirectory, { recursive: true });
  await checkViewport({ width: 1440, height: 900 }, "sample-progress-desktop.png");
  await checkViewport({ width: 390, height: 844 }, "sample-progress-mobile.png");
  console.log("Sample progress QA passed for desktop and mobile viewports.");
} finally {
  await browser.close();
}
