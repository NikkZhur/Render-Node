import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { env as processEnv } from "node:process";

const repositoryRoot = fileURLToPath(new URL("..", import.meta.url));
const loopbackHosts = new Set(["localhost", "127.0.0.1", "::1"]);

const parseOrigins = (rawOrigins = "") => {
  const value = rawOrigins.trim();
  if (!value) return [];
  const origins = value.startsWith("[") ? JSON.parse(value) : value.split(",");
  return origins.map((origin) => new URL(String(origin).trim()));
};

export const resolveDevPublicOrigin = (environment) => {
  const allowedOrigins = parseOrigins(environment.RENDER_NODE_ALLOWED_ORIGINS);
  const explicitOrigin = environment.RENDER_NODE_DEV_PUBLIC_ORIGIN;
  if (explicitOrigin) {
    const parsed = new URL(explicitOrigin);
    if (!allowedOrigins.some((origin) => origin.origin === parsed.origin)) {
      throw new Error("RENDER_NODE_DEV_PUBLIC_ORIGIN must be present in RENDER_NODE_ALLOWED_ORIGINS");
    }
    return parsed;
  }
  const externalOrigins = allowedOrigins.filter((origin) => !loopbackHosts.has(origin.hostname));
  return externalOrigins.length === 1 ? externalOrigins[0] : null;
};

export const webSocketOptionsForOrigin = (origin) => ({
  protocol: origin.protocol === "https:" ? "wss" : "ws",
  host: origin.hostname,
  port: 5173,
  clientPort: Number(origin.port || (origin.protocol === "https:" ? 443 : 80)),
});

export default defineConfig(({ mode }) => {
  const environment = { ...loadEnv(mode, repositoryRoot, ""), ...processEnv };
  const backendUrl = environment.RENDER_NODE_BACKEND_URL ?? "http://127.0.0.1:8000";
  const backendAuthToken = environment.RENDER_NODE_BACKEND_AUTH_TOKEN;
  const publicOrigin = resolveDevPublicOrigin(environment);
  const apiProxy = {
    target: backendUrl,
    ws: true,
    ...(backendAuthToken
      ? { headers: { Authorization: `Bearer ${backendAuthToken}` } }
      : {}),
  };

  return {
    plugins: [react()],
    server: {
      host: "0.0.0.0",
      port: 5173,
      strictPort: true,
      ...(publicOrigin ? { ws: webSocketOptionsForOrigin(publicOrigin) } : {}),
      proxy: {
        "/api": apiProxy,
      },
    },
    preview: {
      host: "0.0.0.0",
      port: 4173,
      proxy: {
        "/api": apiProxy,
      },
    },
  };
});
