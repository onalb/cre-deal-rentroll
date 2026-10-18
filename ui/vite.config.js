import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8002",
        timeout: 120_000,
        proxyTimeout: 120_000,
      },
    },
  },
});
