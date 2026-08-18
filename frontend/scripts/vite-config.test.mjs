import assert from "node:assert/strict";
import test from "node:test";

import { apiProxyOptions, devServerOptions } from "../vite.config.js";

test("development uses Vite browser-host WebSocket defaults", () => {
  const server = devServerOptions(apiProxyOptions("http://127.0.0.1:8000"));

  assert.equal(server.ws, undefined);
  assert.equal(server.hmr, undefined);
  assert.equal(server.host, "0.0.0.0");
  assert.equal(server.allowedHosts, true);
});

test("API proxy preserves browser Host and Origin for HTTP and WebSocket", () => {
  const proxy = apiProxyOptions("http://127.0.0.1:8000");

  assert.equal(proxy.target, "http://127.0.0.1:8000");
  assert.equal(proxy.ws, true);
  assert.equal(proxy.changeOrigin, false);
  assert.equal(proxy.rewriteWsOrigin, false);
  assert.equal(proxy.headers, undefined);
});

test("browser proxy never injects the private backend Bearer", () => {
  const proxy = apiProxyOptions("http://127.0.0.1:8000");

  assert.equal(proxy.headers, undefined);
  assert.equal(JSON.stringify(devServerOptions(proxy)).includes("Authorization"), false);
});
