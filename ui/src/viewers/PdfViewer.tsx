// PDF pages rendered with pdf.js, overlaid with a box for every region other documents cite
// (from /api/cited-by) and for the current navigation target.

import { getDocument, GlobalWorkerOptions, type PDFPageProxy } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { useEffect, useMemo, useRef, type MouseEvent } from "react";
import { useApi } from "../api/ApiContext";
import type { Api } from "../api/client";
import { locatorKey } from "../api/locators";
import type { Citation, PdfLocator } from "../api/types";
import { useAsync } from "../api/useAsync";
import { useElementWidth } from "../util/useElementSize";
import { citedBoxes, type CitedBox } from "./pdfBoxes";

GlobalWorkerOptions.workerSrc = workerUrl;

export type BoxClick = (citations: Citation[], e: MouseEvent) => void;

/** Every page of a version's PDF. The document is destroyed when `signal` aborts. */
async function loadPages(api: Api, version: string, signal: AbortSignal): Promise<PDFPageProxy[]> {
  const task = getDocument(api.blobData ? { data: await api.blobData(version) } : { url: await api.blobUrl(version) });
  const destroy = () => void task.destroy();
  if (signal.aborted) destroy(); // superseded while the URL was resolving
  else signal.addEventListener("abort", destroy, { once: true });
  const pdf = await task.promise;
  return Promise.all(Array.from({ length: pdf.numPages }, (_, i) => pdf.getPage(i + 1)));
}

export function PdfViewer({ version, target, onBox }: { version: string; target: PdfLocator | null; onBox: BoxClick }) {
  const api = useApi();
  const [ref, width] = useElementWidth<HTMLDivElement>();
  const pages = useAsync((signal) => loadPages(api, version, signal), [api, version]);
  const citedBy = useAsync(() => api.citedBy(version), [api, version]);
  const boxes = useMemo(() => citedBoxes(citedBy.data ?? [], version), [citedBy.data, version]);

  return (
    <div ref={ref} className="pdf">
      {pages.error && <div className="error">Could not load PDF: {pages.error.message}</div>}
      {pages.loading && <div className="muted">Loading PDF…</div>}
      {width > 0 &&
        pages.data?.map((page, i) => (
          <PdfPage
            key={i}
            page={page}
            width={width}
            boxes={boxes.filter((b) => b.locator.page === i)}
            target={target?.page === i ? target : null}
            onBox={onBox}
          />
        ))}
    </div>
  );
}

function PdfPage({
  page,
  width,
  boxes,
  target,
  onBox,
}: {
  page: PDFPageProxy;
  width: number;
  boxes: CitedBox[];
  target: PdfLocator | null;
  onBox: BoxClick;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const base = page.getViewport({ scale: 1 });
  const height = (width * base.height) / base.width;

  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const ratio = window.devicePixelRatio || 1;
    const viewport = page.getViewport({ scale: (width / base.width) * ratio });
    el.width = Math.floor(viewport.width);
    el.height = Math.floor(viewport.height);
    const task = page.render({ canvas: el, viewport });
    task.promise.catch(() => {
      // cancelled by a newer render
    });
    return () => task.cancel();
  }, [page, width, base.width]);

  return (
    <div className="pdf-page" style={{ width, height }}>
      <canvas ref={canvas} style={{ width, height }} />
      {boxes.map((b) => (
        <div
          key={locatorKey(b.locator)}
          className="pdf-box"
          style={boxStyle(b.locator)}
          title={boxTitle(b)}
          onClick={(e) => onBox(b.citations, e)}
        />
      ))}
      {target && <div className="pdf-box pdf-target" data-target style={boxStyle(target)} />}
      <div className="pdf-page-label">page {page.pageNumber}</div>
    </div>
  );
}

function boxTitle({ citations }: CitedBox): string {
  const kinds = [...new Set(citations.map((c) => c.kind))];
  return `${citations.length} citation(s): ${kinds.join(", ")}`;
}

/** A normalized bbox as percentage offsets within its page. */
function boxStyle({ bbox: [x0, y0, x1, y1] }: PdfLocator) {
  return { left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` };
}
