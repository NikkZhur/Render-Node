import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { env } from "node:process";

const backendUrl = env.RENDER_NODE_BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: backendUrl, ws: true },
    },
  },
  preview: {
    host: "0.0.0.0",
    port: 4173,
    proxy: {
      "/api": { target: backendUrl, ws: true },
    },
  },
});
