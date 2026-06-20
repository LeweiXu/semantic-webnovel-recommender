import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies the API to the FastAPI backend on :8000. In production the
// backend serves the built files itself, so these proxies are dev-only.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
