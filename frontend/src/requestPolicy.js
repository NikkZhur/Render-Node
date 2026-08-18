export const BROWSER_MUTATION_HEADER = "X-Render-Node-Request";
export const BROWSER_WEBSOCKET_PROTOCOL = "render-node-browser-v1";

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export function withBrowserRequestPolicy(options = {}) {
  const method = String(options.method ?? "GET").toUpperCase();
  const headers = new Headers(options.headers ?? {});
  if (UNSAFE_METHODS.has(method)) headers.set(BROWSER_MUTATION_HEADER, "1");
  return {
    ...options,
    credentials: "same-origin",
    headers,
  };
}
