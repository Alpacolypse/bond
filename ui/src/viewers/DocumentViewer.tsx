// The drawer's body: loads a version, picks the per-format viewer, resolves the navigation target,
// scrolls to and flashes it, and opens the citation popover on clicks.

import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type MouseEvent, type RefObject } from "react";
import { useApi } from "../api/ApiContext";
import type { Api } from "../api/client";
import type { Annotations, Citation, Format, Locator } from "../api/types";
import { useAsync } from "../api/useAsync";
import { CitationPopover, type PopoverState } from "../nav/CitationPopover";
import { markdownMarks } from "../nav/marks";
import type { NavEntry } from "../nav/NavContext";
import { JsonViewer } from "./JsonViewer";
import { MarkdownViewer } from "./MarkdownViewer";
import type { MarkClick } from "./MarkedText";
import { TableViewer } from "./TableViewer";

// pdf.js is large: load it only when a PDF is opened.
const PdfViewer = lazy(() => import("./PdfViewer").then((m) => ({ default: m.PdfViewer })));

const NO_ANNOTATIONS: Annotations = { units: [], gaps: [] };

/** The delimiter of each table format: fixed by the format, as in bond.formats. */
const DELIMITER: Partial<Record<Format, string>> = { csv: ",", tsv: "\t" };

/** A version's annotations; PDFs have none (API.md), so they are not asked for. */
function loadAnnotations(api: Api, version: string, format: Format): Promise<Annotations> {
  return format === "pdf" ? Promise.resolve(NO_ANNOTATIONS) : api.annotations(version);
}

export function DocumentViewer({ entry, format, sourceView }: { entry: NavEntry; format: Format; sourceView: boolean }) {
  const api = useApi();
  const version = entry.ref.version;
  const detail = useAsync(() => api.version(version), [api, version]);
  const citations = useAsync(() => api.citations(version), [api, version]);
  const annotations = useAsync(() => loadAnnotations(api, version, format), [api, version, format]);
  const [popover, setPopover] = useState<PopoverState | null>(null);
  const closePopover = useCallback(() => setPopover(null), []);
  const body = useRef<HTMLDivElement>(null);

  useEffect(() => setPopover(null), [entry]);

  const target = entry.ref.locator;
  const content = detail.data?.content ?? null;
  const marked = annotations.data ?? NO_ANNOTATIONS;
  const ready = detail.data !== undefined && citations.data !== undefined;
  useFlashTarget(body, entry.nonce, ready, target?.type === "version");

  const openPopover = (direction: PopoverState["direction"], cited: Citation[], e: MouseEvent) =>
    setPopover({ x: e.clientX, y: e.clientY, citations: cited, direction });
  const onMark: MarkClick = (m, e) => openPopover("outbound", m.citations, e);

  const error = detail.error ?? citations.error ?? annotations.error;
  return (
    <div className="drawer-body" ref={body}>
      {error && <div className="error">{error.message}</div>}
      {format === "markdown" && content !== null && citations.data && (
        <MarkdownDocument
          content={content}
          citations={citations.data}
          annotations={marked}
          target={target}
          onMark={onMark}
          sourceView={sourceView}
        />
      )}
      {format === "json" && content !== null && citations.data && (
        <JsonViewer content={content} citations={citations.data} annotations={marked} target={target} onMark={onMark} />
      )}
      {DELIMITER[format] !== undefined && content !== null && citations.data && (
        <TableViewer
          content={content}
          delimiter={DELIMITER[format]}
          citations={citations.data}
          annotations={marked}
          target={target}
          onMark={onMark}
        />
      )}
      {format === "pdf" && (
        <Suspense fallback={<div className="muted">Loading PDF viewer…</div>}>
          <PdfViewer
            version={version}
            target={target?.type === "pdf" ? target : null}
            onBox={(cited, e) => openPopover("inbound", cited, e)}
          />
        </Suspense>
      )}
      {popover && <CitationPopover state={popover} onClose={closePopover} />}
    </div>
  );
}

function MarkdownDocument({
  content,
  citations,
  annotations,
  target,
  onMark,
  sourceView,
}: {
  content: string;
  citations: readonly Citation[];
  annotations: Annotations;
  target: Locator | null;
  onMark: MarkClick;
  sourceView: boolean;
}) {
  const marks = useMemo(
    () => markdownMarks(content, citations, annotations, target),
    [content, citations, annotations, target],
  );
  return <MarkdownViewer content={content} marks={marks} onMark={onMark} sourceView={sourceView} />;
}

/** After each navigation, scroll the target into view and flash it (or the whole document). */
function useFlashTarget(container: RefObject<HTMLDivElement | null>, nonce: number, ready: boolean, wholeVersion: boolean) {
  useEffect(() => {
    const root = container.current;
    if (!root || !ready) return;
    if (wholeVersion) {
      root.scrollTop = 0;
      return;
    }
    // Targets can appear late (PDF pages render asynchronously), so poll briefly.
    let frame = 0;
    let tries = 0;
    const find = () => {
      const targets = [...root.querySelectorAll("[data-target]")].filter((el) => el instanceof HTMLElement);
      if (targets.length > 0) {
        targets[0]?.scrollIntoView({ block: "center", behavior: "smooth" });
        flash(targets);
      } else if (tries++ < 180) {
        frame = requestAnimationFrame(find);
      }
    };
    frame = requestAnimationFrame(find);
    return () => cancelAnimationFrame(frame);
  }, [container, nonce, ready, wholeVersion]);
}

/** (Re)start the flash animation on each element. */
function flash(elements: readonly HTMLElement[]) {
  for (const el of elements) {
    el.classList.remove("flash");
    void el.offsetWidth; // force a reflow so re-adding the class restarts the animation
    el.classList.add("flash");
  }
}
