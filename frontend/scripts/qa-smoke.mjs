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

try {
  await mkdir(outputDirectory, { recursive: true });

  const desktopContext = await browser.newContext({
    viewport: { width: 1600, height: 900 },
  });
  const desktop = await desktopContext.newPage();
  watchErrors(desktop);
  await desktop.goto(targetUrl, { waitUntil: "networkidle" });
  await desktop.evaluate(() => window.localStorage.setItem("render-node-theme", "dark"));
  await desktop.reload({ waitUntil: "networkidle" });

  assert(await desktop.getByRole("heading", { name: "Job setup" }).isVisible(), "Desktop job setup is not visible");
  assert(await desktop.getByText("Blender 4.5.11", { exact: true }).first().isVisible(), "Active Blender version is not visible");
  const headerSummary = desktop.getByRole("navigation", { name: "Node summary" });
  assert(await headerSummary.isVisible(), "Desktop node summary is not visible in the header");
  assert(await headerSummary.getByText("0 queued", { exact: true }).isVisible(), "Queued job count is missing from the header");
  assert(await headerSummary.getByText("2 GPUs ready", { exact: true }).isVisible(), "Ready GPU count is missing from the header");
  assert(await headerSummary.getByText("12m avg. frame", { exact: true }).isVisible(), "Average frame time is missing from the header");
  const headerControlHeights = await desktop.locator(".app-header").evaluate((header) =>
    [...header.querySelectorAll(".summary-pill, .connection-pill, .version-button, .icon-button, .theme-switch")].map(
      (element) => element.getBoundingClientRect().height,
    ),
  );
  assert(Math.max(...headerControlHeights) - Math.min(...headerControlHeights) <= 1, "Header controls do not share a consistent height");
  const headerSpacing = await desktop.locator(".header-controls").evaluate((controls) => {
    const controlsBounds = controls.getBoundingClientRect();
    const itemBounds = [
      ...controls.querySelectorAll(
        ".summary-pill, .header-actions > .connection-pill, .header-actions > .version-button, .header-actions > .icon-button, .header-actions > .theme-switch",
      ),
    ].map((element) => element.getBoundingClientRect());
    return {
      borderWidth: Number.parseFloat(getComputedStyle(controls).borderTopWidth),
      gaps: itemBounds.slice(1).map((bounds, index) => bounds.left - itemBounds[index].right),
      leftInset: itemBounds[0].left - controlsBounds.left,
      rightInset: controlsBounds.right - itemBounds.at(-1).right,
    };
  });
  assert(headerSpacing.borderWidth > 0, "Header controls are not enclosed in one block");
  assert(Math.max(...headerSpacing.gaps) - Math.min(...headerSpacing.gaps) <= 1, "Header controls do not use equal horizontal gaps");
  assert(Math.abs(headerSpacing.leftInset - headerSpacing.rightInset) <= 1, "Header block has uneven horizontal padding");
  const themeSwitch = desktop.getByRole("group", { name: "Color theme" });
  assert(await themeSwitch.isVisible(), "Theme switch is not visible after Settings");
  assert(await desktop.getByRole("button", { name: "Dark theme" }).getAttribute("aria-pressed") === "true", "Dark theme is not initially active");
  const darkBackground = await desktop.locator("body").evaluate((body) => getComputedStyle(body).backgroundColor);
  await desktop.getByRole("button", { name: "Light theme" }).click();
  assert(await desktop.locator("html[data-theme='light']").count() === 1, "Light theme did not activate");
  assert(await desktop.getByRole("button", { name: "Light theme" }).getAttribute("aria-pressed") === "true", "Light theme control has no active state");
  const lightBackground = await desktop.locator("body").evaluate((body) => getComputedStyle(body).backgroundColor);
  assert(lightBackground !== darkBackground, "Theme switch does not change the page palette");
  const previewEmptyContrast = await desktop.locator(".preview-empty strong").evaluate((element) => ({
    foreground: getComputedStyle(element).color,
    viewport: getComputedStyle(element.closest(".render-viewport")).backgroundColor,
  }));
  assert(previewEmptyContrast.foreground !== previewEmptyContrast.viewport, "Light theme makes the dark preview message unreadable");
  await desktop.waitForTimeout(180);
  await desktop.screenshot({ path: path.join(outputDirectory, "desktop-light.png"), scale: "css" });
  await desktop.reload({ waitUntil: "networkidle" });
  assert(await desktop.locator("html[data-theme='light']").count() === 1, "Selected theme was not restored after reload");
  await desktop.getByRole("button", { name: "Dark theme" }).click();
  assert(await desktop.locator("html[data-theme='dark']").count() === 1, "Dark theme did not reactivate");

  for (const passiveHeaderItem of [desktop.locator(".summary-queue"), desktop.locator(".connection-pill")]) {
    const backgroundBeforeHover = await passiveHeaderItem.evaluate((element) => getComputedStyle(element).background);
    await passiveHeaderItem.hover();
    const backgroundAfterHover = await passiveHeaderItem.evaluate((element) => getComputedStyle(element).background);
    assert(backgroundAfterHover === backgroundBeforeHover, "A non-interactive header item has a hover highlight");
  }

  const engineDropdown = desktop.getByRole("button", { name: "Render engine: Cycles" });
  assert(await engineDropdown.evaluate((element) => getComputedStyle(element).borderTopWidth === "0px"), "Render engine dropdown still has a border");
  await engineDropdown.click();
  const engineMenu = desktop.getByRole("listbox", { name: "Render engine" });
  assert(await engineMenu.isVisible(), "Render engine dropdown did not open");
  const dropdownMotion = await engineMenu.evaluate((element) => ({
    backdropFilter: getComputedStyle(element).backdropFilter,
    transitionProperty: getComputedStyle(element).transitionProperty,
  }));
  assert(dropdownMotion.backdropFilter === "none", "Dropdown uses an expensive backdrop filter");
  assert(dropdownMotion.transitionProperty.includes("opacity") && dropdownMotion.transitionProperty.includes("transform"), "Dropdown is missing compositor-friendly motion");
  await engineMenu.getByRole("option", { name: "Eevee" }).click();
  assert(await desktop.getByRole("button", { name: "Render engine: Eevee" }).isVisible(), "Render engine selection did not update");

  const deviceDropdown = desktop.getByRole("button", { name: "Compute device: OptiX" });
  assert(await deviceDropdown.evaluate((element) => getComputedStyle(element).borderTopWidth === "0px"), "Compute device dropdown still has a border");
  await deviceDropdown.click();
  await desktop.getByRole("listbox", { name: "Compute device" }).getByRole("option", { name: "CUDA" }).click();
  assert(await desktop.getByRole("button", { name: "Compute device: CUDA" }).isVisible(), "Compute device selection did not update");

  const frameMode = desktop.getByRole("group", { name: "Frame mode" });
  const rangeIndicatorTransform = await frameMode.evaluate((element) => getComputedStyle(element, "::before").transform);
  await frameMode.getByRole("button", { name: "Single" }).click();
  await desktop.waitForTimeout(200);
  const singleIndicatorTransform = await frameMode.evaluate((element) => getComputedStyle(element, "::before").transform);
  assert(singleIndicatorTransform !== rangeIndicatorTransform, "Frame mode indicator did not move smoothly");
  assert(await frameMode.getByRole("button", { name: "Single" }).getAttribute("aria-pressed") === "true", "Single frame mode is not selected");
  await frameMode.getByRole("button", { name: "Range" }).click();

  const gpuOptions = desktop.locator(".gpu-option");
  assert((await gpuOptions.count()) > 0, "No GPU options are visible");
  assert((await desktop.locator(".gpu-option.selected").count()) === (await gpuOptions.count()), "Not all available GPUs are selected by default");

  const dashboardBox = await desktop.locator(".dashboard-grid").boundingBox();
  const headerControlsBox = await desktop.locator(".header-controls").boundingBox();
  const metricsBox = await desktop.locator(".metrics-strip").boundingBox();
  assert(
    dashboardBox && headerControlsBox && Math.abs(headerControlsBox.x + headerControlsBox.width - dashboardBox.x - dashboardBox.width) <= 1,
    "Desktop header controls and dashboard do not share the same right edge",
  );
  assert(dashboardBox && metricsBox && metricsBox.width >= dashboardBox.width - 2, "Desktop metrics do not span the dashboard width");
  assert((await desktop.locator(".resource-row").count()) === 3, "Metrics do not render one row per CPU and GPU");
  const metricComposition = await desktop.locator(".metric-item").first().evaluate((card) => {
    const ring = card.querySelector(".metric-ring").getBoundingClientRect();
    const chart = card.querySelector(".metric-chart").getBoundingClientRect();
    const stops = [...card.querySelectorAll(".metric-chart stop")].map((stop) => Number.parseFloat(stop.getAttribute("stop-opacity")));
    return {
      chartWidth: chart.width,
      ringText: card.querySelector(".metric-ring-content").textContent,
      ringWidth: ring.width,
      stops,
    };
  });
  assert(metricComposition.ringWidth > 0 && metricComposition.ringText.includes("Load") && metricComposition.ringText.includes("72%"), "Metric label and value are not inside the ring");
  assert(metricComposition.chartWidth > metricComposition.ringWidth, "Metric line chart does not use the available side space");
  assert(metricComposition.stops[0] > 0 && metricComposition.stops.at(-1) === 0, "Metric chart fill does not fade through alpha");
  assert((await desktop.locator(".metric-chart").count()) === 9, "Not every hardware metric has a line chart");

  await desktop.getByRole("button", { name: "Open frame sequence, 240 frames" }).click();
  const desktopFramesModal = desktop.getByRole("dialog", { name: "Frames 1–240" });
  assert(await desktopFramesModal.isVisible(), "Frame sequence popup did not open on desktop");
  await desktopFramesModal.getByText("frame_0050.png", { exact: true }).waitFor();
  assert((await desktopFramesModal.locator(".frame-row").count()) === 50, "The first frame page does not contain 50 frames");
  assert(await desktopFramesModal.getByText("240 PNG files · 5 pages", { exact: true }).isVisible(), "Frame sequence page count is incorrect");
  assert(await desktopFramesModal.getByText("1–50 of 240", { exact: true }).isVisible(), "The first frame range is incorrect");
  assert((await desktopFramesModal.locator("img").count()) === 0, "Frame popup unexpectedly loads previews");
  assert(await desktopFramesModal.getByRole("button", { name: "Download all frames as ZIP" }).isVisible(), "Sequence ZIP action is not visible");
  const desktopFrameList = desktopFramesModal.locator(".frame-list");
  const desktopFrameListFit = await desktopFrameList.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }));
  assert(desktopFrameListFit.scrollHeight > desktopFrameListFit.clientHeight, "Long frame sequence does not scroll inside the popup");
  await desktopFramesModal.getByRole("button", { name: "Next" }).click();
  await desktopFramesModal.getByText("frame_0051.png", { exact: true }).waitFor();
  assert((await desktopFramesModal.locator(".frame-row").count()) === 50, "The second frame page does not contain 50 frames");
  for (const firstFrame of [101, 151, 201]) {
    await desktopFramesModal.getByRole("button", { name: "Next" }).click();
    await desktopFramesModal.getByText(`frame_${String(firstFrame).padStart(4, "0")}.png`, { exact: true }).waitFor();
  }
  assert((await desktopFramesModal.locator(".frame-row").count()) === 40, "The final frame page does not contain the remaining 40 frames");
  assert(await desktopFramesModal.getByText("201–240 of 240", { exact: true }).isVisible(), "The final frame range is incorrect");
  await desktopFramesModal.locator(".frame-row").last().scrollIntoViewIfNeeded();
  assert(await desktopFramesModal.getByText("frame_0240.png", { exact: true }).isVisible(), "The final frame is missing from page five");
  for (const firstFrame of [151, 101, 51, 1]) {
    await desktopFramesModal.getByRole("button", { name: "Previous" }).click();
    await desktopFramesModal.getByText(`frame_${String(firstFrame).padStart(4, "0")}.png`, { exact: true }).waitFor();
  }
  assert(await desktopFramesModal.getByText("frame_0001.png", { exact: true }).isVisible(), "Returning to the first frame page failed");
  await desktop.screenshot({ path: path.join(outputDirectory, "desktop-frames.png"), scale: "css" });
  await desktop.keyboard.press("Escape");
  await desktopFramesModal.waitFor({ state: "hidden" });

  await desktop.getByRole("button", { name: /Blender 4\.5\.11/ }).click();
  assert(await desktop.getByRole("dialog", { name: "Blender versions" }).isVisible(), "Version manager did not open");
  assert((await desktop.locator(".version-row").count()) === 6, "Version manager has an unexpected number of rows");
  const blender52 = desktop.locator(".version-row").filter({ hasText: "Blender 5.2.0" });
  await blender52.getByRole("button", { name: "Make active" }).click();
  await blender52.getByText("Active", { exact: true }).waitFor();
  await desktop.getByRole("button", { name: "Close version manager" }).click();

  await desktop.getByRole("button", { name: /Start render/ }).click();
  await desktop.getByText("Rendering", { exact: true }).first().waitFor();
  assert(await desktop.getByRole("button", { name: /Cancel render/ }).isVisible(), "Cancel action is not available during render");
  assert(await headerSummary.getByText("1 queued", { exact: true }).isVisible(), "Queued job count does not update after starting a render");

  assert((await desktop.getByRole("tab", { name: /Live log/ }).count()) === 0, "Live log tab was not removed");
  assert((await desktop.getByRole("button", { name: "More options" }).count()) === 0, "Preview overflow placeholder was not removed");
  assert(await desktop.getByRole("log", { name: "Blender live log" }).isVisible(), "Live log overlay is not visible on the rendered frame");
  assert((await desktop.getByRole("button", { name: /Download frame/ }).count()) === 0, "Incomplete frame exposes a download action");
  const frameChipBox = await desktop.locator(".frame-chip").boundingBox();
  const frameActionsBox = await desktop.locator(".preview-frame-actions").boundingBox();
  assert(frameChipBox && frameActionsBox && frameChipBox.x < frameActionsBox.x, "Frame number is not positioned to the left of preview actions");

  await desktop.getByRole("button", { name: /Open frame .* in full resolution/ }).click();
  const fullFrameDialog = desktop.getByRole("dialog", { name: /Frame .* full resolution/ });
  assert(await fullFrameDialog.isVisible(), "Full resolution frame dialog did not open");
  const fullFrameBox = await fullFrameDialog.boundingBox();
  assert(
    fullFrameBox && fullFrameBox.width >= 1600 * 0.65 && fullFrameBox.width <= 1600 * 0.72
      && fullFrameBox.height >= 900 * 0.65 && fullFrameBox.height <= 900 * 0.72,
    "Full resolution frame dialog does not use approximately 70% of the desktop viewport",
  );
  await fullFrameDialog.getByRole("button", { name: "Close full resolution frame" }).click();
  await fullFrameDialog.waitFor({ state: "hidden" });

  await desktop.locator(".job-row").filter({ hasText: "Loft still" }).click();
  assert(await desktop.getByRole("button", { name: /Download frame/ }).isVisible(), "Completed frame has no download action");
  await desktop.screenshot({ path: path.join(outputDirectory, "desktop-rendering.png"), scale: "css" });

  const desktopFit = await desktop.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  assert(desktopFit.scrollWidth <= desktopFit.width, "Desktop page has horizontal overflow");
  await desktop.getByRole("button", { name: /Cancel render/ }).click();
  await desktopContext.close();

  const compactContext = await browser.newContext({
    viewport: { width: 1000, height: 800 },
  });
  const compact = await compactContext.newPage();
  watchErrors(compact);
  await compact.goto(targetUrl, { waitUntil: "networkidle" });
  const compactFit = await compact.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    metricClientWidth: document.querySelector(".metrics-scroll").clientWidth,
    metricScrollWidth: document.querySelector(".metrics-scroll").scrollWidth,
    resourceRows: document.querySelectorAll(".resource-row").length,
    rootFont: Number.parseFloat(getComputedStyle(document.documentElement).fontSize),
    controlFont: Number.parseFloat(getComputedStyle(document.querySelector(".primary-action")).fontSize),
  }));
  assert(compactFit.scrollWidth <= compactFit.width, "Compact desktop page has horizontal overflow");
  assert(compactFit.metricScrollWidth <= compactFit.metricClientWidth, "Compact metrics have horizontal overflow");
  assert(compactFit.resourceRows === 3, "Compact metrics lost CPU or GPU rows");
  assert(await compact.locator(".header-summary").isHidden(), "Compact header summary should collapse before it causes overflow");
  const compactJobsBox = await compact.locator(".queue-panel").boundingBox();
  const compactArtifactsBox = await compact.locator(".artifacts-panel").boundingBox();
  assert(
    compactJobsBox
      && compactArtifactsBox
      && Math.abs(compactJobsBox.y - compactArtifactsBox.y) <= 1
      && Math.abs(compactJobsBox.height - compactArtifactsBox.height) <= 1,
    "Compact Artifacts panel does not stretch to match Jobs",
  );
  await compact.screenshot({ path: path.join(outputDirectory, "desktop-compact.png"), fullPage: true, scale: "css" });
  await compactContext.close();

  const ultrawideContext = await browser.newContext({
    viewport: { width: 3440, height: 1440 },
  });
  const ultrawide = await ultrawideContext.newPage();
  watchErrors(ultrawide);
  await ultrawide.goto(targetUrl, { waitUntil: "networkidle" });
  assert(await ultrawide.getByRole("navigation", { name: "Node summary" }).isVisible(), "Ultrawide node summary is not visible");
  const ultrawideLayout = await ultrawide.evaluate(() => {
    const header = document.querySelector(".app-header").getBoundingClientRect();
    const headerSummary = document.querySelector(".header-summary").getBoundingClientRect();
    const brand = document.querySelector(".brand-block").getBoundingClientRect();
    const headerControls = document.querySelector(".header-controls").getBoundingClientRect();
    const headerActions = document.querySelector(".header-actions").getBoundingClientRect();
    const themeSwitch = document.querySelector(".theme-switch").getBoundingClientRect();
    const main = document.querySelector("main").getBoundingClientRect();
    const dashboard = document.querySelector(".dashboard-grid").getBoundingClientRect();
    const preview = document.querySelector(".render-viewport").getBoundingClientRect();
    const previewPanel = document.querySelector(".preview-panel").getBoundingClientRect();
    const setupPanel = document.querySelector(".setup-panel").getBoundingClientRect();
    const rightRail = document.querySelector(".right-rail").getBoundingClientRect();
    const queuePanel = document.querySelector(".queue-panel").getBoundingClientRect();
    const artifactsPanel = document.querySelector(".artifacts-panel").getBoundingClientRect();
    const metrics = document.querySelector(".metrics-strip").getBoundingClientRect();
    const metricsScroll = document.querySelector(".metrics-scroll");
    const metricsScrollBounds = metricsScroll.getBoundingClientRect();
    const metricRowBounds = [...document.querySelectorAll(".resource-row")].map((row) => row.getBoundingClientRect());
    const footer = document.querySelector(".app-footer").getBoundingClientRect();
    return {
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      headerOrder: brand.left < headerSummary.left && headerSummary.left < headerActions.left,
      headerRightGap: header.right - headerControls.right,
      headerDashboardRightDelta: Math.abs(headerControls.right - dashboard.right),
      themeIsLast: Math.abs(themeSwitch.right - headerActions.right) <= 1,
      pageWidth: document.documentElement.scrollWidth,
      pageHeight: document.documentElement.scrollHeight,
      mainWidth: main.width,
      dashboardWidth: dashboard.width,
      previewHeight: preview.height,
      previewPanelBottom: previewPanel.bottom,
      previewPanelHeight: previewPanel.height,
      setupPanelBottom: setupPanel.bottom,
      setupPanelHeight: setupPanel.height,
      rightRailBottom: rightRail.bottom,
      queuePanelHeight: queuePanel.height,
      artifactsPanelBottom: artifactsPanel.bottom,
      artifactsPanelHeight: artifactsPanel.height,
      metricsTop: metrics.top,
      metricsHeight: metrics.height,
      metricsBottom: metrics.bottom,
      metricsClientHeight: metricsScroll.clientHeight,
      metricsScrollHeight: metricsScroll.scrollHeight,
      metricsOverflowY: getComputedStyle(metricsScroll).overflowY,
      visibleMetricRows: metricRowBounds.filter((row) => row.top < metricsScrollBounds.bottom - 1 && row.bottom > metricsScrollBounds.top + 1).length,
      footerTop: footer.top,
      footerBottom: footer.bottom,
      resourceRows: document.querySelectorAll(".resource-row").length,
      rootFont: Number.parseFloat(getComputedStyle(document.documentElement).fontSize),
      controlFont: Number.parseFloat(getComputedStyle(document.querySelector(".primary-action")).fontSize),
    };
  });
  assert(ultrawideLayout.pageWidth <= ultrawideLayout.viewportWidth, "Ultrawide page has horizontal overflow");
  assert(ultrawideLayout.headerOrder, "Header controls have an unexpected order");
  assert(ultrawideLayout.headerRightGap <= 49, "Header controls are not grouped on the right on an ultrawide screen");
  assert(ultrawideLayout.headerDashboardRightDelta <= 1, "Header controls and dashboard do not share the same right edge");
  assert(ultrawideLayout.themeIsLast, "Theme switch is not positioned after Settings");
  assert(ultrawideLayout.mainWidth >= ultrawideLayout.viewportWidth * 0.95, "Ultrawide main content does not use the available width");
  assert(ultrawideLayout.dashboardWidth >= ultrawideLayout.viewportWidth * 0.95, "Ultrawide dashboard does not stretch with the screen");
  assert(ultrawideLayout.previewPanelHeight >= 800 && ultrawideLayout.previewHeight >= 650, "Ultrawide preview did not grow with the screen");
  assert(ultrawideLayout.metricsHeight >= 220 && ultrawideLayout.metricsHeight <= 261, "Ultrawide metrics do not respect the compact height limit");
  assert(Math.abs(ultrawideLayout.metricsTop - ultrawideLayout.setupPanelBottom - 14) <= 1, "Job setup does not stretch to the metrics block");
  assert(Math.abs(ultrawideLayout.metricsTop - ultrawideLayout.previewPanelBottom - 14) <= 1, "Preview does not stretch to the metrics block");
  assert(Math.abs(ultrawideLayout.metricsTop - ultrawideLayout.rightRailBottom - 14) <= 1, "Right rail does not stretch to the metrics block");
  assert(Math.abs(ultrawideLayout.artifactsPanelBottom - ultrawideLayout.rightRailBottom) <= 1, "Artifacts are not anchored above the metrics block");
  assert(ultrawideLayout.artifactsPanelHeight >= 210 && ultrawideLayout.artifactsPanelHeight <= 231, "Artifacts panel does not respect its fixed height");
  assert(ultrawideLayout.queuePanelHeight > ultrawideLayout.artifactsPanelHeight, "Jobs panel does not receive the remaining right-rail height");
  assert(Math.abs(ultrawideLayout.setupPanelHeight - ultrawideLayout.previewPanelHeight) <= 1, "Top desktop panels do not share the available height");
  assert(ultrawideLayout.footerTop - ultrawideLayout.metricsBottom <= 26, "Metrics leave unused space above the footer");
  assert(Math.abs(ultrawideLayout.footerBottom - ultrawideLayout.pageHeight) <= 2, "Status labels are not anchored in the page footer");
  assert(ultrawideLayout.resourceRows === 3, "Ultrawide metrics lost CPU or GPU rows");
  assert(ultrawideLayout.metricsOverflowY === "auto", "Hardware rows are not configured for internal scrolling");
  assert(ultrawideLayout.visibleMetricRows === 2, "Metrics panel does not limit the viewport to two resource rows");
  assert(ultrawideLayout.metricsScrollHeight > ultrawideLayout.metricsClientHeight, "The third hardware row does not create internal scrolling");
  assert(Math.abs(ultrawideLayout.rootFont - compactFit.rootFont) < 0.1, "Root typography overrides the browser base size");
  assert(ultrawideLayout.controlFont > compactFit.controlFont, "Control typography does not scale with the viewport");
  await ultrawide.screenshot({ path: path.join(outputDirectory, "desktop-ultrawide.png"), fullPage: true, scale: "css" });
  const ultrawideMetricsScroll = ultrawide.locator(".metrics-scroll");
  await ultrawideMetricsScroll.hover();
  await ultrawide.mouse.wheel(0, 600);
  await ultrawide.waitForTimeout(150);
  assert((await ultrawideMetricsScroll.evaluate((element) => element.scrollTop)) > 0, "Metrics panel does not scroll to additional hardware rows");
  const cpuRowInView = await ultrawide.locator(".resource-row").filter({ hasText: "CPU 01" }).evaluate((row) => {
    const rowBounds = row.getBoundingClientRect();
    const scrollBounds = row.closest(".metrics-scroll").getBoundingClientRect();
    return rowBounds.top < scrollBounds.bottom && rowBounds.bottom > scrollBounds.top;
  });
  assert(cpuRowInView, "The third hardware row is not reachable by scrolling");
  await ultrawide.screenshot({ path: path.join(outputDirectory, "desktop-ultrawide-metrics-scrolled.png"), fullPage: true, scale: "css" });
  await ultrawideContext.close();

  const mobileContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  const mobile = await mobileContext.newPage();
  watchErrors(mobile);
  await mobile.goto(targetUrl, { waitUntil: "networkidle" });
  assert(await mobile.getByRole("heading", { name: "Job setup" }).isVisible(), "Mobile job setup is not visible");
  assert(await mobile.locator("html[data-theme='dark']").count() === 1, "A fresh session does not default to dark theme");
  assert(await mobile.locator(".header-summary").isHidden(), "Mobile header summary should be hidden");
  assert(await mobile.getByRole("group", { name: "Color theme" }).isVisible(), "Mobile theme switch is not visible");

  await mobile.getByRole("button", { name: /Start render/ }).tap();
  await mobile.getByRole("button", { name: /Open frame .* in full resolution/ }).tap();
  const mobileFullFrameDialog = mobile.getByRole("dialog", { name: /Frame .* full resolution/ });
  assert(await mobileFullFrameDialog.isVisible(), "Full resolution frame dialog did not open on mobile");
  const mobileFullFrameBox = await mobileFullFrameDialog.boundingBox();
  assert(
    mobileFullFrameBox && mobileFullFrameBox.x >= 0 && mobileFullFrameBox.x + mobileFullFrameBox.width <= 390
      && mobileFullFrameBox.width >= 360,
    "Mobile full resolution frame dialog is not adaptively fitted to the viewport",
  );
  await mobileFullFrameDialog.getByRole("button", { name: "Close full resolution frame" }).tap();
  await mobile.getByRole("button", { name: /Cancel render/ }).tap();

  await mobile.getByRole("button", { name: "Open frame sequence, 240 frames" }).tap();
  const mobileFramesModal = mobile.getByRole("dialog", { name: "Frames 1–240" });
  assert(await mobileFramesModal.isVisible(), "Frame sequence popup did not open after a mobile tap");
  await mobileFramesModal.getByText("frame_0050.png", { exact: true }).waitFor();
  assert((await mobileFramesModal.locator(".frame-row").count()) === 50, "Mobile frame page does not contain 50 frames");
  await mobile.waitForTimeout(220);
  const mobileFramesBox = await mobileFramesModal.boundingBox();
  assert(
    mobileFramesBox && mobileFramesBox.x >= 0 && mobileFramesBox.y >= 0 && mobileFramesBox.x + mobileFramesBox.width <= 391 && mobileFramesBox.y + mobileFramesBox.height <= 845,
    "Mobile frame sequence popup extends beyond the viewport",
  );
  await mobileFramesModal.getByRole("button", { name: "Next" }).tap();
  await mobileFramesModal.getByText("frame_0051.png", { exact: true }).waitFor();
  assert((await mobileFramesModal.locator(".frame-row").count()) === 50, "Mobile second frame page does not contain 50 frames");
  for (const firstFrame of [101, 151, 201]) {
    await mobileFramesModal.getByRole("button", { name: "Next" }).tap();
    await mobileFramesModal.getByText(`frame_${String(firstFrame).padStart(4, "0")}.png`, { exact: true }).waitFor();
  }
  assert((await mobileFramesModal.locator(".frame-row").count()) === 40, "Mobile final frame page does not contain the remaining 40 frames");
  await mobileFramesModal.locator(".frame-row").last().scrollIntoViewIfNeeded();
  assert(await mobileFramesModal.getByText("frame_0240.png", { exact: true }).isVisible(), "The final paged frame is not reachable on mobile");
  for (const firstFrame of [151, 101, 51, 1]) {
    await mobileFramesModal.getByRole("button", { name: "Previous" }).tap();
    await mobileFramesModal.getByText(`frame_${String(firstFrame).padStart(4, "0")}.png`, { exact: true }).waitFor();
  }
  assert(await mobileFramesModal.getByText("frame_0001.png", { exact: true }).isVisible(), "Returning to mobile frame page one failed");
  await mobile.waitForTimeout(220);
  await mobile.screenshot({ path: path.join(outputDirectory, "mobile-frames.png"), scale: "css" });
  await mobileFramesModal.getByRole("button", { name: "Close frame sequence" }).tap();
  await mobileFramesModal.waitFor({ state: "hidden" });
  await mobile.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));

  const mobileFit = await mobile.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    headerDashboardRightDelta: Math.abs(
      document.querySelector(".header-controls").getBoundingClientRect().right
        - document.querySelector(".dashboard-grid").getBoundingClientRect().right,
    ),
    metricClientWidth: document.querySelector(".metrics-scroll").clientWidth,
    metricScrollWidth: document.querySelector(".metrics-scroll").scrollWidth,
    metricClientHeight: document.querySelector(".metrics-scroll").clientHeight,
    metricScrollHeight: document.querySelector(".metrics-scroll").scrollHeight,
    resourceRows: document.querySelectorAll(".resource-row").length,
    resourceBounds: [...document.querySelectorAll(".resource-row")].map((row) => {
      const bounds = row.getBoundingClientRect();
      return { top: bounds.top, bottom: bounds.bottom };
    }),
  }));
  assert(mobileFit.scrollWidth <= mobileFit.width, "Mobile page has horizontal overflow");
  assert(mobileFit.headerDashboardRightDelta <= 1, "Mobile header controls and dashboard do not share the same right edge");
  assert(mobileFit.metricScrollWidth <= mobileFit.metricClientWidth, "Mobile metrics have horizontal overflow");
  assert(mobileFit.resourceRows === 3, "Mobile metrics lost CPU or GPU rows");
  assert(mobileFit.metricScrollHeight > mobileFit.metricClientHeight, "Additional mobile hardware rows do not scroll inside the metrics panel");
  assert(mobileFit.resourceBounds.every((row, index, rows) => index === 0 || row.top >= rows[index - 1].bottom - 1), "Mobile hardware rows overlap");
  await mobile.screenshot({ path: path.join(outputDirectory, "mobile-initial.png"), fullPage: true, scale: "css" });
  await mobileContext.close();

  assert(browserErrors.length === 0, `Browser errors:\n${browserErrors.join("\n")}`);
  console.log("Playwright smoke QA passed: desktop flow, version manager, render controls, live log, and mobile fit.");
} finally {
  await browser.close();
}
