import { execFileSync } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { request as httpRequest } from "node:http";
import { createServer } from "node:https";
import { connect } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const publicHost = "pairing.arbitrary-cloud.test";
const publicPort = 9443;
const upstreamHost = "127.0.0.1";
const upstreamPort = 5173;
const rewrittenHost = "render-node.internal.invalid:5173";
const outputDirectory = process.env.QA_OUTPUT_DIR ?? await mkdtemp(join(tmpdir(), "render-node-pairing-qa-"));
const certificateDirectory = await mkdtemp(join(tmpdir(), "render-node-pairing-cert-"));
const keyPath = join(certificateDirectory, "key.pem");
const certificatePath = join(certificateDirectory, "certificate.pem");
const publicOrigin = `https://${publicHost}:${publicPort}`;
const requestTargets = [];
const websocketRequests = [];

await mkdir(outputDirectory, { recursive: true });
execFileSync(
  "openssl",
  [
    "req", "-x509", "-newkey", "rsa:2048", "-nodes",
    "-keyout", keyPath, "-out", certificatePath, "-days", "1",
    "-subj", `/CN=${publicHost}`,
    "-addext", `subjectAltName=DNS:${publicHost}`,
  ],
  { stdio: "ignore" },
);

const proxy = createServer(
  { key: await readFile(keyPath), cert: await readFile(certificatePath) },
  (request, response) => {
    requestTargets.push(request.url ?? "");
    const upstreamRequest = httpRequest(
      {
        host: upstreamHost,
        port: upstreamPort,
        path: request.url,
        method: request.method,
        headers: { ...request.headers, host: rewrittenHost },
      },
      (upstreamResponse) => {
        response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
        upstreamResponse.pipe(response);
      },
    );
    upstreamRequest.on("error", (error) => {
      response.writeHead(502, { "content-type": "text/plain" });
      response.end(`Proxy failure: ${error.message}`);
    });
    request.pipe(upstreamRequest);
  },
);

proxy.on("upgrade", (request, clientSocket, head) => {
  requestTargets.push(request.url ?? "");
  websocketRequests.push({
    application: request.url?.startsWith("/api/v1/events") ?? false,
    cookiePresent: Boolean(request.headers.cookie),
    origin: request.headers.origin ?? null,
    secFetchDest: request.headers["sec-fetch-dest"] ?? null,
    secFetchMode: request.headers["sec-fetch-mode"] ?? null,
    secFetchSite: request.headers["sec-fetch-site"] ?? null,
  });
  const upstreamSocket = connect(upstreamPort, upstreamHost, () => {
    const headers = [];
    for (let index = 0; index < request.rawHeaders.length; index += 2) {
      const name = request.rawHeaders[index];
      const value = name.toLowerCase() === "host" ? rewrittenHost : request.rawHeaders[index + 1];
      headers.push(`${name}: ${value}`);
    }
    upstreamSocket.write(
      `${request.method} ${request.url} HTTP/${request.httpVersion}\r\n${headers.join("\r\n")}\r\n\r\n`,
    );
    if (head.length > 0) upstreamSocket.write(head);
    clientSocket.pipe(upstreamSocket).pipe(clientSocket);
  });
  upstreamSocket.on("error", () => clientSocket.destroy());
  clientSocket.on("error", () => upstreamSocket.destroy());
});

await new Promise((resolve, reject) => {
  proxy.once("error", reject);
  proxy.listen(publicPort, "127.0.0.1", resolve);
});

const browser = await chromium.launch({
  headless: true,
  args: ["--no-proxy-server", `--host-resolver-rules=MAP ${publicHost} 127.0.0.1`],
});
const browserErrors = [];
const socketUrls = [];
const contexts = [];

const newContext = async (viewport = { width: 1440, height: 900 }) => {
  const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport });
  contexts.push(context);
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: publicOrigin });
  return context;
};

const observe = (page, label) => {
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(`${label} console: ${message.text()}`);
  });
  page.on("pageerror", (error) => browserErrors.push(`${label} page: ${error.message}`));
  page.on("websocket", (socket) => socketUrls.push(socket.url()));
};

