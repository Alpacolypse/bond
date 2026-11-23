// Splitting a string into highlight marks: the pure core of the Markdown, JSON and table viewers.
//
// Inputs are code point ranges (Bond's offsets); output marks are UTF-16 ranges ready for
// slicing a JS string. Each mark's sourcing comes from the kinds of the citations covering it: text
// covered by a citation that rests on something is "cited", else by a SOURCE citation "source", else
// by an UNSOURCED one "unsourced". A gap (text in a claim unit that no citation covers, from
// /annotations) is "uncited"; anything else, like syntax and punctuation, is left unmarked.

import { isPointerPrefix, parsePointer } from "../api/locators";
import type { Annotations, Citation, Kind, Locator } from "../api/types";
import type { Sourcing } from "../theme";
import { codePointLength, offsetMap } from "../util/offsets";

export type CpRange = { start: number; end: number };
export type ClaimRange = CpRange & { citation: Citation };

export type Mark = {
  /** UTF-16 range in the marked string. */
  start: number;
  end: number;
  citations: Citation[];
  /** null for text outside every claim and claim unit (e.g. a heading). */
  sourcing: Sourcing | null;
  /** Index into `units` of the claim unit this mark lies in, if any. */
  unit: number | null;
  target: boolean;
};

export function kindSourcing(kind: Kind): Sourcing {
  return kind === "source" ? "source" : kind === "unsourced" ? "unsourced" : "cited";
}

/** The sourcing of text covered by citations of these kinds: the strongest wins. */
export function kindsSourcing(kinds: readonly Kind[]): Sourcing {
  if (kinds.length === 0) return "uncited";
  const s = new Set(kinds.map(kindSourcing));
  return s.has("cited") ? "cited" : s.has("source") ? "source" : "unsourced";
}

const intersects = (r: CpRange, start: number, end: number) => r.start < end && start < r.end;

/**
 * Partition `text` into marks. Every UTF-16 position of `text` is in exactly one mark, so callers
 * can render the marks in order to reproduce the string.
 */
export function buildMarks(
  text: string,
  claims: readonly ClaimRange[],
  units: readonly CpRange[],
  gaps: readonly CpRange[],
  target: CpRange | null,
): Mark[] {
  const map = offsetMap(text);
  const toUtf16 = (r: CpRange): CpRange => ({ start: map.toUtf16(r.start), end: map.toUtf16(r.end) });
  const claims16 = claims.map((c) => ({ ...toUtf16(c), citation: c.citation }));
  const units16 = units.map(toUtf16);
  const gaps16 = gaps.map(toUtf16);
  const target16 = target && toUtf16(target);

  const marks: Mark[] = [];
  let start = 0;
  for (const end of cutPoints(text.length, [...claims16, ...units16, ...gaps16, ...(target16 ? [target16] : [])])) {
    if (end === start) continue;
    const citations = claims16.filter((c) => intersects(c, start, end)).map((c) => c.citation);
    const unit = units16.findIndex((u) => intersects(u, start, end));
    const mark: Mark = {
      start,
      end,
      citations,
      sourcing: markSourcing(citations, gaps16.some((g) => intersects(g, start, end))),
      unit: unit === -1 ? null : unit,
      target: target16 !== null && intersects(target16, start, end),
    };
    const prev = marks.at(-1);
    if (prev && looksTheSame(prev, mark)) prev.end = end;
    else marks.push(mark);
    start = end;
  }
  return marks;
}

/** Every range boundary inside [0, length], plus both ends, ascending and distinct. */
function cutPoints(length: number, ranges: readonly CpRange[]): number[] {
  const cuts = new Set([0, length]);
  for (const r of ranges) cuts.add(r.start).add(r.end);
  return [...cuts].filter((p) => p >= 0 && p <= length).sort((a, b) => a - b);
}

function markSourcing(citations: readonly Citation[], inGap: boolean): Sourcing | null {
  if (citations.length > 0) return kindsSourcing(citations.map((c) => c.kind));
  return inGap ? "uncited" : null;
}

/** True if adjacent marks render and behave identically, so they can be merged. */
function looksTheSame(a: Mark, b: Mark): boolean {
  return (
    a.sourcing === b.sourcing &&
    a.unit === b.unit &&
    a.target === b.target &&
    a.citations.length === b.citations.length &&
    a.citations.every((c, i) => c.id === b.citations[i]?.id)
  );
}

/** Claim ranges of a text (Markdown) version's citations, over a text of `length` code points. */
export function textClaims(citations: readonly Citation[], length: number): ClaimRange[] {
  const out: ClaimRange[] = [];
  for (const c of citations) {
    if (c.claim.type === "text") out.push({ start: c.claim.start, end: c.claim.end, citation: c });
    else if (c.claim.type === "version") out.push({ start: 0, end: length, citation: c });
  }
  return out;
}

/** The text ranges among locators; others (a stray JSON pointer, a PDF box) are ignored. */
function textRanges(locators: readonly Locator[]): CpRange[] {
  return locators.flatMap((l) => (l.type === "text" ? [{ start: l.start, end: l.end }] : []));
}

/** The marks of a whole Markdown version: its citations' claims, its claim units and gaps, and the target. */
export function markdownMarks(
  content: string,
  citations: readonly Citation[],
  annotations: Annotations,
  target: Locator | null,
): Mark[] {
  const units = textRanges(annotations.units.map((u) => u.locator));
  const textTarget = target?.type === "text" ? target : null;
  return buildMarks(content, textClaims(citations, codePointLength(content)), units, textRanges(annotations.gaps), textTarget);
}

/**
 * The part of one table cell (at row, column; `length` code points) a locator covers: the whole cell
 * (the cell itself, its row, its column, or the whole version), a range inside it, or null if it misses.
 */
export function csvCellRange(locator: Locator, row: number, column: number, length: number): CpRange | null {
  if (locator.type === "version") return { start: 0, end: length };
  if (locator.type !== "csv") return null;
  if (locator.row !== undefined && locator.row !== row) return null;
  if (locator.column !== undefined && locator.column !== column) return null;
  if (locator.start !== undefined && locator.end !== undefined) return { start: locator.start, end: locator.end };
  return { start: 0, end: length };
}

/**
 * The part of one JSON leaf a locator covers, as a code point range over the leaf's displayed
 * text (`length` code points): the whole leaf, a range inside a string, or null if it misses.
 */
export function jsonLeafRange(locator: Locator, leaf: readonly string[], length: number): CpRange | null {
  if (locator.type === "version") return { start: 0, end: length };
  if (locator.type !== "json") return null;
  const tokens = parsePointer(locator.pointer);
  if (!isPointerPrefix(tokens, leaf)) return null;
  if (tokens.length === leaf.length && locator.start !== undefined) return { start: locator.start, end: locator.end };
  return { start: 0, end: length };
}
