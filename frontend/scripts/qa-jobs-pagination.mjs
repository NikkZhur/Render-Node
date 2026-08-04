import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const targetUrl = process.env.TARGET_URL ?? "http://127.0.0.1:5173";
const outputDirectory = fileURLToPath(new URL("../test-results/", import.meta.url));
const browser = await chromium.launch({ headless: true });
const requestedPages = [];

const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

const jobs = Array.from({ length: 23 }, (_, index) => ({
  id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
  name: `Paginated job ${String(index + 1).padStart(2, "0")}`,
  source_filename: `scene-${index + 1}.blend`,
  status: index % 4 === 0 ? "completed" : "ready",
  blender_version: "4.1.1",
  engine: "BLENDER_EEVEE",
  device: "CPU",
  gpu_ids: [],
  frame_mode: "SINGLE",
  frame_start: 1,
  frame_end: null,
  current_frame: index % 4 === 0 ? 1 : null,
  progress: index % 4 === 0 ? 1 : 0,
  process_pid: null,
  created_at: new Date(Date.now() - index * 60_000).toISOString(),
  started_at: null,
  finished_at: null,
  exit_code: index % 4 === 0 ? 0 : null,
  error: null,
}));

const routeJobPages = (page) => page.route("**/api/v1/jobs/page?**", (route) => {
  const url = new URL(route.request().url());
  const requestedPage = Number(url.searchParams.get("page") ?? 1);
  const pageSize = Number(url.searchParams.get("page_size") ?? 10);
  requestedPages.push(requestedPage);
  const start = (requestedPage - 1) * pageSize;
  return route.fulfill({
    contentType: "application/json",
    json: {
      items: jobs.slice(start, start + pageSize),
      page: requestedPage,
      page_size: pageSize,
      total: jobs.length,
      pages: Math.ceil(jobs.length / pageSize),
    },
  });
});

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
  await routeJobPages(desktop);
  await desktop.goto(targetUrl, { waitUntil: "networkidle" });
  const queue = desktop.locator(".queue-panel");
  const rows = queue.locator(".job-row");
  await queue.getByText("1 / 3", { exact: true }).waitFor();
  assert(await rows.count() === 10, "First jobs page did not contain exactly 10 rows");
  assert(await queue.locator(".queue-count").textContent() === "23", "Jobs total is not shown");
  const initialPanel = await queue.boundingBox();
  const listOverflow = await queue.locator(".job-list").evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }));
  assert(listOverflow.scrollHeight > listOverflow.clientHeight, "Jobs list has no internal scroll");
  assert(!requestedPages.includes(2) && !requestedPages.includes(3), "Later jobs pages were prefetched");

  await queue.getByRole("button", { name: "Next jobs page" }).click();
  await rows.first().getByText("Paginated job 11", { exact: true }).waitFor();
  assert(await rows.count() === 10, "Second jobs page did not contain exactly 10 rows");
  assert(requestedPages.includes(2), "Second jobs page was not loaded on demand");
  assert(!requestedPages.includes(3), "Third jobs page was loaded before navigation");
  const secondPanel = await queue.boundingBox();
  assert(initialPanel && secondPanel && initialPanel.height === secondPanel.height, "Jobs panel changed height after pagination");

  await queue.getByRole("button", { name: "Next jobs page" }).click();
  await rows.first().getByText("Paginated job 21", { exact: true }).waitFor();
  assert(await rows.count() === 3, "Last jobs page did not contain the remaining 3 rows");
  assert(requestedPages.includes(3), "Third jobs page was not loaded on demand");
  await queue.getByRole("button", { name: "Previous jobs page" }).click();
  await rows.first().getByText("Paginated job 11", { exact: true }).waitFor();
  await queue.getByRole("button", { name: "Previous jobs page" }).click();
  await rows.first().getByText("Paginated job 01", { exact: true }).waitFor();
  await assertNoHorizontalOverflow(desktop, "Desktop jobs pagination");
  await desktop.screenshot({ path: path.join(outputDirectory, "jobs-pagination-desktop.png"), scale: "css" });
  await desktopContext.close();

  const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const mobile = await mobileContext.newPage();
  await routeJobPages(mobile);
  await mobile.goto(targetUrl, { waitUntil: "networkidle" });
  const mobileQueue = mobile.locator(".queue-panel");
  await mobileQueue.getByText("1 / 3", { exact: true }).waitFor();
  assert(await mobileQueue.locator(".job-row").count() === 10, "Mobile jobs page exceeded 10 rows");
  const mobileListOverflow = await mobileQueue.locator(".job-list").evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }));
  assert(mobileListOverflow.scrollHeight > mobileListOverflow.clientHeight, "Mobile jobs list has no internal scroll");
  await assertNoHorizontalOverflow(mobile, "Mobile jobs pagination");
  await mobileQueue.screenshot({ path: path.join(outputDirectory, "jobs-pagination-mobile.png"), scale: "css" });
  await mobileContext.close();

  console.log("Jobs pagination QA passed: 10-row server pages, on-demand navigation, fixed panel height, internal scroll, and responsive fit.");
} finally {
  await browser.close();
}
