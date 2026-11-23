// The document drawer: slides in from the right over the graph when a document is opened, with
// back/forward, close, and a quiet secondary row for the sourcing key and the Markdown source toggle.
// Esc or the close button closes it; drag its left edge to resize.

import { useEffect, useState, type PointerEvent } from "react";
import type { GraphNode } from "../api/types";
import { FormatDot, SourcingLegend } from "../nav/chips";
import { useNav } from "../nav/NavContext";
import { DocumentViewer } from "../viewers/DocumentViewer";

const MIN_WIDTH = 380;
const MIN_GRAPH = 240;

const clampWidth = (w: number) => Math.round(Math.max(MIN_WIDTH, Math.min(window.innerWidth - MIN_GRAPH, w)));

export function defaultDrawerWidth(): number {
  return clampWidth(window.innerWidth * 0.48);
}

export function Drawer({ width, onWidthChange }: { width: number; onWidthChange(width: number): void }) {
  const nav = useNav();
  const entry = nav.current;
  const node = entry ? nav.nodes.get(entry.ref.version) : undefined;
  const [sourceView, setSourceView] = useState(false);
  const [resizing, setResizing] = useState(false);

  useCloseOnEscape(nav.open, nav.close);

  useEffect(() => {
    const onResize = () => onWidthChange(clampWidth(width));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [width, onWidthChange]);

  const startResize = (e: PointerEvent<HTMLDivElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    setResizing(true);
  };
  const resize = (e: PointerEvent<HTMLDivElement>) => {
    if (resizing) onWidthChange(clampWidth(window.innerWidth - e.clientX));
  };

  return (
    <aside
      className={`drawer${nav.open ? " open" : ""}${resizing ? " resizing" : ""}`}
      style={{ width }}
      aria-hidden={!nav.open}
    >
      <div
        className="drawer-grip"
        onPointerDown={startResize}
        onPointerMove={resize}
        onPointerUp={() => setResizing(false)}
        title="Drag to resize"
      />
      {entry && node && node.format !== null && (
        <>
          <DrawerHeader node={node} />
          <DrawerSubheader node={node} sourceView={sourceView} onSourceViewChange={setSourceView} />
          {/* Keyed by version: a new document starts from fresh state rather than the last one's data. */}
          <DocumentViewer key={entry.ref.version} entry={entry} format={node.format} sourceView={sourceView} />
        </>
      )}
    </aside>
  );
}

/** Close the drawer on Esc while it is open. */
function useCloseOnEscape(open: boolean, close: () => void) {
  useEffect(() => {
    if (!open) return;
    // The citation popover handles Esc first (in the capture phase) and marks it handled.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close]);
}

function DrawerHeader({ node }: { node: GraphNode }) {
  const nav = useNav();
  return (
    <header className="drawer-head">
      <div className="drawer-title">
        <FormatDot format={node.format} />
        <h1 title={node.title ?? node.name}>{node.title ?? node.name}</h1>
        {node.title && <code className="drawer-name">{node.name}</code>}
      </div>
      <div className="drawer-actions">
        <button className="icon" onClick={nav.back} disabled={!nav.canBack} title="Back" aria-label="Back">
          <Chevron dir="left" />
        </button>
        <button className="icon" onClick={nav.forward} disabled={!nav.canForward} title="Forward" aria-label="Forward">
          <Chevron dir="right" />
        </button>
        <button className="icon" onClick={nav.close} title="Close (Esc)" aria-label="Close">
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden>
            <path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </button>
      </div>
    </header>
  );
}

function DrawerSubheader({
  node,
  sourceView,
  onSourceViewChange,
}: {
  node: GraphNode;
  sourceView: boolean;
  onSourceViewChange(sourceView: boolean): void;
}) {
  return (
    <div className="drawer-sub">
      {node.format === "pdf" ? <span>Boxes mark regions other documents cite.</span> : <SourcingLegend />}
      {node.format === "markdown" && (
        <label className="toggle-source">
          <input type="checkbox" checked={sourceView} onChange={(e) => onSourceViewChange(e.target.checked)} /> source
        </label>
      )}
    </div>
  );
}

function Chevron({ dir }: { dir: "left" | "right" }) {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden>
      <path
        d={dir === "left" ? "M8.5 3L4.5 7l4 4" : "M5.5 3l4 4-4 4"}
        stroke="currentColor"
        strokeWidth="1.5"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
