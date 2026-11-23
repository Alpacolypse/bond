// The MCP app view's entry (app.html), built into one self-contained HTML file that Bond's MCP server
// serves as its ui:// resource: the host renders it in a sandboxed iframe with no network access.

import "./polyfills";
// pdf.js runs its worker on the main thread here: the sandbox may refuse worker and blob URLs, and
// pdf.js uses `globalThis.pdfjsWorker`'s handler in-thread when it is set (vite.app.config.ts stubs
// out the worker URL that PdfViewer would otherwise load).
import * as pdfjsWorker from "pdfjs-dist/build/pdf.worker.min.mjs";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Host } from "./host";
import { McpView } from "./McpView";
import "../styles.css";
import "./app.css";

(globalThis as { pdfjsWorker?: unknown }).pdfjsWorker = pdfjsWorker;

const host = new Host();
void host.connect();

const root = document.getElementById("root");
if (!root) throw new Error("app.html has no #root element");

createRoot(root).render(
  <StrictMode>
    <McpView host={host} />
  </StrictMode>,
);
