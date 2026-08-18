import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const targetUrl = process.env.TARGET_URL ?? "http://127.0.0.1:5173";
const outputDirectory = fileURLToPath(new URL("../test-results/", import.meta.url));
const version = "5.2.0";
const now = "2026-08-17T12:00:00Z";

const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

const jobs = [
  {
    id: "11111111-1111-4111-8111-111111111111",
    name: "Ready scene",
    source_filename: "ready.blend",
    status: "ready",
    progress: 0,
    current_frame: null,
    blender_version: version,
    engine: "CYCLES",
    device: "CPU",
    gpu_ids: [],
    frame_mode: "SINGLE",
    frame_start: 1,
    frame_end: null,
    created_at: now,
    error: null,
  },
  {
    id: "22222222-2222-4222-8222-222222222222",
    name: "Historical render",
    source_filename: "history.blend",
    status: "completed",
    progress: 1,
    current_frame: 1,
    blender_version: version,
    engine: "CYCLES",
    device: "CPU",
    gpu_ids: [],
    frame_mode: "SINGLE",
    frame_start: 1,
    frame_end: null,
    created_at: now,
    error: null,
  },
];

const json = (route, body, status = 200) => route.fulfill({
  body: JSON.stringify(body),
  contentType: "application/json",
  status,
});

const installApiRoutes = async (page) => {
  const state = { active: false, downloaded: false, installed: false, jobMutations: 0 };

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const apiPath = url.pathname;
    const method = request.method();

    if (apiPath === "/api/v1/jobs/page" && method === "GET") {
      return json(route, { items: jobs, page: 1, page_size: 10, total: jobs.length, pages: 1 });
    }
    if (apiPath === "/api/v1/devices" && method === "GET") return json(route, []);
    if (apiPath === "/api/v1/system/capabilities" && method === "GET") {
      return json(route, {
        runner: { available: true, mode: "local", message: "Ready to render" },
      });
    }
    if (apiPath === "/api/v1/system/metrics" && method === "GET") {
      return json(route, { collected_at: now, cpus: [], gpus: [], storages: [], websocket_clients: 0 });
    }
    if (apiPath === "/api/v1/blender/versions" && method === "GET") {
      return json(route, state.installed ? [{
        version,
        source: "official",
        state: "installed",
        supported: true,
        active: state.active,
        archive_filename: `blender-${version}-linux-x64.tar.xz`,
        expected_sha256: "a".repeat(64),
        verified_sha256: "a".repeat(64),
        operation_id: null,
        error: null,
        created_at: now,
        updated_at: now,
      }] : []);
    }
    if (apiPath === "/api/v1/blender/releases" && method === "GET") {
      return json(route, [{
        version,
        filename: `blender-${version}-linux-x64.tar.xz`,
        channel: "stable",
        supported: true,
        downloaded: state.downloaded,
        installed: state.installed,
        active: state.active,
        source: state.downloaded ? "official" : null,
      }]);
    }
    if (apiPath === `/api/v1/blender/releases/${version}/download` && method === "POST") {
      state.downloaded = true;
      return json(route, { operation_id: "33333333-3333-4333-8333-333333333333" }, 202);
    }
    if (apiPath === `/api/v1/blender/versions/${version}/install` && method === "POST") {
      state.installed = true;
      return json(route, { operation_id: "44444444-4444-4444-8444-444444444444" }, 202);
    }
    if (apiPath === `/api/v1/blender/versions/${version}/activate` && method === "POST") {
      state.active = true;
      return json(route, {
        version,
        source: "official",
        state: "installed",
        supported: true,
        active: true,
        archive_filename: `blender-${version}-linux-x64.tar.xz`,
        expected_sha256: "a".repeat(64),
        verified_sha256: "a".repeat(64),
        operation_id: null,
        error: null,
        created_at: now,
        updated_at: now,
      });
    }
    if (apiPath.startsWith("/api/v1/blender/operations/") && method === "GET") {
      return json(route, { state: "completed", progress: 1 });
    }
    if (apiPath.endsWith("/artifacts") && method === "GET") {
      return json(route, [{ kind: "blender_log", filename: "blender.log", size_bytes: 2048 }]);
    }
    if (apiPath.endsWith("/frames") && method === "GET") {
      return json(route, { items: [], page: 1, page_size: 50, total: 0, pages: 0 });
    }
    if (apiPath.endsWith("/logs/blender/tail") && method === "GET") {
      return json(route, { lines: ["Historical Blender log"] });
    }
    if (apiPath.startsWith("/api/v1/jobs/") || (apiPath === "/api/v1/jobs" && method !== "GET")) {
      state.jobMutations += 1;
      return json(route, { error: { code: "unexpected_job_mutation", message: "Unexpected job mutation" } }, 500);
    }
    return json(route, { error: { code: "not_found", message: `${method} ${apiPath}` } }, 404);
  });

  return state;
};

