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

const makeJob = ({ id, name, status, artifacts = false }) => ({
  id,
  name,
  source_filename: `${name.toLowerCase().replaceAll(" ", "-")}.blend`,
  status,
  blender_version: "4.1.1",
  engine: "BLENDER_EEVEE",
  device: "CPU",
  gpu_ids: [],
  frame_mode: "SINGLE",
  frame_start: 1,
  frame_end: null,
  current_frame: status === "completed" ? 1 : null,
  progress: status === "completed" ? 1 : status === "rendering" ? 0.4 : 0,
  process_pid: status === "rendering" ? 2468 : null,
  created_at: new Date().toISOString(),
  started_at: status === "rendering" ? new Date().toISOString() : null,
  finished_at: status === "completed" ? new Date().toISOString() : null,
  exit_code: status === "completed" ? 0 : null,
  error: null,
  artifacts,
});

const fixtureJobs = () => [
  makeJob({ id: "10000000-0000-4000-8000-000000000001", name: "Ready clean", status: "ready" }),
  makeJob({ id: "10000000-0000-4000-8000-000000000002", name: "Complete with files", status: "completed", artifacts: true }),
  makeJob({ id: "10000000-0000-4000-8000-000000000003", name: "Active render", status: "rendering" }),
  makeJob({ id: "10000000-0000-4000-8000-000000000004", name: "Failed clean", status: "failed" }),
];

const installRoutes = async (page) => {
  let jobs = fixtureJobs();
  const deleted = [];

  await page.route("**/api/v1/jobs/page?**", (route) => route.fulfill({
    contentType: "application/json",
    json: { items: jobs, page: 1, page_size: 10, total: jobs.length, pages: jobs.length ? 1 : 0 },
  }));
  await page.route(/\/api\/v1\/jobs\/[^/]+\/artifacts$/, (route) => {
    const jobId = route.request().url().split("/").at(-2);
    const job = jobs.find((candidate) => candidate.id === jobId);
    return route.fulfill({
      contentType: "application/json",
      json: job?.artifacts
        ? [{ id: "20000000-0000-4000-8000-000000000001", job_id: jobId, kind: "blender_log", filename: "blender.log", size_bytes: 1024 }]
        : [],
    });
  });
  await page.route(/\/api\/v1\/jobs\/[^/]+\/frames\?/, (route) => route.fulfill({
    contentType: "application/json",
    json: { items: [], page: 1, page_size: 1, total: 0, pages: 0 },
  }));
  await page.route(/\/api\/v1\/jobs\/[^/]+$/, (route) => {
    if (route.request().method() !== "DELETE") return route.fallback();
    const jobId = route.request().url().split("/").at(-1);
    const job = jobs.find((candidate) => candidate.id === jobId);
    if (job?.status === "queued" || job?.status === "rendering") {
      return route.fulfill({
        status: 409,
        contentType: "application/json",
        json: { error: { code: "active_job_cannot_be_deleted", message: "Active job" } },
      });
    }
    deleted.push(jobId);
    jobs = jobs.filter((candidate) => candidate.id !== jobId);
    return route.fulfill({ status: 204, body: "" });
  });

  return { deleted };
};

const rowFor = (page, name) => page.locator(".job-row-shell", { hasText: name });

const assertTranslated = async (row, expectedOpen, label) => {
  const transform = await row.locator(".job-row").evaluate((element) => getComputedStyle(element).transform);
  const translated = transform !== "none" && Math.abs(Number(transform.split(",")[4] ?? 0)) > 40;
  assert(translated === expectedOpen, `${label}: unexpected row transform ${transform}`);
};

const swipe = async (context, page, row, direction) => {
  await row.scrollIntoViewIfNeeded();
  const box = await row.boundingBox();
  assert(box, "Swipe row is not visible");
  const client = await context.newCDPSession(page);
  const startX = direction === "left" ? box.x + box.width - 28 : box.x + 28;
  const endX = direction === "left" ? startX - 90 : startX + 90;
  const y = box.y + box.height / 2;
  await client.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: startX, y }] });
  await client.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: endX, y }] });
  await client.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await client.detach();
};

const assertNoHorizontalOverflow = async (page, label) => {
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  assert(dimensions.scrollWidth <= dimensions.clientWidth, `${label} has horizontal overflow`);
};

