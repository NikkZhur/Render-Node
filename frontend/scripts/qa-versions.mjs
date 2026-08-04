import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const targetUrl = process.env.TARGET_URL ?? "http://127.0.0.1:5173";
const outputDirectory = fileURLToPath(new URL("../test-results/", import.meta.url));
const archiveHtml = `
  <a href="Blender5.2/">Blender5.2/</a>
  <a href="Blender4.4/">Blender4.4/</a>
  <a href="Blender4.1/">Blender4.1/</a>
`;

const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

const preparePage = async (browser, options) => {
  const context = await browser.newContext(options);
  const page = await context.newPage();
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("https://download.blender.org/release/", (route) => route.fulfill({
    body: archiveHtml,
    contentType: "text/html",
    status: 200,
  }));
  await page.goto(targetUrl, { waitUntil: "networkidle" });
  return { context, errors, page };
};

const openAndInstall = async (page) => {
  await page.getByRole("button", { name: "Blender 5.2.0" }).click();
  const dialog = page.getByRole("dialog", { name: "Blender versions" });
  await dialog.waitFor();
  assert((await dialog.locator(".version-list .version-row").count()) === 2, "Expected two bundled versions");
  assert((await dialog.locator(".version-list .delete-version-button").count()) === 0, "Bundled versions expose deletion");

  await dialog.getByRole("button", { name: /Choose other versions/ }).click();
  const catalog = dialog.getByLabel("Available Blender versions");
  const release = catalog.locator(".catalog-version-row").filter({ hasText: "Blender 4.4" });
  await release.getByRole("button", { name: "Download", exact: true }).click();
  await release.getByRole("button", { name: "Install", exact: true }).click();
  const installed = dialog.locator(".version-list .version-row").filter({ hasText: "Blender 4.4" });
  await installed.waitFor();
  await installed.getByRole("button", { name: "Delete Blender 4.4", exact: true }).waitFor();
  return { dialog, installed };
};

const assertFit = async (page, dialog) => {
  const fit = await dialog.evaluate((element) => {
    const bounds = element.getBoundingClientRect();
    return {
      bottom: bounds.bottom,
      clientWidth: element.clientWidth,
      left: bounds.left,
      right: bounds.right,
      scrollWidth: element.scrollWidth,
      top: bounds.top,
    };
  });
  const viewport = page.viewportSize();
  assert(viewport && fit.left >= 0 && fit.top >= 0, "Version dialog starts outside the viewport");
  assert(viewport && fit.right <= viewport.width && fit.bottom <= viewport.height, "Version dialog exceeds the viewport");
  assert(fit.scrollWidth <= fit.clientWidth, "Version dialog has horizontal overflow");
};

await mkdir(outputDirectory, { recursive: true });
const browser = await chromium.launch({ headless: true });

try {
  const desktop = await preparePage(browser, { viewport: { width: 1366, height: 768 } });
  const desktopVersion = await openAndInstall(desktop.page);
  await assertFit(desktop.page, desktopVersion.dialog);
  await desktop.page.screenshot({
    path: path.join(outputDirectory, "version-delete-desktop.png"),
    scale: "css",
  });

  desktop.page.once("dialog", (confirmation) => confirmation.dismiss());
  await desktopVersion.installed.getByRole("button", { name: "Delete Blender 4.4", exact: true }).click();
  assert(await desktopVersion.installed.isVisible(), "Cancelling deletion removed the version");

  desktop.page.once("dialog", (confirmation) => confirmation.accept());
  await desktopVersion.installed.getByRole("button", { name: "Delete Blender 4.4", exact: true }).click();
  await desktopVersion.installed.waitFor({ state: "hidden" });
  assert((await desktopVersion.dialog.locator(".version-list .version-row").count()) === 2, "Deleted version remains installed");
  await desktopVersion.dialog.getByLabel("Available Blender versions")
    .locator(".catalog-version-row").filter({ hasText: "Blender 4.4" })
    .getByRole("button", { name: "Download", exact: true }).waitFor();
  assert(desktop.errors.length === 0, `Desktop browser errors: ${desktop.errors.join("; ")}`);

  const mobile = await preparePage(browser, {
    hasTouch: true,
    isMobile: true,
    viewport: { width: 390, height: 844 },
  });
  const mobileVersion = await openAndInstall(mobile.page);
  await assertFit(mobile.page, mobileVersion.dialog);
  await mobile.page.screenshot({
    path: path.join(outputDirectory, "version-delete-mobile.png"),
    scale: "css",
  });
  assert(mobile.errors.length === 0, `Mobile browser errors: ${mobile.errors.join("; ")}`);

  await desktop.context.close();
  await mobile.context.close();
  console.log("Version manager QA passed: bundled manifest, install, cancel/delete, catalog restore, and viewport fit.");
} finally {
  await browser.close();
}
