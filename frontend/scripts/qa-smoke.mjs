import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const targetUrl = process.env.TARGET_URL ?? "http://127.0.0.1:5173";
const outputDirectory = fileURLToPath(new URL("../test-results/", import.meta.url));
const browser = await chromium.launch({ headless: true });
const browserErrors = [];
const officialArchiveHtml = `
  <a href="Blender5.2/">Blender5.2/</a>
  <a href="Blender5.1/">Blender5.1/</a>
  <a href="Blender4.5/">Blender4.5/</a>
  <a href="Blender4.4/">Blender4.4/</a>
  <a href="Blender4.3/">Blender4.3/</a>
  <a href="Blender4.2/">Blender4.2/</a>
  <a href="Blender4.1/">Blender4.1/</a>
  <a href="Blender4.0/">Blender4.0/</a>
  <a href="Blender3.6/">Blender3.6/</a>
  <a href="Blender3.5/">Blender3.5/</a>
  <a href="Blender3.4/">Blender3.4/</a>
  <a href="Blender3.3/">Blender3.3/</a>
  <a href="Blender3.2/">Blender3.2/</a>
  <a href="Blender3.1/">Blender3.1/</a>
  <a href="Blender3.0/">Blender3.0/</a>
  <a href="Blender2.93/">Blender2.93/</a>
`;

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
  let officialArchiveRequests = 0;
  await desktop.route("https://download.blender.org/release/", async (route) => {
    officialArchiveRequests += 1;
    await route.fulfill({
      contentType: "text/html",
      body: officialArchiveHtml,
      status: 200,
    });
  });
  await desktop.goto(targetUrl, { waitUntil: "networkidle" });
  await desktop.evaluate(() => window.localStorage.setItem("render-node-theme", "dark"));
  await desktop.reload({ waitUntil: "networkidle" });

  assert(await desktop.getByRole("heading", { name: "Job setup" }).isVisible(), "Desktop job setup is not visible");
  const logSeverityContract = await desktop.evaluate(async () => {
    const { getLogLevel } = await import("/src/logPresentation.js");
    return {
      completedFatal: getLogLevel("FATAL: Blender process crashed"),
      completedError: getLogLevel("EGL Error (0x3009): non-fatal surface mismatch"),
      warning: getLogLevel("Warning: scene uses a newer Blender version"),
    };
  });
  assert(
    logSeverityContract.completedFatal === "error" && logSeverityContract.completedError === "error"
      && logSeverityContract.warning === "warning",
    "Log severity styling does not distinguish errors from warnings",
  );
  assert(await desktop.getByText("Blender 4.5.11", { exact: true }).first().isVisible(), "Active Blender version is not visible");
  const headerSummary = desktop.getByRole("navigation", { name: "Node summary" });
  assert(await headerSummary.isVisible(), "Desktop node summary is not visible in the header");
  assert(await headerSummary.getByText("0 queued", { exact: true }).isVisible(), "Queued job count is missing from the header");
  assert(await headerSummary.getByText("2 GPUs ready", { exact: true }).isVisible(), "Ready GPU count is missing from the header");
  assert(await headerSummary.getByText("12m avg. frame", { exact: true }).isVisible(), "Average frame time is missing from the header");
  assert((await headerSummary.locator(".summary-storage-warning").count()) === 0, "Healthy storage exposes a critical header warning");
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
  assert((await desktop.locator(".resource-row").count()) === 4, "Metrics do not render every CPU, GPU, and storage row");
  const storageRow = desktop.locator(".resource-row").filter({ hasText: "STORAGE 01" });
  assert(await storageRow.getByText("Workspace NVMe", { exact: true }).isVisible(), "Workspace storage identity is missing");
  assert(await storageRow.getByText("/workspace · 412 GB free of 1 TB", { exact: true }).isVisible(), "Available workspace capacity is missing");
  assert(await storageRow.getByLabel("Used: 59%").isVisible(), "Storage usage indicator is missing");
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
  assert((await desktop.locator(".metric-chart").count()) === 12, "Not every hardware metric has a line chart");

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
  const desktopFrameRowHeight = await desktopFramesModal.locator(".frame-row").first().evaluate(
    (row) => row.getBoundingClientRect().height,
  );
  assert(Math.abs(desktopFrameRowHeight - 54) <= 1, "Frame download rows do not keep their fixed height");
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
  const singleFrameLayout = await desktopFrameList.evaluate((list) => {
    const rows = [...list.querySelectorAll(".frame-row")];
    rows.slice(1).forEach((row) => row.remove());
    const listBounds = list.getBoundingClientRect();
    const rowBounds = rows[0].getBoundingClientRect();
    return {
      listBottom: listBounds.bottom,
      rowBottom: rowBounds.bottom,
      rowHeight: rowBounds.height,
    };
  });
  assert(Math.abs(singleFrameLayout.rowHeight - 54) <= 1, "A single frame row stretches vertically");
  assert(singleFrameLayout.listBottom - singleFrameLayout.rowBottom > 100, "A single frame row fills the frame list");
  await desktop.screenshot({ path: path.join(outputDirectory, "desktop-frames-single.png"), scale: "css" });
  await desktop.keyboard.press("Escape");
  await desktopFramesModal.waitFor({ state: "hidden" });

  const desktopVersionButton = desktop.locator(".version-button");
  await desktopVersionButton.click();
  const versionDialog = desktop.getByRole("dialog", { name: "Blender versions" });
  assert(await versionDialog.isVisible(), "Version manager did not open");
  const closeVersionButton = versionDialog.getByRole("button", { name: "Close version manager" });
  await desktop.waitForFunction(() => document.activeElement?.getAttribute("aria-label") === "Close version manager");
  assert(await closeVersionButton.evaluate((button) => button === document.activeElement), "Version manager did not focus its close action");
  assert(await desktop.locator("body").evaluate((body) => body.style.overflow === "hidden"), "Version manager did not lock background scrolling");
  await desktop.keyboard.press("Shift+Tab");
  assert(await versionDialog.evaluate((dialog) => dialog.contains(document.activeElement)), "Version manager focus escaped the dialog");
  assert((await versionDialog.locator(".version-list .version-row").count()) === 5, "Installed version list has an unexpected number of rows");
  assert(officialArchiveRequests === 0, "Official archive loaded before the user opened it");
  await desktop.waitForTimeout(180);
  const collapsedVersionBox = await versionDialog.boundingBox();
  await versionDialog.getByRole("button", { name: /Choose other versions/ }).click();
  const officialVersions = versionDialog.getByLabel("Available Blender versions");
  assert(await officialVersions.isVisible(), "Official version catalog did not open");
  await officialVersions.getByText("Blender 4.4", { exact: true }).waitFor();
  assert(officialArchiveRequests === 1, "Official archive was not loaded exactly once");
  const expandedVersionBox = await versionDialog.boundingBox();
  assert(
    collapsedVersionBox && expandedVersionBox && expandedVersionBox.height > collapsedVersionBox.height + 100,
    "Opening the official catalog does not increase the popup height",
  );
  assert(
    expandedVersionBox.y >= 0 && expandedVersionBox.y + expandedVersionBox.height <= 901,
    "Expanded version manager extends beyond the desktop viewport",
  );
  const expandedVersionFit = await versionDialog.evaluate((dialog) => ({
    clientHeight: dialog.clientHeight,
    overflowY: getComputedStyle(dialog).overflowY,
    scrollHeight: dialog.scrollHeight,
  }));
  assert(expandedVersionFit.overflowY === "hidden", "The whole version manager remains scrollable");
  assert(expandedVersionFit.scrollHeight <= expandedVersionFit.clientHeight, "Version manager content overflows its shell");
  const officialVersionScroll = await officialVersions.evaluate((catalog) => ({
    clientHeight: catalog.clientHeight,
    overflowY: getComputedStyle(catalog).overflowY,
    scrollHeight: catalog.scrollHeight,
  }));
  assert(officialVersionScroll.overflowY === "auto", "Official version list is not the scroll container");
  assert(officialVersionScroll.scrollHeight > officialVersionScroll.clientHeight, "Long official version list does not scroll internally");
  await officialVersions.evaluate((catalog) => { catalog.scrollTop = catalog.scrollHeight; });
  assert((await officialVersions.evaluate((catalog) => catalog.scrollTop)) > 0, "Official version list cannot be scrolled");
  const blender44 = officialVersions.locator(".catalog-version-row").filter({ hasText: "Blender 4.4" });
  await blender44.getByRole("button", { name: "Download" }).click();
  await blender44.getByRole("button", { name: "Install" }).waitFor();
  await blender44.getByRole("button", { name: "Install" }).click();
  await versionDialog.locator(".version-list .version-row").filter({ hasText: "Blender 4.4" }).waitFor();
  assert((await versionDialog.locator(".version-list .version-row").count()) === 6, "Installed version did not move to the main list");
  await versionDialog.locator('input[type="file"]').setInputFiles({
    name: "blender-4.3.3-linux-x64.tar.xz",
    mimeType: "application/x-xz",
    buffer: Buffer.alloc(1024),
  });
  const manualVersion = officialVersions.locator(".catalog-version-row").filter({ hasText: "Blender 4.3.3" });
  await manualVersion.getByText(/ready to install/).waitFor();
  assert(await manualVersion.getByText("Manual", { exact: true }).isVisible(), "Uploaded installer is not marked as manual");
  await manualVersion.getByRole("button", { name: "Install" }).click();
  await versionDialog.locator(".version-list .version-row").filter({ hasText: "Blender 4.3.3" }).waitFor();
  assert((await versionDialog.locator(".version-list .version-row").count()) === 7, "Manually uploaded version did not move to the main list");
  const blender52 = desktop.locator(".version-row").filter({ hasText: "Blender 5.2.0" });
  await blender52.getByRole("button", { name: "Make active" }).click();
  await blender52.getByText("Active", { exact: true }).waitFor();
  await desktop.keyboard.press("Escape");
  await versionDialog.waitFor({ state: "hidden" });
  assert(await desktopVersionButton.evaluate((button) => button === document.activeElement), "Version manager did not restore focus to its opener");
  assert(await desktop.locator("body").evaluate((body) => body.style.overflow === ""), "Version manager did not restore background scrolling");

  await desktop.getByRole("button", { name: /Start render/ }).click();
  await desktop.getByText("Rendering", { exact: true }).first().waitFor();
  assert(await desktop.getByRole("button", { name: /Cancel render/ }).isVisible(), "Cancel action is not available during render");
  assert(await headerSummary.getByText("1 queued", { exact: true }).isVisible(), "Queued job count does not update after starting a render");

  assert((await desktop.getByRole("tab", { name: /Live log/ }).count()) === 0, "Live log tab was not removed");
  assert((await desktop.getByRole("button", { name: "More options" }).count()) === 0, "Preview overflow placeholder was not removed");
  const logToggle = desktop.locator(".preview-log-toggle");
  const liveLog = desktop.locator(".preview-log-overlay");
  assert(await logToggle.isVisible(), "Collapsed live log tab is not visible on the rendered frame");
  assert(await logToggle.getAttribute("aria-label") === "Show Blender live log", "Collapsed live log tab has the wrong accessible label");
  assert(await logToggle.getAttribute("aria-expanded") === "false", "Live log drawer is not collapsed initially");
  assert(await liveLog.getAttribute("aria-hidden") === "true" && await liveLog.getAttribute("tabindex") === "-1", "Collapsed live log remains accessible to pointer or keyboard input");
  const closedLogLayout = await liveLog.evaluate((overlay) => {
    const bounds = overlay.closest(".preview-log-shell").getBoundingClientRect();
    const viewportBounds = overlay.closest(".render-viewport").getBoundingClientRect();
    return {
      overlayLeft: bounds.left,
      shellTranslateX: new DOMMatrixReadOnly(getComputedStyle(overlay.closest(".preview-log-shell")).transform).m41,
      viewportRight: viewportBounds.right,
    };
  });
  assert(closedLogLayout.overlayLeft >= closedLogLayout.viewportRight, "Collapsed live log glass remains visible inside the preview");
  const tabBoxBeforeHover = await logToggle.boundingBox();
  await logToggle.hover();
  await desktop.waitForTimeout(180);
  const tabHoverState = await logToggle.evaluate((button) => ({
    animationName: getComputedStyle(button).animationName,
    bounds: button.getBoundingClientRect().toJSON(),
    clipPath: getComputedStyle(button).clipPath,
    filter: getComputedStyle(button).filter,
    surfaceAnimationName: getComputedStyle(button, "::before").animationName,
    surfaceClipPath: getComputedStyle(button, "::before").clipPath,
  }));
  assert(
    tabHoverState.animationName === "none" && tabHoverState.filter === "none"
      && tabHoverState.surfaceAnimationName === "none",
    "Live log tab unexpectedly animates or filters on hover",
  );
  assert(
    tabHoverState.clipPath === "none",
    "Live log tab clips its hit area instead of keeping a rectangular click target",
  );
  assert(
    tabHoverState.surfaceClipPath.startsWith("polygon(") && tabHoverState.surfaceClipPath.split(",").length === 4,
    "Collapsed live log surface is not a four-sided trapezoid",
  );
  assert(
    tabBoxBeforeHover && Math.abs(tabHoverState.bounds.width - tabBoxBeforeHover.width) <= 1
      && Math.abs(tabHoverState.bounds.height - tabBoxBeforeHover.height) <= 1,
    "Live log tab hover changes its layout geometry",
  );
  await logToggle.click();
  await desktop.waitForTimeout(100);
  const tabBoxDuringOpen = await logToggle.boundingBox();
  const tabVisualDuringOpen = await logToggle.evaluate((button) => ({
    afterClipPath: getComputedStyle(button, "::after").clipPath,
    afterFilter: getComputedStyle(button, "::after").filter,
    afterTransform: getComputedStyle(button, "::after").transform,
    beforeClipPath: getComputedStyle(button, "::before").clipPath,
    beforeFilter: getComputedStyle(button, "::before").filter,
    beforeTransform: getComputedStyle(button, "::before").transform,
    outlineStyle: getComputedStyle(button).outlineStyle,
    userSelect: getComputedStyle(button).userSelect,
  }));
  const logMotionDuringTabPhase = await liveLog.evaluate((overlay) => {
    const drawer = overlay.closest(".preview-log-drawer");
    return {
      panelOpen: drawer.classList.contains("is-panel-open"),
      shellTranslateX: new DOMMatrixReadOnly(getComputedStyle(overlay.closest(".preview-log-shell")).transform).m41,
    };
  });
  assert(await logToggle.getAttribute("aria-expanded") === "true", "Live log toggle does not expose its requested open state");
  assert(
    await liveLog.getAttribute("aria-hidden") === "true" && await liveLog.getAttribute("tabindex") === "-1",
    "Live log panel starts before the tab expansion phase finishes",
  );
  await desktop.waitForFunction(() => document.querySelector(".preview-log-drawer")?.classList.contains("is-panel-open"));
  await desktop.waitForTimeout(80);
  const logMotionDuringPanelPhase = await liveLog.evaluate((overlay) => {
    const drawer = overlay.closest(".preview-log-drawer");
    return {
      panelOpen: drawer.classList.contains("is-panel-open"),
      shellTranslateX: new DOMMatrixReadOnly(getComputedStyle(overlay.closest(".preview-log-shell")).transform).m41,
    };
  });
  await desktop.waitForTimeout(520);
  const tabBoxAfterOpen = await logToggle.boundingBox();
  assert(
    tabBoxBeforeHover && tabBoxDuringOpen && tabBoxAfterOpen
      && ["x", "width"].every(
        (key) => Math.abs(tabBoxBeforeHover[key] - tabBoxDuringOpen[key]) <= 0.1
          && Math.abs(tabBoxBeforeHover[key] - tabBoxAfterOpen[key]) <= 0.1,
      ),
    "Live log tab shifts horizontally or jitters while the drawer opens",
  );
  assert(
    tabVisualDuringOpen.outlineStyle === "none"
      && tabVisualDuringOpen.userSelect === "none"
      && tabVisualDuringOpen.beforeClipPath.startsWith("polygon(")
      && tabVisualDuringOpen.afterClipPath.startsWith("polygon(")
      && tabVisualDuringOpen.beforeFilter === "none"
      && tabVisualDuringOpen.afterFilter === "none"
      && tabVisualDuringOpen.beforeTransform === "none"
      && tabVisualDuringOpen.afterTransform === "none",
    "Live log tab exposes a rectangular focus or selection surface during animation",
  );
  assert(
    closedLogLayout.shellTranslateX > 0
      && !logMotionDuringTabPhase.panelOpen
      && Math.abs(logMotionDuringTabPhase.shellTranslateX - closedLogLayout.shellTranslateX) <= 1
      && logMotionDuringPanelPhase.panelOpen
      && logMotionDuringPanelPhase.shellTranslateX > 0
      && logMotionDuringPanelPhase.shellTranslateX < closedLogLayout.shellTranslateX,
    "Live log tab and panel phases overlap or run out of sequence",
  );
  assert(await logToggle.getAttribute("aria-expanded") === "true", "Live log drawer did not open from its tab");
  assert(await liveLog.getAttribute("aria-hidden") === "false" && await liveLog.getAttribute("tabindex") === "0", "Open live log is not keyboard accessible");
  const openedLogDrawer = await liveLog.evaluate((overlay) => {
    const bounds = overlay.closest(".preview-log-shell").getBoundingClientRect();
    const drawer = overlay.closest(".preview-log-drawer");
    const viewportBounds = overlay.closest(".render-viewport").getBoundingClientRect();
    const toggle = overlay.closest(".render-viewport").querySelector(".preview-log-toggle");
    const toggleBounds = toggle.getBoundingClientRect();
    return {
      bottom: bounds.bottom,
      height: bounds.height,
      left: bounds.left,
      right: bounds.right,
      railCut: Number.parseFloat(getComputedStyle(drawer).getPropertyValue("--log-rail-cut")),
      toggleInnerPath: getComputedStyle(toggle, "::after").clipPath,
      toggleBottom: toggleBounds.bottom,
      toggleHeight: toggleBounds.height,
      toggleLeft: toggleBounds.left,
      toggleOuterPath: getComputedStyle(toggle, "::before").clipPath,
      toggleRight: toggleBounds.right,
      toggleTop: toggleBounds.top,
      toggleWidth: toggleBounds.width,
      top: bounds.top,
      viewportBottom: viewportBounds.bottom,
      viewportLeft: viewportBounds.left,
      viewportRight: viewportBounds.right,
    };
  });
  assert(
    openedLogDrawer.left >= openedLogDrawer.viewportLeft && openedLogDrawer.toggleRight <= openedLogDrawer.viewportRight
      && openedLogDrawer.bottom <= openedLogDrawer.viewportBottom,
    "Open live log drawer extends beyond the preview",
  );
  assert(
    Math.abs(openedLogDrawer.right - openedLogDrawer.toggleLeft) <= 2
      && openedLogDrawer.toggleWidth >= 21 && openedLogDrawer.toggleWidth <= 23
      && openedLogDrawer.toggleOuterPath.startsWith("polygon(")
      && openedLogDrawer.toggleInnerPath.startsWith("polygon(")
      && Math.abs(openedLogDrawer.top - openedLogDrawer.toggleTop - openedLogDrawer.railCut) <= 1
      && Math.abs(openedLogDrawer.toggleBottom - openedLogDrawer.bottom - openedLogDrawer.railCut) <= 1
      && Math.abs(openedLogDrawer.height - (openedLogDrawer.toggleHeight - 2 * openedLogDrawer.railCut)) <= 2,
    "Live log panel does not align with the trapezoid's shorter inner edge",
  );
  assert((await liveLog.locator("code").count()) > 4, "Live log still truncates its history to four entries");
  await desktop.evaluate(async () => {
    const { mockApi } = await import("/src/mockApi.js");
    for (let index = 0; index < 3; index += 1) {
      mockApi.logLines.push([`09:41:${30 + index}`, "EGL Error: repeated surface mismatch"]);
    }
    mockApi.logLines.push(["09:41:33", "Warning: scene was written by a newer Blender version"]);
    mockApi.logLines.push(["09:41:34", "Blender quit"]);
    for (let index = 0; index < 24; index += 1) {
      mockApi.logLines.push([
        `09:42:${String(index).padStart(2, "0")}`,
        index === 0
          ? `Long unbroken diagnostic token: ${"render_node_layout_regression_".repeat(12)}`
          : `Progress update ${index + 1} / 24 | keeping the render worker responsive`,
      ]);
    }
  });
  await desktop.getByRole("button", { name: "Light theme" }).click();
  await desktop.getByRole("button", { name: "Dark theme" }).click();
  await desktop.waitForFunction(() => document.querySelectorAll('.preview-log-overlay code').length >= 30);
  const longLogLine = liveLog.locator("code").filter({ hasText: "Long unbroken diagnostic token" });
  const repeatedError = liveLog.locator(".log-entry").filter({ hasText: "EGL Error: repeated surface mismatch" });
  assert((await repeatedError.count()) === 1, "Consecutive duplicate log messages were not collapsed");
  assert(await repeatedError.getByLabel("Repeated 3 times").isVisible(), "Collapsed log message does not expose its repeat count");
  const logLevelColors = await liveLog.evaluate((overlay) => ({
    error: getComputedStyle(overlay.querySelector(".log-level-error")).color,
    info: getComputedStyle(overlay.querySelector(".log-level-info")).color,
    system: getComputedStyle(overlay.querySelector(".log-level-system")).color,
    warning: getComputedStyle(overlay.querySelector(".log-level-warning")).color,
  }));
  assert(new Set(Object.values(logLevelColors)).size === 4, "Log severity levels do not have distinct colors");
  const logGlassLayout = await liveLog.evaluate((overlay) => {
    const shell = overlay.closest(".preview-log-shell");
    const bounds = shell.getBoundingClientRect();
    const overlayBounds = overlay.getBoundingClientRect();
    const firstVisibleEntry = [...overlay.children].find((entry) => entry.getBoundingClientRect().bottom > overlayBounds.top + 0.5);
    const viewportBounds = overlay.closest(".render-viewport").getBoundingClientRect();
    const actionBounds = overlay.closest(".render-viewport").querySelector(".preview-frame-actions").getBoundingClientRect();
    const style = getComputedStyle(shell);
    const scrollStyle = getComputedStyle(overlay);
    return {
      actionTop: actionBounds.top,
      backdropFilter: style.backdropFilter,
      borderRightWidth: style.borderRightWidth,
      borderTopRightRadius: style.borderTopRightRadius,
      bottom: bounds.bottom,
      clientHeight: overlay.clientHeight,
      firstVisibleTop: firstVisibleEntry?.getBoundingClientRect().top ?? overlayBounds.top,
      maxScrollTop: overlay.scrollHeight - overlay.clientHeight,
      maskImage: scrollStyle.maskImage,
      overflowY: scrollStyle.overflowY,
      paddingTop: Number.parseFloat(style.paddingTop),
      pointerEvents: style.pointerEvents,
      scrollHeight: overlay.scrollHeight,
      scrollbarButtonDisplay: getComputedStyle(overlay, "::-webkit-scrollbar-button").display,
      scrollTop: overlay.scrollTop,
      textShadow: scrollStyle.textShadow,
      top: bounds.top,
      visibleAreaTop: overlayBounds.top,
      viewportBottom: viewportBounds.bottom,
      viewportTop: viewportBounds.top,
    };
  });
  const logToggleGlass = await logToggle.evaluate((button) => {
    const style = getComputedStyle(button);
    const contourStyle = getComputedStyle(button, "::before");
    const surfaceStyle = getComputedStyle(button, "::after");
    const arrow = button.querySelector(".preview-log-toggle-arrow svg").getBoundingClientRect();
    return {
      arrowWidth: arrow.width,
      backdropFilter: surfaceStyle.backdropFilter,
      clipPath: style.clipPath,
      contourBackground: contourStyle.backgroundColor,
      contourFilter: contourStyle.filter,
      contourTransform: contourStyle.transform,
      contourWillChange: contourStyle.willChange,
      surfaceBackground: surfaceStyle.backgroundImage,
      surfaceClipPath: surfaceStyle.clipPath,
      surfaceFilter: surfaceStyle.filter,
      surfaceTransform: surfaceStyle.transform,
      surfaceWillChange: surfaceStyle.willChange,
    };
  });
  const wrappedLogLayout = await longLogLine.evaluate((line) => {
    const style = getComputedStyle(line);
    return {
      height: line.getBoundingClientRect().height,
      lineHeight: Number.parseFloat(style.lineHeight),
      overflowWrap: style.overflowWrap,
      whiteSpace: style.whiteSpace,
    };
  });
  assert(logGlassLayout.backdropFilter.includes("blur"), "Live log is missing its glass blur");
  assert(
    logGlassLayout.borderRightWidth === "0px" && logGlassLayout.borderTopRightRadius === "0px"
      && logToggleGlass.backdropFilter.includes("blur")
      && logToggleGlass.surfaceBackground.includes("linear-gradient")
      && logToggleGlass.contourBackground !== "rgba(0, 0, 0, 0)",
    "Live log and its trapezoid do not form a seamless glass surface",
  );
  assert(logGlassLayout.paddingTop >= 15, "Live log content is pressed against its top edge");
  assert(logToggleGlass.arrowWidth >= 17, "Expanded live log arrow is too small");
  assert(
    logToggleGlass.clipPath === "none"
      && logToggleGlass.surfaceClipPath.startsWith("polygon(")
      && logToggleGlass.contourFilter === "none"
      && logToggleGlass.surfaceFilter === "none"
      && logToggleGlass.contourTransform === "none"
      && logToggleGlass.surfaceTransform === "none"
      && logToggleGlass.contourWillChange === "auto"
      && logToggleGlass.surfaceWillChange === "auto",
    "Live log rail has lost its trapezoid surface or rectangular hit area",
  );
  assert(logGlassLayout.overflowY === "auto" && logGlassLayout.pointerEvents === "auto", "Live log is not an interactive scroll pane");
  assert(logGlassLayout.maskImage === "none", "Live log still clips its first row with a mask");
  assert(logGlassLayout.scrollbarButtonDisplay === "none", "Live log scrollbar still exposes arrow buttons");
  assert(logGlassLayout.textShadow.includes("3px"), "Live log text glow is stronger than intended");
  assert(logGlassLayout.scrollHeight > logGlassLayout.clientHeight, "Long live log does not overflow internally");
  assert(Math.abs(logGlassLayout.scrollTop - logGlassLayout.maxScrollTop) <= 2, "Live log does not initially follow its latest entry");
  assert(logGlassLayout.firstVisibleTop >= logGlassLayout.visibleAreaTop - 1, "Live log tail starts with a clipped partial row");
  assert(
    logGlassLayout.top >= logGlassLayout.viewportTop && logGlassLayout.bottom <= logGlassLayout.viewportBottom
      && logGlassLayout.bottom < logGlassLayout.actionTop,
    "Live log glass panel overlaps preview controls or leaves the viewport",
  );
  assert(
    wrappedLogLayout.whiteSpace === "pre-wrap" && wrappedLogLayout.overflowWrap === "anywhere"
      && wrappedLogLayout.height > wrappedLogLayout.lineHeight * 2,
    "Long live log entry is truncated instead of wrapping",
  );
  const followedScrollTop = logGlassLayout.scrollTop;
  await liveLog.hover();
  await desktop.mouse.wheel(0, -500);
  await desktop.waitForTimeout(120);
  const manualScrollTop = await liveLog.evaluate((overlay) => overlay.scrollTop);
  assert(manualScrollTop < followedScrollTop, "Live log cannot be scrolled upward");
  await desktop.evaluate(async () => {
    const { mockApi } = await import("/src/mockApi.js");
    mockApi.logLines.push(["09:43:00", "A new line must not steal the user's manual scroll position"]);
  });
  await desktop.getByRole("button", { name: "Light theme" }).click();
  await liveLog.getByText("A new line must not steal the user's manual scroll position", { exact: true }).waitFor();
  const preservedScrollTop = await liveLog.evaluate((overlay) => overlay.scrollTop);
  assert(Math.abs(preservedScrollTop - manualScrollTop) <= 2, "A new log entry overrides the user's manual scroll position");
  await liveLog.hover();
  await desktop.mouse.wheel(0, 10_000);
  await desktop.waitForTimeout(120);
  await desktop.evaluate(async () => {
    const { mockApi } = await import("/src/mockApi.js");
    mockApi.logLines.push(["09:43:01", "Tail following resumed"]);
  });
  await desktop.getByRole("button", { name: "Dark theme" }).click();
  await liveLog.getByText("Tail following resumed", { exact: true }).waitFor();
  const resumedTail = await liveLog.evaluate((overlay) => ({
    maxScrollTop: overlay.scrollHeight - overlay.clientHeight,
    scrollTop: overlay.scrollTop,
  }));
  assert(Math.abs(resumedTail.scrollTop - resumedTail.maxScrollTop) <= 2, "Live log does not resume tail following at the bottom");
  await desktop.screenshot({ path: path.join(outputDirectory, "desktop-log-glass.png"), scale: "css" });
  await logToggle.click();
  await desktop.waitForTimeout(760);
  assert(await logToggle.getAttribute("aria-expanded") === "false", "Second live log tab click did not close the drawer");
  await logToggle.click();
  await desktop.waitForTimeout(800);
  await liveLog.focus();
  await desktop.keyboard.press("Escape");
  await desktop.waitForTimeout(760);
  assert(await logToggle.getAttribute("aria-expanded") === "false", "Escape did not close the live log drawer");
  assert(await logToggle.evaluate((button) => button === document.activeElement), "Closing the live log with Escape did not restore focus to its tab");
  await logToggle.click();
  await desktop.waitForTimeout(80);
  await logToggle.click();
  await desktop.waitForTimeout(300);
  assert(
    await logToggle.getAttribute("aria-expanded") === "false"
      && !(await logToggle.getAttribute("class")).includes("is-open")
      && !(await liveLog.locator("xpath=ancestor::*[contains(@class, 'preview-log-drawer')]").getAttribute("class")).includes("is-panel-open"),
    "Rapid repeated click leaves the staged live log transition partially open",
  );
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

  const standardHdContext = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
  });
  const standardHd = await standardHdContext.newPage();
  watchErrors(standardHd);
  await standardHd.goto(targetUrl, { waitUntil: "networkidle" });
  const readStandardHdLayout = () => standardHd.evaluate(() => {
    const setup = document.querySelector(".setup-panel").getBoundingClientRect();
    const preview = document.querySelector(".preview-panel").getBoundingClientRect();
    const rightRail = document.querySelector(".right-rail").getBoundingClientRect();
    const metrics = document.querySelector(".metrics-strip").getBoundingClientRect();
    const footer = document.querySelector(".app-footer").getBoundingClientRect();
    return {
      clientHeight: document.documentElement.clientHeight,
      scrollHeight: document.documentElement.scrollHeight,
      setupBottom: setup.bottom,
      previewBottom: preview.bottom,
      rightRailBottom: rightRail.bottom,
      metricsTop: metrics.top,
      metricsBottom: metrics.bottom,
      footerBottom: footer.bottom,
    };
  });
  const assertStandardHdFit = (layout, state) => {
    assert(layout.scrollHeight <= layout.clientHeight, `Standard HD page has vertical overflow while ${state}`);
    assert(layout.metricsBottom <= layout.clientHeight + 1, `Standard HD metrics leave the viewport while ${state}`);
    assert(layout.footerBottom <= layout.clientHeight + 1, `Standard HD footer leaves the viewport while ${state}`);
    assert(Math.abs(layout.metricsTop - layout.setupBottom - 14) <= 1, `Job setup overlaps metrics while ${state}`);
    assert(Math.abs(layout.metricsTop - layout.previewBottom - 14) <= 1, `Preview overlaps metrics while ${state}`);
    assert(Math.abs(layout.metricsTop - layout.rightRailBottom - 14) <= 1, `Right rail overlaps metrics while ${state}`);
  };

  assertStandardHdFit(await readStandardHdLayout(), "ready");
  await standardHd.screenshot({ path: path.join(outputDirectory, "desktop-standard-hd-ready.png"), scale: "css" });
  await standardHd.locator(".render-viewport").evaluate(async (viewport) => {
    const portrait = document.createElement("img");
    portrait.alt = "Portrait preview layout regression";
    portrait.className = "render-frame-image";
    portrait.src = `data:image/svg+xml,${encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="900" viewBox="0 0 600 900"><rect width="600" height="900" fill="#111820"/></svg>',
    )}`;
    viewport.append(portrait);
    await portrait.decode();
  });
  const portraitPreviewLayout = await standardHd.locator(".render-frame-image").evaluate((image) => {
    const imageBounds = image.getBoundingClientRect();
    const viewportBounds = image.closest(".render-viewport").getBoundingClientRect();
    return {
      imageHeight: imageBounds.height,
      imagePosition: getComputedStyle(image).position,
      imageWidth: imageBounds.width,
      viewportHeight: viewportBounds.height,
      viewportWidth: viewportBounds.width,
    };
  });
  assertStandardHdFit(await readStandardHdLayout(), "portrait preview");
  assert(portraitPreviewLayout.imagePosition === "absolute", "Portrait preview participates in layout sizing");
  assert(
    Math.abs(portraitPreviewLayout.imageWidth - portraitPreviewLayout.viewportWidth) <= 1
      && Math.abs(portraitPreviewLayout.imageHeight - portraitPreviewLayout.viewportHeight) <= 1,
    "Portrait preview does not stay inside the render viewport",
  );
  await standardHd.screenshot({ path: path.join(outputDirectory, "desktop-standard-hd-portrait.png"), scale: "css" });
  await standardHd.locator(".render-frame-image").evaluate((image) => image.remove());
  await standardHd.getByRole("button", { name: /Start render/ }).click();
  await standardHd.getByRole("button", { name: /Cancel render/ }).waitFor();
  assertStandardHdFit(await readStandardHdLayout(), "rendering");
  await standardHd.screenshot({ path: path.join(outputDirectory, "desktop-standard-hd-rendering.png"), scale: "css" });
  await standardHd.getByRole("button", { name: /Cancel render/ }).click();
  await standardHdContext.close();

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
  assert(compactFit.resourceRows === 4, "Compact metrics lost a CPU, GPU, or storage row");
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
  assert(ultrawideLayout.resourceRows === 4, "Ultrawide metrics lost a CPU, GPU, or storage row");
  assert(ultrawideLayout.metricsOverflowY === "auto", "Hardware rows are not configured for internal scrolling");
  assert(ultrawideLayout.visibleMetricRows === 2, "Metrics panel does not limit the viewport to two resource rows");
  assert(ultrawideLayout.metricsScrollHeight > ultrawideLayout.metricsClientHeight, "Additional resource rows do not create internal scrolling");
  assert(Math.abs(ultrawideLayout.rootFont - compactFit.rootFont) < 0.1, "Root typography overrides the browser base size");
  assert(ultrawideLayout.controlFont > compactFit.controlFont, "Control typography does not scale with the viewport");
  await ultrawide.screenshot({ path: path.join(outputDirectory, "desktop-ultrawide.png"), fullPage: true, scale: "css" });
  const ultrawideMetricsScroll = ultrawide.locator(".metrics-scroll");
  await ultrawideMetricsScroll.hover();
  await ultrawide.mouse.wheel(0, 600);
  await ultrawide.waitForTimeout(150);
  assert((await ultrawideMetricsScroll.evaluate((element) => element.scrollTop)) > 0, "Metrics panel does not scroll to additional hardware rows");
  const storageRowInView = await ultrawide.locator(".resource-row").filter({ hasText: "STORAGE 01" }).evaluate((row) => {
    const rowBounds = row.getBoundingClientRect();
    const scrollBounds = row.closest(".metrics-scroll").getBoundingClientRect();
    return rowBounds.top < scrollBounds.bottom && rowBounds.bottom > scrollBounds.top;
  });
  assert(storageRowInView, "The storage row is not reachable by scrolling");
  await ultrawide.screenshot({ path: path.join(outputDirectory, "desktop-ultrawide-metrics-scrolled.png"), fullPage: true, scale: "css" });
  await ultrawideContext.close();

  const mobileContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  const mobile = await mobileContext.newPage();
  watchErrors(mobile);
  await mobile.route("https://download.blender.org/release/", (route) => route.fulfill({
    contentType: "text/html",
    body: officialArchiveHtml,
    status: 200,
  }));
  await mobile.goto(targetUrl, { waitUntil: "networkidle" });
  assert(await mobile.getByRole("heading", { name: "Job setup" }).isVisible(), "Mobile job setup is not visible");
  assert(await mobile.locator("html[data-theme='dark']").count() === 1, "A fresh session does not default to dark theme");
  assert(await mobile.locator(".header-summary").isHidden(), "Mobile header summary should be hidden");
  assert(await mobile.getByRole("group", { name: "Color theme" }).isVisible(), "Mobile theme switch is not visible");
  const mobileSetupBox = await mobile.locator(".setup-panel").boundingBox();
  const mobilePreviewBox = await mobile.locator(".preview-panel").boundingBox();
  const mobileStartBox = await mobile.getByRole("button", { name: /Start render/ }).boundingBox();
  assert(
    mobileSetupBox && mobilePreviewBox && mobileSetupBox.y < mobilePreviewBox.y,
    "Mobile job setup is not positioned before the preview",
  );
  assert(mobileStartBox && mobileStartBox.y + mobileStartBox.height <= 844, "Mobile primary render action is below the initial viewport");

  await mobile.getByRole("button", { name: /Start render/ }).tap();
  const mobileLogToggle = mobile.locator(".preview-log-toggle");
  const mobileLiveLog = mobile.locator(".preview-log-overlay");
  await mobileLogToggle.scrollIntoViewIfNeeded();
  const mobileClosedLogLayout = await mobileLiveLog.evaluate((overlay) => {
    const bounds = overlay.closest(".preview-log-shell").getBoundingClientRect();
    const viewportBounds = overlay.closest(".render-viewport").getBoundingClientRect();
    return { overlayLeft: bounds.left, viewportRight: viewportBounds.right };
  });
  assert(mobileClosedLogLayout.overlayLeft >= mobileClosedLogLayout.viewportRight, "Collapsed mobile live log remains visible inside the preview");
  await mobileLogToggle.tap();
  await mobile.waitForTimeout(800);
  const mobileOpenLogLayout = await mobileLiveLog.evaluate((overlay) => {
    const bounds = overlay.closest(".preview-log-shell").getBoundingClientRect();
    const drawer = overlay.closest(".preview-log-drawer");
    const viewportBounds = overlay.closest(".render-viewport").getBoundingClientRect();
    const toggleBounds = overlay.closest(".render-viewport").querySelector(".preview-log-toggle").getBoundingClientRect();
    return {
      bottom: bounds.bottom,
      height: bounds.height,
      left: bounds.left,
      right: bounds.right,
      railCut: Number.parseFloat(getComputedStyle(drawer).getPropertyValue("--log-rail-cut")),
      toggleBottom: toggleBounds.bottom,
      toggleHeight: toggleBounds.height,
      toggleLeft: toggleBounds.left,
      toggleRight: toggleBounds.right,
      toggleTop: toggleBounds.top,
      toggleWidth: toggleBounds.width,
      top: bounds.top,
      viewportBottom: viewportBounds.bottom,
      viewportLeft: viewportBounds.left,
      viewportRight: viewportBounds.right,
      viewportTop: viewportBounds.top,
    };
  });
  assert(
    mobileOpenLogLayout.left >= mobileOpenLogLayout.viewportLeft && mobileOpenLogLayout.toggleRight <= mobileOpenLogLayout.viewportRight
      && mobileOpenLogLayout.top >= mobileOpenLogLayout.viewportTop && mobileOpenLogLayout.bottom <= mobileOpenLogLayout.viewportBottom,
    "Open mobile live log drawer extends beyond the preview",
  );
  assert(
    Math.abs(mobileOpenLogLayout.right - mobileOpenLogLayout.toggleLeft) <= 2
      && mobileOpenLogLayout.toggleWidth >= 18 && mobileOpenLogLayout.toggleWidth <= 20
      && Math.abs(mobileOpenLogLayout.top - mobileOpenLogLayout.toggleTop - mobileOpenLogLayout.railCut) <= 1
      && Math.abs(mobileOpenLogLayout.toggleBottom - mobileOpenLogLayout.bottom - mobileOpenLogLayout.railCut) <= 1
      && Math.abs(mobileOpenLogLayout.height - (mobileOpenLogLayout.toggleHeight - 2 * mobileOpenLogLayout.railCut)) <= 2,
    "Mobile live log panel does not align with the trapezoid's shorter inner edge",
  );
  await mobile.screenshot({ path: path.join(outputDirectory, "mobile-log-drawer.png"), scale: "css" });
  await mobile.getByRole("button", { name: "Hide Blender live log" }).tap();
  await mobile.waitForTimeout(760);
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

  await mobile.locator(".version-button").tap();
  const mobileVersionDialog = mobile.getByRole("dialog", { name: "Blender versions" });
  await mobile.waitForTimeout(180);
  const collapsedMobileVersionBox = await mobileVersionDialog.boundingBox();
  await mobileVersionDialog.getByRole("button", { name: /Choose other versions/ }).tap();
  const mobileOfficialVersions = mobileVersionDialog.getByLabel("Available Blender versions");
  await mobileOfficialVersions.getByText("Blender 4.4", { exact: true }).waitFor();
  const mobileVersionLayout = await mobileVersionDialog.evaluate((dialog) => {
    const catalog = dialog.querySelector(".version-catalog-content");
    const bounds = dialog.getBoundingClientRect();
    return {
      bottom: bounds.bottom,
      catalogClientHeight: catalog.clientHeight,
      catalogOverflowY: getComputedStyle(catalog).overflowY,
      catalogScrollHeight: catalog.scrollHeight,
      clientHeight: dialog.clientHeight,
      height: bounds.height,
      overflowY: getComputedStyle(dialog).overflowY,
      scrollHeight: dialog.scrollHeight,
      top: bounds.top,
    };
  });
  assert(
    collapsedMobileVersionBox && mobileVersionLayout.height > collapsedMobileVersionBox.height + 80,
    "Opening the catalog does not expand the mobile version manager",
  );
  assert(mobileVersionLayout.top >= 0 && mobileVersionLayout.bottom <= 845, "Mobile version manager extends beyond the viewport");
  assert(mobileVersionLayout.overflowY === "hidden", "The whole mobile version manager remains scrollable");
  assert(mobileVersionLayout.scrollHeight <= mobileVersionLayout.clientHeight, "Mobile version manager content overflows its shell");
  assert(mobileVersionLayout.catalogOverflowY === "auto", "Mobile official version list is not the scroll container");
  assert(
    mobileVersionLayout.catalogScrollHeight > mobileVersionLayout.catalogClientHeight,
    "Long mobile official version list does not scroll internally",
  );
  await mobile.screenshot({ path: path.join(outputDirectory, "mobile-versions.png"), scale: "css" });
  await mobileVersionDialog.getByRole("button", { name: "Close version manager" }).tap();
  await mobileVersionDialog.waitFor({ state: "hidden" });

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
  assert(mobileFit.resourceRows === 4, "Mobile metrics lost a CPU, GPU, or storage row");
  assert(mobileFit.metricScrollHeight > mobileFit.metricClientHeight, "Additional mobile hardware rows do not scroll inside the metrics panel");
  assert(mobileFit.resourceBounds.every((row, index, rows) => index === 0 || row.top >= rows[index - 1].bottom - 1), "Mobile hardware rows overlap");
  await mobile.screenshot({ path: path.join(outputDirectory, "mobile-initial.png"), fullPage: true, scale: "css" });
  await mobileContext.close();

  assert(browserErrors.length === 0, `Browser errors:\n${browserErrors.join("\n")}`);
  console.log("Playwright smoke QA passed: desktop flow, version manager, render controls, live log, and mobile fit.");
} finally {
  await browser.close();
}
