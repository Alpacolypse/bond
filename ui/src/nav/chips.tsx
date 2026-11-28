import type { Format, Kind } from "../api/types";
import { formatColor, SOURCING_COLOR, SOURCING_LABEL, SOURCINGS } from "../theme";
import { kindSourcing } from "./marks";

export function KindChip({ kind }: { kind: Kind }) {
  return (
    <span className="kind" style={{ color: SOURCING_COLOR[kindSourcing(kind)] }}>
      {kind === "source" ? "declared source" : kind}
    </span>
  );
}

/** A format's dot; null is an unreachable version. */
export function FormatDot({ format }: { format: Format | null }) {
  return (
    <span className="dot" style={{ background: formatColor(format) }} title={format ?? "unreachable"} />
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

/** `text` collapsed onto one line and cut to at most `n` characters. */
export function clip(text: string, n = 140): string {
  const t = text.replace(/\s+/g, " ").trim();
  return t.length > n ? `${t.slice(0, n - 1)}…` : t;
}
