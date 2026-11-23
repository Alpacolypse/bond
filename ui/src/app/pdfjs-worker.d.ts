// pdf.js's worker as a module: imported by the MCP app to run it on the main thread.
declare module "pdfjs-dist/build/pdf.worker.min.mjs" {
  export const WorkerMessageHandler: unknown;
}
