import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  bootstrapBrowserAccess,
  createPairingUrl,
  hasPairingFragment,
  takePairingToken,
} from "../src/accessBootstrap.js";
import {
  BROWSER_MUTATION_HEADER,
  BROWSER_WEBSOCKET_PROTOCOL,
  withBrowserRequestPolicy,
} from "../src/requestPolicy.js";

test("unsafe requests receive the browser marker while GET stays unmarked", () => {
  const get = withBrowserRequestPolicy({ headers: { accept: "application/json" } });
  const post = withBrowserRequestPolicy({
    method: "POST",
    headers: { "content-type": "application/json" },
  });

  assert.equal(get.credentials, "same-origin");
  assert.equal(get.headers.has(BROWSER_MUTATION_HEADER), false);
  assert.equal(post.headers.get(BROWSER_MUTATION_HEADER), "1");
  assert.equal(post.headers.get("content-type"), "application/json");
});

test("pairing fragment is removed before its token is sent in a JSON API call", async () => {
  const events = [];
  const browser = {
    location: {
      hash: "#pair=opaque_token_12345678901234567890123456789012",
      pathname: "/",
      search: "",
    },
    history: {
      state: { preserved: true },
      replaceState(state, title, url) {
        events.push(["history", state, title, url]);
      },
    },
  };
  const accessApi = {
    claim: async () => events.push(["claim"]),
    pair: async (token) => events.push(["pair", token]),
  };

  await bootstrapBrowserAccess(accessApi, browser);

  assert.deepEqual(events[0], ["history", { preserved: true }, "", "/"]);
  assert.deepEqual(events[1], ["pair", "opaque_token_12345678901234567890123456789012"]);
});

test("ordinary bootstrap claims and duplicate pairing fragments are rejected locally", async () => {
  let claimed = 0;
  await bootstrapBrowserAccess(
    {
      claim: async () => { claimed += 1; },
      pair: async () => assert.fail("pair should not run"),
    },
    { location: { hash: "", pathname: "/", search: "" }, history: {} },
  );
  assert.equal(claimed, 1);

  const calls = [];
  const duplicate = takePairingToken(
    { hash: "#pair=one&pair=two", pathname: "/panel", search: "?mode=full" },
    { state: null, replaceState: (...args) => calls.push(args) },
  );
  assert.equal(duplicate, "");
  assert.deepEqual(calls, [[null, "", "/panel?mode=full"]]);
  assert.equal(hasPairingFragment({ hash: "#pair=one" }), true);
  assert.equal(hasPairingFragment({ hash: "#section=jobs" }), false);
});

test("pairing URL uses only current origin and a fragment", () => {
  assert.equal(
    createPairingUrl("opaque-token", "https://panel.example:8443"),
    "https://panel.example:8443/#pair=opaque-token",
  );
});

test("browser WebSocket uses the explicit compatibility protocol", async () => {
  assert.equal(BROWSER_WEBSOCKET_PROTOCOL, "render-node-browser-v1");
  const source = await readFile(new URL("../src/realtime.js", import.meta.url), "utf8");
  assert.match(source, /new WebSocket\(eventsApi\.url\(\), BROWSER_WEBSOCKET_PROTOCOL\)/);
});

test("share control is first in header controls and keeps the accessible contract", async () => {
  const source = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
  const controls = source.slice(
    source.indexOf('<div className="header-controls">'),
    source.indexOf('<nav className="header-summary"'),
  );
  assert.match(controls, /aria-label="Pair another device"/);
  assert.match(controls, /<Icon name="share"/);
  assert.match(
    controls,
    /Pair another device — create a one-time link that grants access to this Render Node\./,
  );
  assert.ok(source.indexOf("pair-device-trigger") < source.indexOf("header-summary"));
});
