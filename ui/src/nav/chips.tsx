import type { Citation, Format, Kind } from "../api/types";
import { formatColor, SOURCING_COLOR, SOURCING_LABEL, SOURCINGS } from "../theme";
import { kindSourcing } from "./marks";

export function KindChip({ kind }: { kind: Kind }) {
  return (
    <span className="kind" style={{ color: SOURCING_COLOR[kindSourcing(kind)] }}>
      {kind === "source" ? "declared source" : kind}
    </span>
  );
}

/** A format's dot; null is a version outside the workspace. */
export function FormatDot({ format }: { format: Format | null }) {
  return (
    <span className="dot" style={{ background: formatColor(format) }} title={format ?? "outside this workspace"} />
  );
}

export function SourcingLegend() {
  return (
    <span className="legend">
      {SOURCINGS.map((s) => (
        <span key={s} className="legend-item" title={SOURCING_LABEL[s]}>
          <span className={`swatch hl-${s}`} />
          {s === "source" ? "declared source" : s === "uncited" ? "undeclared" : s}
        </span>
      ))}
    </span>
  );
}

/** A citation's kind-specific attrs on one line: registered producer identifiers. */
export function attrsLine(attrs: Citation["attrs"]): string {
  return Object.entries(attrs)
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" · ");
}

/** `text` collapsed onto one line and cut to at most `n` characters. */
export function clip(text: string, n = 140): string {
  const t = text.replace(/\s+/g, " ").trim();
  return t.length > n ? `${t.slice(0, n - 1)}…` : t;
}
