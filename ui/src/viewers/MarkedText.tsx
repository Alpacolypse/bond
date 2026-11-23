import type { MouseEvent } from "react";
import type { Mark } from "../nav/marks";

export type MarkClick = (mark: Mark, e: MouseEvent) => void;

/** Render `source.slice(from, to)` with the highlight marks (UTF-16 ranges over `source`) that fall in it. */
export function MarkedText({
  source,
  from,
  to,
  marks,
  onMark,
}: {
  source: string;
  from: number;
  to: number;
  marks: readonly Mark[];
  onMark: MarkClick;
}) {
  const parts = [];
  for (const m of marks) {
    if (m.end <= from) continue;
    if (m.start >= to) break;
    const s = Math.max(from, m.start);
    const e = Math.min(to, m.end);
    const text = source.slice(s, e);
    if (m.sourcing === null && !m.target) {
      parts.push(text);
      continue;
    }
    parts.push(
      <span
        key={s}
        className={m.sourcing ? `hl hl-${m.sourcing}` : undefined}
        data-target={m.target || undefined}
        onClick={m.sourcing ? (ev) => onMark(m, ev) : undefined}
      >
        {text}
      </span>,
    );
  }
  return <>{parts}</>;
}
