import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { request as httpRequest } from "node:http";
import { createServer } from "node:https";
import { connect } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const publicHost = "panel.arbitrary-cloud.test";
const publicPort = 9443;
const upstreamHost = "127.0.0.1";
const upstreamPort = 5173;
const outputDirectory = process.env.QA_OUTPUT_DIR ?? await mkdtemp(join(tmpdir(), "render-node-qa-"));
const certificateDirectory = await mkdtemp(join(tmpdir(), "render-node-cert-"));
const keyPath = join(certificateDirectory, "key.pem");
const certificatePath = join(certificateDirectory, "certificate.pem");

execFileSync(
  "openssl",
  [
    "req",
    "-x509",
    "-newkey",
    "rsa:2048",
    "-nodes",
    "-keyout",
    keyPath,
    "-out",
    certificatePath,
    "-days",
    "1",
    "-subj",
    `/CN=${publicHost}`,
    "-addext",
    `subjectAltName=DNS:${publicHost}`,
  ],
  { stdio: "ignore" },
);

const proxy = createServer(
  {
    key: await readFile(keyPath),
    cert: await readFile(certificatePath),
  },
  (request, response) => {
    const upstreamRequest = httpRequest(
      {
        host: upstreamHost,
        port: upstreamPort,
        path: request.url,
        method: request.method,
        headers: request.headers,
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
  const upstreamSocket = connect(upstreamPort, upstreamHost, () => {
    const headers = [];
    for (let index = 0; index < request.rawHeaders.length; index += 2) {
      headers.push(`${request.rawHeaders[index]}: ${request.rawHeaders[index + 1]}`);
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
const context = await browser.newContext({
  ignoreHTTPSErrors: true,
  viewport: { width: 1440, height: 900 },
});
const page = await context.newPage();
const browserErrors = [];
const socketUrls = [];
const apiResponses = [];
const failedRequests = [];
page.on("console", (message) => {
  if (message.type() === "error") browserErrors.push(`console: ${message.text()}`);
});
page.on("pageerror", (error) => browserErrors.push(`page: ${error.message}`));
page.on("websocket", (socket) => socketUrls.push(socket.url()));
page.on("response", (response) => {
  if (response.url().includes("/api/")) {
    apiResponses.push(`${response.status()} ${response.url()}`);
  }
});
page.on("requestfailed", (request) => {
  failedRequests.push(`request: ${request.url()} ${request.failure()?.errorText ?? "failed"}`);
});

const assertViewport = async (label) => {
  const layout = await page.evaluate(() => ({
    viewportWidth: window.innerWidth,
    documentWidth: document.documentElement.scrollWidth,
    bodyWidth: document.body.scrollWidth,
    outOfBounds: [...document.querySelectorAll(".app-header, .panel, .app-footer")]
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

try {
  const targetUrl = `https://${publicHost}:${publicPort}`;
  await page.goto(targetUrl, { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);
  const footerText = await page.locator(".app-footer").textContent();
  if (!footerText?.includes("API connected")) {
    throw new Error(
      `API did not connect. Footer: ${footerText}; sockets: ${socketUrls}; `
      + `responses: ${apiResponses}; errors: ${browserErrors}; failed: ${failedRequests}`,
    );
  }

  const mutation = await page.evaluate(async () => {
    const response = await fetch("/api/v1/jobs", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "X-Render-Node-Request": "1",
      },
      body: "{}",
    });
    return { status: response.status, body: await response.text() };
  });
  if (mutation.status !== 422 || mutation.body.includes("Browser request context is invalid")) {
    throw new Error(`Same-origin POST was rejected: ${JSON.stringify(mutation)}`);
  }

  await page.waitForTimeout(500);
  const applicationSocket = socketUrls.find((url) => url.includes("/api/v1/events"));
  if (!applicationSocket) throw new Error(`Application WebSocket did not connect: ${socketUrls}`);
  if (socketUrls.some((url) => /localhost|127\.0\.0\.1/.test(new URL(url).hostname))) {
    throw new Error(`Browser opened a loopback WebSocket: ${socketUrls}`);
  }
  if (!socketUrls.some((url) => new URL(url).hostname === publicHost)) {
    throw new Error(`HMR did not use the external browser host: ${socketUrls}`);
  }

  await assertViewport("desktop");
  const desktopScreenshot = join(outputDirectory, "same-origin-desktop.png");
  await page.screenshot({ path: desktopScreenshot });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(250);
  await assertViewport("mobile");
  const mobileScreenshot = join(outputDirectory, "same-origin-mobile.png");
  await page.screenshot({ path: mobileScreenshot });

  const unexpectedBrowserErrors = browserErrors.filter(
    (error) => !error.includes("status of 422 (Unprocessable Entity)"),
  );
  if (unexpectedBrowserErrors.length > 0) {
    throw new Error(`Browser errors:\n${unexpectedBrowserErrors.join("\n")}`);
  }
  console.log(
    `Same-origin proxy smoke passed. Screenshots: ${desktopScreenshot}, ${mobileScreenshot}`,
  );
} finally {
  await browser.close();
  await new Promise((resolve) => proxy.close(resolve));
  await rm(certificateDirectory, { recursive: true, force: true });
}
