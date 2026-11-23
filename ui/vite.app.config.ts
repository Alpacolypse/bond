// The MCP app view (app.html): one self-contained HTML file, every script and style inlined, for
// hosts that render it in a sandbox without network access. `npm run build` builds it after the
// viewer (vite.config.ts), into src/bond/static/app/index.html.

import { renameSync } from "node:fs";
import { join } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

const WORKER_URL = "pdfjs-dist/build/pdf.worker.min.mjs?url";
const NO_WORKER_URL = "\0bond-no-worker-url";

/** PdfViewer's worker URL, emptied: the app runs pdf.js's worker on the main thread (src/app/main.tsx). */
const noWorkerUrl: Plugin = {
  name: "bond-no-worker-url",
  enforce: "pre",
  resolveId: (id) => (id === WORKER_URL ? NO_WORKER_URL : null),
  load: (id) => (id === NO_WORKER_URL ? 'export default "";' : null),
};

/** The page is the app's index.html, which the server reads (renamed once written: Rolldown's bundle can't be re-keyed). */
const asIndexHtml: Plugin = {
  name: "bond-app-index-html",
  writeBundle({ dir }) {
    if (dir) renameSync(join(dir, "app.html"), join(dir, "index.html"));
  },
};

export default defineConfig({
  plugins: [react(), noWorkerUrl, viteSingleFile({ removeViteModuleLoader: true }), asIndexHtml],
  build: {
    outDir: "../src/bond/static/app",
    emptyOutDir: true,
    chunkSizeWarningLimit: 4000,
    rollupOptions: { input: "app.html" },
  },
});
