import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { env as processEnv } from "node:process";

const repositoryRoot = fileURLToPath(new URL("..", import.meta.url));

export const apiProxyOptions = (backendUrl) => ({
  target: backendUrl,
  ws: true,
  changeOrigin: false,
  rewriteWsOrigin: false,
});

export const devServerOptions = (apiProxy) => ({
  host: "0.0.0.0",
  allowedHosts: true,
  port: 5173,
  strictPort: true,
  proxy: { "/api": apiProxy },
});

export default defineConfig(({ mode }) => {
  const environment = { ...loadEnv(mode, repositoryRoot, ""), ...processEnv };
  const backendUrl = environment.RENDER_NODE_BACKEND_URL ?? "http://127.0.0.1:8000";
  const apiProxy = apiProxyOptions(backendUrl);

  return {
    plugins: [react()],
    server: devServerOptions(apiProxy),
    preview: {
      host: "0.0.0.0",
      port: 4173,
      proxy: {
        "/api": apiProxy,
      },
    },
  };
});
