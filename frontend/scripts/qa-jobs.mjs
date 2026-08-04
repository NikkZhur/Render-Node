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

const assertNoHorizontalOverflow = async (page, label) => {
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
  const completedRow = desktop.locator(".job-row").filter({ hasText: "Loft still" });
  await completedRow.click();
  assert(await setup.getByText("Read only", { exact: true }).isVisible(), "Completed job is not read-only");
  assert(await setup.getByText("loft_camera_03.blend", { exact: true }).isVisible(), "Selected job scene is missing");
  assert(await setup.getByText("Blender 4.2.22", { exact: true }).isVisible(), "Selected job runtime is incorrect");
  assert(await setup.getByRole("button", { name: "Render engine: Cycles" }).isDisabled(), "Completed job engine remains editable");
  assert(await setup.getByRole("button", { name: "Compute device: OptiX" }).isDisabled(), "Completed job device remains editable");
  assert(await setup.getByRole("button", { name: "Single" }).getAttribute("aria-pressed") === "true", "Selected job frame mode is incorrect");
  assert(await setup.locator('.frame-range input').inputValue() === "48", "Selected job frame is incorrect");

  await setup.getByRole("button", { name: "Rerender as new job" }).click();
  await setup.getByText("Editable", { exact: true }).waitFor();
  assert(await setup.getByText("loft_camera_03.blend", { exact: true }).isVisible(), "Rerender did not reuse the scene");
  assert(await setup.getByText("Blender 4.2.22", { exact: true }).isVisible(), "Rerender did not copy the runtime");
  assert(await setup.getByRole("button", { name: "Single" }).getAttribute("aria-pressed") === "true", "Rerender did not copy frame mode");
  assert(await setup.locator('.frame-range input').inputValue() === "48", "Rerender did not copy the frame");
  assert(await desktop.locator(".job-row").first().getByText("Loft still rerender", { exact: true }).isVisible(), "Rerender did not create a separate job");
  assert(await desktop.getByText("No artifacts yet", { exact: true }).isVisible(), "Rerender inherited old artifacts");

  await setup.getByRole("button", { name: "Render engine: Cycles" }).click();
  await desktop.getByRole("listbox", { name: "Render engine" }).getByRole("option", { name: "Eevee" }).click();
  const saveButton = setup.getByRole("button", { name: "Save changes" });
  assert(await saveButton.isEnabled(), "Changed rerender settings cannot be saved");
  await saveButton.click();
  assert(await saveButton.isDisabled(), "Saved rerender remains dirty");
  await desktop.locator(".job-row").filter({ hasText: "Forest study" }).click();
  await desktop.locator(".job-row").filter({ hasText: "Loft still rerender" }).click();
  assert(await setup.getByRole("button", { name: "Render engine: Eevee" }).isVisible(), "Saved rerender settings were not retained");
  await desktop.screenshot({ path: path.join(outputDirectory, "jobs-rerender-desktop.png"), scale: "css" });
  await assertNoHorizontalOverflow(desktop, "Desktop rerender view");

  await desktop.getByRole("button", { name: "New job", exact: true }).click();
  assert(await setup.getByText("New render", { exact: true }).isVisible(), "New job did not open a draft");
  assert(await setup.getByText("Choose a .blend or ZIP", { exact: true }).isVisible(), "New job draft is not empty");
  assert(await setup.getByText("Blender 5.2.0", { exact: true }).isVisible(), "New job does not use the active runtime");
  await setup.locator('input[accept=".blend,.zip"]').setInputFiles({
    name: "fresh-scene.blend",
    mimeType: "application/octet-stream",
    buffer: Buffer.from("BLENDER-v300"),
  });
  await setup.getByText("fresh-scene.blend", { exact: true }).waitFor();
  assert(await desktop.locator(".job-row").first().getByText("fresh-scene", { exact: true }).isVisible(), "Uploaded draft did not become a new job");
  await setup.getByRole("button", { name: "Start render" }).click();
  await setup.getByText("Read only", { exact: true }).waitFor();
  assert(await setup.getByRole("button", { name: "Render engine: Cycles" }).isDisabled(), "Settings remain editable after start");
  assert(await setup.getByRole("button", { name: "Cancel render" }).isVisible(), "Started job has no cancel action");
  await setup.getByRole("button", { name: "Cancel render" }).click();
  await setup.getByRole("button", { name: "Rerender as new job" }).waitFor();
  await desktopContext.close();

  const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const mobile = await mobileContext.newPage();
  watchErrors(mobile);
  await mobile.goto(targetUrl, { waitUntil: "networkidle" });
  await mobile.locator(".job-row").filter({ hasText: "Loft still" }).click();
  await mobile.locator(".setup-panel").getByText("Read only", { exact: true }).waitFor();
  await assertNoHorizontalOverflow(mobile, "Mobile selected-job view");
  const mobileSetup = await mobile.locator(".setup-panel").boundingBox();
  assert(mobileSetup && mobileSetup.x >= 0 && mobileSetup.x + mobileSetup.width <= 390, "Mobile job setup exceeds the viewport");
  await mobile.evaluate(() => window.scrollTo(0, 0));
  await mobile.screenshot({ path: path.join(outputDirectory, "jobs-readonly-mobile.png"), fullPage: true, scale: "css" });
  await mobileContext.close();

  if (browserErrors.length > 0) throw new Error(`Browser errors:\n${browserErrors.join("\n")}`);
  console.log("Playwright jobs QA passed: selected settings, lock, rerender, new job, upload, and responsive fit.");
} finally {
  await browser.close();
}
