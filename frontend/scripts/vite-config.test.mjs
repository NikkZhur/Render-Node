import assert from "node:assert/strict";
import test from "node:test";

import { resolveDevPublicOrigin, webSocketOptionsForOrigin } from "../vite.config.js";

test("local development does not override the browser HMR host", () => {
  assert.equal(
    resolveDevPublicOrigin({
      RENDER_NODE_ALLOWED_ORIGINS: "http://localhost:5173,http://127.0.0.1:5173",
    }),
    null,
  );
});

test("a sole remote allowlisted origin keeps HMR away from browser localhost", () => {
  const origin = resolveDevPublicOrigin({
    RENDER_NODE_ALLOWED_ORIGINS:
      "http://localhost:5173,https://pod-5173.proxy.runpod.net",
  });

  assert.equal(origin?.origin, "https://pod-5173.proxy.runpod.net");
  assert.deepEqual(webSocketOptionsForOrigin(origin), {
    protocol: "wss",
    host: "pod-5173.proxy.runpod.net",
    port: 5173,
    clientPort: 443,
  });
});

test("an explicit HMR origin must be in the backend allowlist", () => {
  assert.throws(
    () =>
      resolveDevPublicOrigin({
        RENDER_NODE_ALLOWED_ORIGINS: "https://allowed.example.com",
        RENDER_NODE_DEV_PUBLIC_ORIGIN: "https://different.example.com",
      }),
    /must be present/,
  );
});
