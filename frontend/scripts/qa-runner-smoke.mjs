import { mkdir } from "node:fs/promises";
import { chromium } from "playwright";

const targetUrl = process.env.TARGET_URL ?? "http://127.0.0.1:5173";
const screenshotDirectory = process.env.QA_SCREENSHOT_DIR;
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const browserErrors = [];

page.on("console", (message) => {
  if (message.type() === "error") browserErrors.push(`console: ${message.text()}`);
});
page.on("pageerror", (error) => browserErrors.push(`page: ${error.message}`));

const uploadScene = async (marker) => {
  const stem = `runner-${marker.toLowerCase()}-${Date.now()}`;
  await page.locator('input[accept=".blend,.zip"]').setInputFiles({
    name: `${stem}.blend`,
    mimeType: "application/octet-stream",
    buffer: Buffer.from(`BLENDER-v300 ${marker}`),
  });
  const row = page.locator(".job-row").filter({ hasText: stem });
  await row.getByText("Ready", { exact: true }).waitFor();
  return row;
};

try {
  await page.goto(targetUrl, { waitUntil: "networkidle" });
  await page.getByText("No NVIDIA GPUs discovered · CPU mode", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Compute device: CPU" }).waitFor();
  await page.getByRole("button", { name: "Single", exact: true }).click();

  const completedRow = await uploadScene("BROWSER");
  await page.getByRole("button", { name: "Start render" }).click();
  await completedRow.getByText("Rendering", { exact: true }).waitFor({ timeout: 10_000 });
  await page.getByLabel("Blender live log").getByText(/RENDER_NODE_PROGRESS/).first().waitFor({ timeout: 10_000 });
  const singlePressed = await page
    .getByRole("button", { name: "Single", exact: true })
    .getAttribute("aria-pressed");
  if (singlePressed !== "true") {
    const frameControls = await page
      .getByRole("group", { name: "Frame mode" })
      .getByRole("button")
      .evaluateAll((buttons) => buttons.map((button) => ({
        label: button.textContent,
        pressed: button.getAttribute("aria-pressed"),
      })));
    throw new Error(
      `Job Setup no longer matches the submitted frame mode: ${JSON.stringify(frameControls)}`,
    );
  }
  await page.waitForFunction(() => {
    const progress = document.querySelector('[aria-label^="Render progress "]');
    return progress && progress.getAttribute("aria-label") !== "Render progress 0%";
  });

  if (screenshotDirectory) {
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(400);
    const initialFit = await page.evaluate(() => {
      const primaryAction = document.querySelector(".primary-action")?.getBoundingClientRect();
      return {
        actionBottom: primaryAction?.bottom ?? Number.POSITIVE_INFINITY,
        innerHeight: window.innerHeight,
        scrollX: window.scrollX,
      };
    });
    if (initialFit.scrollX !== 0 || initialFit.actionBottom > initialFit.innerHeight) {
      throw new Error(`Primary render controls do not fit the viewport: ${JSON.stringify(initialFit)}`);
    }
    await mkdir(screenshotDirectory, { recursive: true });
    await page.screenshot({
      animations: "disabled",
      path: `${screenshotDirectory}/rendering.png`,
      type: "png",
    });
  }

  await completedRow.getByText("Completed", { exact: true }).waitFor({ timeout: 10_000 });
  await page.getByText("Render completed", { exact: true }).waitFor();
  const preview = page.getByAltText("Preview frame 1");
  await preview.waitFor({ timeout: 10_000 });
  if (!(await preview.getAttribute("src"))?.endsWith("/frames/1/preview")) {
    throw new Error("The preview does not use the registered HTTP artifact URL");
  }

  await page.getByRole("button", { name: "Open frame 001 in full resolution" }).click();
  const fullFrame = page.getByRole("dialog", { name: "Frame 001 full resolution" });
  await fullFrame.getByAltText("Frame 001 full resolution").waitFor();
  await fullFrame.getByRole("button", { name: "Close full resolution frame" }).click();

  const frameDownload = page.getByRole("button", { name: "Download frame 001" });
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    frameDownload.click(),
  ]);
  if (download.suggestedFilename() !== "frame_0001.png") {
    throw new Error(`Unexpected frame download name: ${download.suggestedFilename()}`);
  }

  await page.getByRole("button", { name: "Open frame sequence, 1 frames" }).click();
  const framesDialog = page.getByRole("dialog", { name: "Frames 1–1" });
  await framesDialog.getByText("frame_0001.png", { exact: true }).waitFor();
  const [zipDownload] = await Promise.all([
    page.waitForEvent("download"),
    framesDialog.getByRole("button", { name: "Download all frames as ZIP" }).click(),
  ]);
  if (zipDownload.suggestedFilename() !== "frames.zip") {
    throw new Error(`Unexpected ZIP download name: ${zipDownload.suggestedFilename()}`);
  }
  await framesDialog.getByRole("button", { name: "Close frame sequence" }).click();

  if ((await page.locator(".resource-row").count()) < 2) {
    throw new Error("CPU and storage metrics were not restored from the backend");
  }
  await page.reload({ waitUntil: "networkidle" });
  await completedRow.click();
  await page.getByAltText("Preview frame 1").waitFor({ timeout: 10_000 });

  const cancelledRow = await uploadScene("SLOW");
  await page.getByText("No artifacts yet", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Start render" }).click();
  await cancelledRow.getByText("Rendering", { exact: true }).waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: "Cancel render" }).click();
  await cancelledRow.getByText("Cancelled", { exact: true }).waitFor({ timeout: 10_000 });

  const viewport = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  if (viewport.scrollWidth > viewport.clientWidth) {
    throw new Error(`Unexpected horizontal overflow: ${JSON.stringify(viewport)}`);
  }
  if (browserErrors.length > 0) {
    throw new Error(`Browser errors:\n${browserErrors.join("\n")}`);
  }

  console.log(
    "Playwright runner smoke passed: WebSocket log/progress, HTTP preview and downloads, reload recovery, metrics, and cancellation.",
  );
} finally {
  await browser.close();
}
