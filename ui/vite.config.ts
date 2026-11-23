import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://localhost:8000" },
  },
  // Into the Python package, which serves it (bond view) and ships it in the wheel.
  build: { outDir: "../src/bond/static/viewer", emptyOutDir: true, chunkSizeWarningLimit: 700 },
  test: { include: ["src/**/*.test.ts"] },
});