try {
  await mkdir(outputDirectory, { recursive: true });

  const desktopContext = await browser.newContext({ viewport: { width: 1600, height: 900 } });
  const desktop = await desktopContext.newPage();
  const desktopState = await installRoutes(desktop);
  await desktop.goto(targetUrl, { waitUntil: "networkidle" });

  const ready = rowFor(desktop, "Ready clean");
  const completedBeforeDelete = rowFor(desktop, "Complete with files");
  await completedBeforeDelete.hover();
  const hoverBackground = await completedBeforeDelete.locator(".job-row").evaluate((element) => getComputedStyle(element).backgroundColor);
  const hoverAlpha = hoverBackground.match(/^rgba\(.+,\s*([\d.]+)\)$/)?.[1] ?? "1";
  assert(Number(hoverAlpha) === 1, `Closed hover background is transparent: ${hoverBackground}`);
  await desktop.screenshot({ path: path.join(outputDirectory, "job-delete-desktop-closed-hover.png"), scale: "css" });
  await ready.getByRole("button", { name: "Reveal delete action for Ready clean" }).click();
  await desktop.waitForTimeout(80);
  await desktop.screenshot({ path: path.join(outputDirectory, "job-delete-desktop-transition.png"), scale: "css" });
  await desktop.waitForTimeout(180);
  await assertTranslated(ready, true, "Desktop reveal");
  await ready.getByRole("button", { name: "Hide delete action for Ready clean" }).click();
  await desktop.waitForTimeout(220);
  await assertTranslated(ready, false, "Desktop close");

  const active = rowFor(desktop, "Active render");
  await active.getByRole("button", { name: "Reveal delete action for Active render" }).click();
  await desktop.waitForTimeout(220);
  assert(await active.getByRole("button", { name: "Cannot delete active job Active render" }).isDisabled(), "Active job delete is not disabled");
  assert(desktopState.deleted.length === 0, "Active job issued a DELETE request");

  await ready.getByRole("button", { name: "Reveal delete action for Ready clean" }).click();
  await desktop.waitForTimeout(220);
  await ready.getByRole("button", { name: "Delete job Ready clean" }).click();
  await ready.waitFor({ state: "detached" });
  assert(await desktop.getByRole("dialog").count() === 0, "Artifact-free job unexpectedly requested confirmation");
  assert(desktopState.deleted.includes("10000000-0000-4000-8000-000000000001"), "Artifact-free job was not deleted");
  const completed = rowFor(desktop, "Complete with files");
  assert(await completed.locator(".job-row").evaluate((element) => element.classList.contains("selected")), "Next job was not selected after deletion");

  await completed.getByRole("button", { name: "Reveal delete action for Complete with files" }).click();
  await desktop.waitForTimeout(220);
  await desktop.screenshot({ path: path.join(outputDirectory, "job-delete-desktop-revealed.png"), scale: "css" });
  await completed.getByRole("button", { name: "Delete job Complete with files" }).click();
  const dialog = desktop.getByRole("dialog", { name: /Delete “Complete with files”/ });
  await dialog.waitFor();
  assert(!desktopState.deleted.includes("10000000-0000-4000-8000-000000000002"), "Job with artifacts was deleted before confirmation");
  await desktop.waitForTimeout(220);
  await desktop.screenshot({ path: path.join(outputDirectory, "job-delete-desktop-confirm.png"), scale: "css" });
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
  await completed.getByRole("button", { name: "Reveal delete action for Complete with files" }).click();
  await desktop.waitForTimeout(220);
  await completed.getByRole("button", { name: "Delete job Complete with files" }).click();
  await desktop.getByRole("dialog").getByRole("button", { name: "Delete job" }).click();
  await completed.waitFor({ state: "detached" });
  await assertNoHorizontalOverflow(desktop, "Desktop delete flow");
  await desktopContext.close();

  const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const mobile = await mobileContext.newPage();
  const mobileState = await installRoutes(mobile);
  await mobile.goto(targetUrl, { waitUntil: "networkidle" });
  const mobileFailed = rowFor(mobile, "Failed clean");
  await swipe(mobileContext, mobile, mobileFailed, "left");
  await mobile.waitForTimeout(80);
  await mobile.screenshot({ path: path.join(outputDirectory, "job-delete-mobile-transition.png"), scale: "css" });
  await mobile.waitForTimeout(180);
  await assertTranslated(mobileFailed, true, "Mobile swipe reveal");
  await swipe(mobileContext, mobile, mobileFailed, "right");
  await mobile.waitForTimeout(220);
  await assertTranslated(mobileFailed, false, "Mobile swipe close");
  await mobileFailed.locator(".job-select-button").tap();
  assert(await mobileFailed.locator(".job-row").evaluate((element) => element.classList.contains("selected")), "Row could not be selected after a swipe cycle");
  await swipe(mobileContext, mobile, mobileFailed, "left");
  await mobile.waitForTimeout(220);
  await mobileFailed.getByRole("button", { name: "Delete job Failed clean" }).tap();
  await mobile.waitForTimeout(500);
  assert(
    mobileState.deleted.includes("10000000-0000-4000-8000-000000000004"),
    `Mobile delete did not reach the API; dialogs=${await mobile.getByRole("dialog").count()}`,
  );
  await mobileFailed.waitFor({ state: "detached" });

  const mobileCompleted = rowFor(mobile, "Complete with files");
  await swipe(mobileContext, mobile, mobileCompleted, "left");
  await mobile.waitForTimeout(220);
  await mobile.screenshot({ path: path.join(outputDirectory, "job-delete-mobile-revealed.png"), scale: "css" });
  await mobileCompleted.getByRole("button", { name: "Delete job Complete with files" }).tap();
  await mobile.getByRole("dialog", { name: /Delete “Complete with files”/ }).waitFor();
  await mobile.waitForTimeout(220);
  await mobile.screenshot({ path: path.join(outputDirectory, "job-delete-mobile-confirm.png"), scale: "css" });
  await assertNoHorizontalOverflow(mobile, "Mobile delete flow");
  await mobileContext.close();

  console.log("Job delete QA passed: desktop reveal, touch swipe, active lock, conditional confirmation, full delete flow, next selection, and responsive fit.");
} finally {
  await browser.close();
}