const waitForDashboard = async (page) => {
  await page.locator(".app-footer").waitFor({ state: "visible" });
  try {
    await page.waitForFunction(
      () => document.querySelector(".app-footer")?.textContent?.includes("API connected"),
    );
  } catch (error) {
    throw new Error(
      `Dashboard did not connect; WebSocket metadata: ${JSON.stringify(websocketRequests)}`,
      { cause: error },
    );
  }
};

const assertViewport = async (page, label) => {
  const layout = await page.evaluate(() => ({
    viewportWidth: window.innerWidth,
    documentWidth: document.documentElement.scrollWidth,
    bodyWidth: document.body.scrollWidth,
    overflowSources: [...document.querySelectorAll("body *")]
      .map((element) => {
        const bounds = element.getBoundingClientRect();
        return {
          className: element.className?.baseVal ?? element.className ?? element.tagName,
          clientWidth: element.clientWidth,
          left: bounds.left,
          right: bounds.right,
          scrollWidth: element.scrollWidth,
          tag: element.tagName,
        };
      })
      .filter(({ clientWidth, left, right, scrollWidth }) => (
        left < -1 || right > window.innerWidth + 1 || scrollWidth > clientWidth + 1
      ))
      .slice(0, 20),
    outOfBounds: [...document.querySelectorAll(
      ".app-header, .header-controls, .pair-device-trigger, .panel, .app-footer, .pair-device-modal, .access-card",
    )]
      .filter((element) => {
        const style = getComputedStyle(element);
        return style.display !== "none" && style.visibility !== "hidden";
      })
      .map((element) => {
        const bounds = element.getBoundingClientRect();
        return { className: element.className, left: bounds.left, right: bounds.right };
      })
      .filter(({ left, right }) => left < -1 || right > window.innerWidth + 1),
  }));
  if (
    layout.documentWidth > layout.viewportWidth
    || layout.bodyWidth > layout.viewportWidth
    || layout.outOfBounds.length > 0
  ) {
    throw new Error(`${label} viewport overflow: ${JSON.stringify(layout)}`);
  }
};

const openInvitation = async (page) => {
  await page.locator(".pair-device-trigger").click();
  const dialog = page.locator(".pair-device-modal");
  await dialog.waitFor({ state: "visible" });
  await page.waitForFunction(
    () => getComputedStyle(document.querySelector(".pair-device-modal")).opacity === "1",
  );
  const input = dialog.getByLabel("One-time pairing link");
  await input.waitFor({ state: "visible" });
  return { dialog, input, link: await input.inputValue() };
};