const assertNoHorizontalOverflow = async (page, locator, label) => {
  const fit = await locator.evaluate((element) => {
    const bounds = element.getBoundingClientRect();
    return {
      clientWidth: element.clientWidth,
      left: bounds.left,
      offenders: [...element.querySelectorAll("*")]
        .map((child) => {
          const childBounds = child.getBoundingClientRect();
          return {
            className: child.className?.toString().slice(0, 80) ?? "",
            right: Math.round(childBounds.right),
            tag: child.tagName,
            width: Math.round(childBounds.width),
          };
        })
        .filter((child) => child.right > document.documentElement.clientWidth + 1)
        .slice(0, 5),
      right: bounds.right,
      scrollWidth: element.scrollWidth,
    };
  });
  const viewport = page.viewportSize();
  assert(viewport && fit.left >= 0 && fit.right <= viewport.width, `${label} exceeds the viewport`);
  assert(
    fit.scrollWidth <= fit.clientWidth,
    `${label} has horizontal overflow (${fit.scrollWidth}px content in ${fit.clientWidth}px): ${JSON.stringify(fit.offenders)}`,
  );
};

const verifyEmptyState = async (page, state) => {
  await page.goto(targetUrl, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "No active Blender version" }).waitFor();

  await page.locator(".job-row").filter({ hasText: "Historical render" }).getByRole("button").first().click();
  await page.getByText("blender.log", { exact: true }).waitFor();
  assert(await page.getByRole("button", { name: "Rerender as new job" }).isDisabled(), "Rerender is enabled without an active runtime");

  await page.locator(".job-row").filter({ hasText: "Ready scene" }).getByRole("button").first().click();
  assert(await page.getByRole("button", { name: "Start render" }).isDisabled(), "Start is enabled without an active runtime");

  await page.getByRole("button", { name: "New job" }).click();
  await page.getByText("No active Blender version", { exact: true }).last().waitFor();
  assert(await page.locator('input[accept=".blend,.zip"]').isDisabled(), "Upload input is enabled without an active runtime");
  assert(await page.locator(".file-drop").isDisabled(), "Upload action is enabled without an active runtime");
  assert(state.jobMutations === 0, "The empty state issued a job mutation");
};

await mkdir(outputDirectory, { recursive: true });
const browser = await chromium.launch({ headless: true });

try {
  const desktopContext = await browser.newContext({ viewport: { width: 1366, height: 768 } });
  const desktop = await desktopContext.newPage();
  const desktopState = await installApiRoutes(desktop);
  await verifyEmptyState(desktop, desktopState);
  await assertNoHorizontalOverflow(desktop, desktop.locator(".app-shell"), "Desktop app");
  await desktop.screenshot({ path: path.join(outputDirectory, "blender-empty-desktop.png"), scale: "css" });

  await desktop.getByRole("button", { name: "Manage versions" }).click();
  const dialog = desktop.getByRole("dialog", { name: "Blender versions" });
  await dialog.getByText("No Blender versions installed", { exact: true }).waitFor();
  const release = dialog.locator(".catalog-version-row").filter({ hasText: `Blender ${version}` });
  await release.getByRole("button", { name: "Download", exact: true }).click();
  await release.getByRole("button", { name: "Install", exact: true }).click();
  const installed = dialog.locator(".version-list .version-row").filter({ hasText: `Blender ${version}` });
  await installed.getByRole("button", { name: "Make active", exact: true }).click();
  await installed.getByText("Active", { exact: true }).waitFor();
  await desktop.getByRole("button", { name: `Blender ${version}` }).waitFor();
  assert(desktopState.active, "Download/install/activate did not update API state");
  await dialog.getByRole("button", { name: "Close version manager" }).click();
  assert(await desktop.locator(".file-drop").isEnabled(), "Upload did not unlock after activation");

  await desktop.locator(".job-row").filter({ hasText: "Ready scene" }).getByRole("button").first().click();
  assert(await desktop.getByRole("button", { name: "Start render" }).isEnabled(), "Start did not unlock after activation");
  await desktop.locator(".job-row").filter({ hasText: "Historical render" }).getByRole("button").first().click();
  assert(await desktop.getByRole("button", { name: "Rerender as new job" }).isEnabled(), "Rerender did not unlock after activation");

  const mobileContext = await browser.newContext({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });
  const mobile = await mobileContext.newPage();
  const mobileState = await installApiRoutes(mobile);
  await verifyEmptyState(mobile, mobileState);
  await mobile.getByRole("button", { name: "Manage versions" }).click();
  const mobileDialog = mobile.getByRole("dialog", { name: "Blender versions" });
  await mobileDialog.getByText("No Blender versions installed", { exact: true }).waitFor();
  await mobile.waitForTimeout(200);
  await mobile.screenshot({ path: path.join(outputDirectory, "blender-empty-mobile.png"), scale: "css" });
  await assertNoHorizontalOverflow(mobile, mobile.locator(".app-shell"), "Mobile app");
  await assertNoHorizontalOverflow(mobile, mobileDialog, "Mobile version manager");

  await desktopContext.close();
  await mobileContext.close();
  console.log("Empty Blender registry QA passed: blocked render actions, history access, explicit activation, and viewport fit.");
} finally {
  await browser.close();
}
