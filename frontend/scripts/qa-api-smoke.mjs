import { chromium } from "playwright";

const targetUrl = process.env.TARGET_URL ?? "http://127.0.0.1:5173";
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const browserErrors = [];
const sceneStem = `browser-scene-${Date.now()}`;
const sceneFilename = `${sceneStem}.blend`;

page.on("console", (message) => {
  if (message.type() === "error") browserErrors.push(`console: ${message.text()}`);
});
page.on("pageerror", (error) => browserErrors.push(`page: ${error.message}`));

try {
  await page.goto(targetUrl, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Blender 4.5.11" }).click();
  const versionDialog = page.getByRole("dialog", { name: "Blender versions" });
  await versionDialog.getByText("Blender 5.2.0", { exact: true }).waitFor();
  if ((await versionDialog.locator(".version-row").count()) !== 5) {
    throw new Error("Real version registry did not return all five bundled runtimes");
  }
  await versionDialog.getByRole("button", { name: "Close version manager" }).click();

  await page.locator('input[accept=".blend,.zip"]').setInputFiles({
    name: sceneFilename,
    mimeType: "application/octet-stream",
    buffer: Buffer.from("BLENDER-v300"),
  });

  const jobRow = page.locator(".job-row").filter({ hasText: sceneStem });
  await jobRow.getByText("Ready", { exact: true }).waitFor();
  if (!(await page.locator(".app-footer").getByText(/API connected/).isVisible())) {
    throw new Error("Frontend did not report the real API connection");
  }

  await page.reload({ waitUntil: "networkidle" });
  await page.locator(".job-row").filter({ hasText: sceneStem }).waitFor();
  if (!(await page.getByText(sceneFilename, { exact: true }).first().isVisible())) {
    throw new Error("Uploaded scene was not restored after reload");
  }
  if (browserErrors.length > 0) {
    throw new Error(`Browser errors:\n${browserErrors.join("\n")}`);
  }

  console.log("Playwright API smoke passed: versions loaded; upload reached READY and survived reload.");
} finally {
  await browser.close();
}