try {
  const firstContext = await newContext();
  const firstPage = await firstContext.newPage();
  observe(firstPage, "first");
  await firstPage.route("**/api/v1/blender/releases", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      json: [{
        active: false,
        channel: "stable",
        downloaded: false,
        filename: "blender-0.0.0-linux-x64.tar.xz",
        installed: false,
        source: null,
        supported: false,
        version: "0.0.0",
      }],
    });
  });
  await firstPage.goto(publicOrigin, { waitUntil: "domcontentloaded" });
  await waitForDashboard(firstPage);

  const firstApiRequest = requestTargets.find((target) => target.startsWith("/api/v1/"));
  if (firstApiRequest !== "/api/v1/access/claim") {
    throw new Error(`Protected UI started before access bootstrap: ${firstApiRequest}`);
  }
  if (requestTargets.some((target) => target === "/api/v1/access/invitations")) {
    throw new Error("Pairing invitation was created without an explicit user action");
  }

  const firstControl = await firstPage.locator(".header-controls button").first().getAttribute("aria-label");
  if (firstControl !== "Pair another device") {
    throw new Error(`Share control is not first: ${firstControl}`);
  }
  await firstPage.locator(".pair-device-trigger").focus();
  await firstPage.waitForFunction(
    () => getComputedStyle(document.querySelector("#pair-device-tooltip")).opacity === "1",
  );
  const tooltipOpacity = await firstPage.locator("#pair-device-tooltip").evaluate(
    (element) => getComputedStyle(element).opacity,
  );
  if (tooltipOpacity !== "1") throw new Error("Keyboard tooltip is not visible");

  const mutation = await firstPage.evaluate(async () => {
    const response = await fetch("/api/v1/jobs", {
      method: "POST",
      headers: { "content-type": "application/json", "X-Render-Node-Request": "1" },
      body: "{}",
    });
    return { status: response.status, body: await response.text() };
  });
  if (mutation.status !== 422 || mutation.body.includes("Browser session")) {
    throw new Error(`Paired REST mutation failed: ${JSON.stringify(mutation)}`);
  }
  const artifactDownload = await firstPage.evaluate(async () => {
    const response = await fetch(
      "/api/v1/jobs/00000000-0000-0000-0000-000000000000/frames.zip",
    );
    return response.status;
  });
  if (artifactDownload === 401 || artifactDownload === 403) {
    throw new Error(`Cookie did not authorize a direct download route: ${artifactDownload}`);
  }
  await firstPage.locator(".version-button").click();
  const catalogToggle = firstPage.locator(".version-catalog-toggle");
  if (await catalogToggle.getAttribute("aria-expanded") === "false") await catalogToggle.click();
  const blenderDownloadResponse = firstPage.waitForResponse((response) => (
    response.request().method() === "POST"
    && response.url().endsWith("/api/v1/blender/releases/0.0.0/download")
  ));
  await firstPage.getByRole("button", { name: "Download", exact: true }).click();
  const blenderDownloadStatus = (await blenderDownloadResponse).status();
  if (blenderDownloadStatus === 401 || blenderDownloadStatus === 403) {
    throw new Error(`Paired Blender Download action was rejected: ${blenderDownloadStatus}`);
  }
  await firstPage.getByRole("button", { name: "Close version manager" }).click();
  await firstPage.waitForFunction(() => document.querySelector(".connection-pill")?.textContent?.includes("Node online"));
  if (!socketUrls.some((url) => url.includes("/api/v1/events"))) {
    throw new Error(`Application WebSocket did not connect: ${socketUrls}`);
  }

  await firstPage.evaluate(() => document.activeElement?.blur());
  await firstPage.waitForFunction(
    () => getComputedStyle(document.querySelector("#pair-device-tooltip")).opacity === "0",
  );
  await assertViewport(firstPage, "desktop dashboard");
  await firstPage.screenshot({ path: join(outputDirectory, "pairing-desktop.png") });
  await firstPage.locator(".pair-device-trigger").hover();
  await firstPage.screenshot({ path: join(outputDirectory, "pairing-tooltip-desktop.png") });

  const secondContext = await newContext();
  const secondPage = await secondContext.newPage();
  observe(secondPage, "second");
  await secondPage.goto(publicOrigin, { waitUntil: "domcontentloaded" });
  await secondPage.getByText("already paired with another browser").waitFor();
  const claimsBeforeIdle = requestTargets.filter((target) => target === "/api/v1/access/claim").length;
  await secondPage.waitForTimeout(750);
  const claimsAfterIdle = requestTargets.filter((target) => target === "/api/v1/access/claim").length;
  if (claimsAfterIdle !== claimsBeforeIdle) {
    throw new Error("Locked access screen retried claim without a user action");
  }
  await secondPage.getByRole("button", { name: "Retry" }).click();
  await secondPage.getByText("already paired with another browser").waitFor();
  const claimsAfterRetry = requestTargets.filter((target) => target === "/api/v1/access/claim").length;
  if (claimsAfterRetry !== claimsAfterIdle + 1) {
    throw new Error("Retry did not perform exactly one deliberate claim");
  }

  const firstInvitation = await openInvitation(firstPage);
  if (await firstPage.evaluate(() => !document.querySelector(".pair-device-modal")?.contains(document.activeElement))) {
    throw new Error("Pairing modal did not capture focus");
  }
  await firstPage.keyboard.press("Shift+Tab");
  if (await firstPage.evaluate(() => !document.querySelector(".pair-device-modal")?.contains(document.activeElement))) {
    throw new Error("Pairing modal focus trap failed");
  }
  await firstInvitation.dialog.getByRole("button", { name: "Copy link" }).click();
  await firstInvitation.dialog.getByText("Link copied").waitFor();
  const clipboard = await firstPage.evaluate(() => navigator.clipboard.readText());
  if (clipboard !== firstInvitation.link) throw new Error("Copy link did not update clipboard");
  await assertViewport(firstPage, "desktop pairing modal");
  await firstPage.screenshot({ path: join(outputDirectory, "pairing-modal-desktop.png") });
  await firstInvitation.dialog.getByRole("button", { name: "Close pairing dialog" }).click();

  await secondPage.goto(firstInvitation.link, { waitUntil: "domcontentloaded" });
  await waitForDashboard(secondPage);
  if (await secondPage.evaluate(() => window.location.hash)) {
    throw new Error("Pairing fragment remained in the address bar");
  }

  const replayContext = await newContext();
  const replayPage = await replayContext.newPage();
  observe(replayPage, "replay");
  await replayPage.goto(firstInvitation.link, { waitUntil: "domcontentloaded" });
  await replayPage.getByText("Could not connect to Render Node").waitFor();
  if (await replayPage.evaluate(() => window.location.hash)) {
    throw new Error("Used pairing fragment remained in the address bar");
  }

  await firstPage.setViewportSize({ width: 390, height: 844 });
  await firstPage.locator(".pair-device-trigger").waitFor({ state: "visible" });
  const mobileInvitation = await openInvitation(firstPage);
  if (mobileInvitation.link === firstInvitation.link) {
    throw new Error("Pairing modal retained a closed invitation token");
  }
  await assertViewport(firstPage, "mobile pairing modal");
  await firstPage.screenshot({ path: join(outputDirectory, "pairing-modal-mobile.png") });
  await mobileInvitation.dialog.getByRole("button", { name: "Close pairing dialog" }).click();
  await assertViewport(firstPage, "mobile dashboard");
  await firstPage.screenshot({ path: join(outputDirectory, "pairing-mobile.png") });
  await firstPage.setViewportSize({ width: 1440, height: 900 });

  for (let sessionCount = 3; sessionCount <= 5; sessionCount += 1) {
    const invitation = await openInvitation(firstPage);
    await invitation.dialog.getByRole("button", { name: "Close pairing dialog" }).click();
    const context = await newContext();
    const page = await context.newPage();
    observe(page, `paired-${sessionCount}`);
    await page.goto(invitation.link, { waitUntil: "domcontentloaded" });
    await waitForDashboard(page);
  }

  await firstPage.locator(".pair-device-trigger").click();
  const limitDialog = firstPage.locator(".pair-device-modal");
  await limitDialog.getByText("Device limit reached", { exact: true }).waitFor();
  await limitDialog.getByText("five browser sessions").waitFor();
  await firstPage.keyboard.press("Escape");
  await firstPage.locator(".pair-device-modal").waitFor({ state: "detached" });
  if (await firstPage.evaluate(() => document.activeElement?.getAttribute("aria-label")) !== "Pair another device") {
    throw new Error("Pairing trigger did not regain focus after Escape");
  }

  if (requestTargets.some((target) => target.includes("pair="))) {
    throw new Error(`Pairing token leaked into proxy request targets: ${requestTargets}`);
  }
  const unexpectedErrors = browserErrors.filter(
    (error) => !/status of (400|404|409|422)/.test(error),
  );
  if (unexpectedErrors.length > 0) {
    throw new Error(`Browser errors:\n${unexpectedErrors.join("\n")}`);
  }

  console.log(`Pairing browser acceptance passed. Screenshots: ${outputDirectory}`);
} finally {
  await Promise.all(contexts.map((context) => context.close()));
  await browser.close();
  await new Promise((resolve) => proxy.close(resolve));
  await rm(certificateDirectory, { recursive: true, force: true });
}
