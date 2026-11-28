// The list of citations covering a clicked region. Outbound (a claim in this document): choose a
// support to go to it. Inbound (a PDF region cited from elsewhere): choose a citing claim.

import { useEffect, useRef, type RefObject } from "react";
import { describeLocator } from "../api/locators";
import type { Citation, Locator } from "../api/types";
import { clip, FormatDot, KindChip } from "./chips";
import { useNav } from "./NavContext";

export type PopoverState = {
  x: number;
  y: number;
  citations: Citation[];
  direction: "outbound" | "inbound";
};

const WIDTH = 380;

/** Go somewhere from the popover (which closes it). */
type Go = (version: string, locator: Locator) => void;

export function CitationPopover({ state, onClose }: { state: PopoverState; onClose(): void }) {
  const nav = useNav();
  const ref = useRef<HTMLDivElement>(null);
  useDismiss(ref, onClose);

  const go: Go = (version, locator) => {
    onClose();
    nav.navigate(version, locator);
  };

  const left = Math.max(12, Math.min(state.x - 24, window.innerWidth - WIDTH - 16));
  const top = Math.min(state.y + 14, window.innerHeight - 220);
  const inbound = state.direction === "inbound";

  return (
    <div ref={ref} className="popover" style={{ left, top, width: WIDTH }}>
      {state.citations.length === 0 && <div className="pop-empty">Undeclared: no citation covers this text.</div>}
      {state.citations.map((c) => (
        <div key={c.id} className="pop-citation">
          <div className="pop-head">
            <KindChip kind={c.kind} />
            {inbound && <span>from {nav.name(c.owner_version)}</span>}
            {c.producer !== null && <span className="pop-attrs">by {c.producer}</span>}
          </div>
          {c.kind === "paraphrase" && <div className="pop-note">Paraphrase: semantic support is not evaluated.</div>}
          {inbound ? <CitingClaim citation={c} go={go} /> : <Supports citation={c} go={go} />}
        </div>
      ))}
    </div>
  );
}

/** Close on Esc or on a mouse press outside `ref`. */
function useDismiss(ref: RefObject<HTMLElement | null>, onClose: () => void) {
  useEffect(() => {
    // Capture phase, so Esc closes the popover before the drawer sees it (and not both).
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      onClose();
    };
    const onDown = (e: MouseEvent) => {
      const el = ref.current;
      if (el && e.target instanceof Node && !el.contains(e.target)) onClose();
    };
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("mousedown", onDown);
    };
  }, [ref, onClose]);
}

/** Inbound: the claim that cites this region, as a link to it. */
function CitingClaim({ citation: c, go }: { citation: Citation; go: Go }) {
  return (
    <button className="pop-link" onClick={() => go(c.owner_version, c.claim)}>
      <span className="pop-quote">{c.claim_text ? `“${clip(c.claim_text)}”` : describeLocator(c.claim)}</span>
    </button>
  );
}

/** Outbound: each support of the citation, as a link to it (or why it has none). */
function Supports({ citation: c, go }: { citation: Citation; go: Go }) {
  const nav = useNav();
  if (c.supports.length === 0) {
    return (
      <div className="pop-note">
        {c.kind === "source" ? "Declared origin of evidence. Source authority is not evaluated." : "No supporting evidence declared."}
      </div>
    );
  }
  return c.supports.map((s, i) =>
    s.unreachable ? (
      <div key={i} className="pop-link pop-unreachable">
        <span className="pop-target">
          <FormatDot format={null} />
          <b>{nav.name(s.version)}</b>
          <span className="pop-where">unreachable</span>
        </span>
        <span className="pop-quote">No workspace this one links to holds it now.</span>
      </div>
    ) : (
      <button key={i} className="pop-link" onClick={() => go(s.version, s.locator)}>
        <span className="pop-target">
          <FormatDot format={s.format} />
          <b>{nav.name(s.version)}</b>
          <span className="pop-where">{describeLocator(s.locator)}</span>
          <span className="pop-go" aria-hidden>
            →
          </span>
        </span>
        {s.text !== null && <span className="pop-quote">“{clip(s.text)}”</span>}
      </button>
    ),
  );
}
