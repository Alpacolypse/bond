// A collapsible JSON tree addressed by JSON Pointer. Each leaf is highlighted by the citations
// whose claims cover it (whole, or a code point range inside a string).

import { useMemo, useState, type ReactNode } from "react";
import { formatPointer, isPointerPrefix, parsePointer } from "../api/locators";
import type { Annotations, Citation, Locator } from "../api/types";
import { buildMarks, jsonLeafRange } from "../nav/marks";
import { codePointLength } from "../util/offsets";
import { MarkedText, type MarkClick } from "./MarkedText";

/** What every node of the tree needs, passed down unchanged. */
type TreeContext = {
  citations: readonly Citation[];
  /** The pointers of the claim units. */
  units: ReadonlySet<string>;
  /** Text in claim units that no citation covers. */
  gaps: readonly Locator[];
  target: Locator | null;
  targetTokens: readonly string[] | null;
  /** Nodes the reader expanded or collapsed, by pointer; the rest follow `OPEN_DEPTH` and the target. */
  expanded: ReadonlyMap<string, boolean>;
  setExpanded(pointer: string, expanded: boolean): void;
  onMark: MarkClick;
};

const OPEN_DEPTH = 3;
const INDENT = 14;

export function JsonViewer({
  content,
  citations,
  annotations,
  target,
  onMark,
}: {
  content: string;
  citations: readonly Citation[];
  annotations: Annotations;
  target: Locator | null;
  onMark: MarkClick;
}) {
  const value = useMemo((): unknown => JSON.parse(content), [content]);
  const units = useMemo(
    () => new Set(annotations.units.flatMap((u) => (u.locator.type === "json" ? [u.locator.pointer] : []))),
    [annotations],
  );
  const [expanded, setExpanded] = useState<ReadonlyMap<string, boolean>>(new Map());
  const ctx: TreeContext = {
    citations,
    units,
    gaps: annotations.gaps,
    target,
    targetTokens: target?.type === "json" ? parsePointer(target.pointer) : null,
    expanded,
    setExpanded: (pointer, open) => setExpanded((m) => new Map(m).set(pointer, open)),
    onMark,
  };
  return (
    <div className="json">
      <JsonNode value={value} path={[]} label={null} ctx={ctx} last />
    </div>
  );
}

type NodeProps = { path: string[]; label: ReactNode; ctx: TreeContext; last: boolean };

function JsonNode({ value, ...props }: NodeProps & { value: unknown }) {
  if (value !== null && typeof value === "object") return <JsonContainer value={value} {...props} />;
  const { path, label, ctx, last } = props;
  return (
    <div className="json-row" style={{ paddingLeft: path.length * INDENT }}>
      <JsonKey label={label} />
      <JsonLeaf value={value} path={path} ctx={ctx} />
      {last ? "" : ","}
    </div>
  );
}

function JsonKey({ label }: { label: ReactNode }) {
  return label !== null && <span className="json-key">{label}: </span>;
}

/** An array or object: a toggle, its children when open, or a count when closed. */
function JsonContainer({ value, path, label, ctx, last }: NodeProps & { value: object }) {
  const pointer = formatPointer(path);
  const comma = last ? "" : ",";
  const isArray = Array.isArray(value);
  const entries: [string, unknown][] = isArray ? value.map((v, i) => [String(i), v]) : Object.entries(value);
  const [openBracket, closeBracket] = isArray ? ["[", "]"] : ["{", "}"];
  const onTargetPath = ctx.targetTokens !== null && isPointerPrefix(path, ctx.targetTokens);
  const isOpen = ctx.expanded.get(pointer) ?? (path.length < OPEN_DEPTH || onTargetPath);
  const isTarget = onTargetPath && ctx.targetTokens?.length === path.length;
  const indent = { paddingLeft: path.length * INDENT };

  return (
    <div className={isTarget ? "json-block target-block" : "json-block"} data-target={isTarget || undefined}>
      <div className="json-row" style={indent}>
        <button className="toggle" onClick={() => ctx.setExpanded(pointer, !isOpen)} title={pointer || "/"}>
          {isOpen ? "▾" : "▸"}
        </button>
        <JsonKey label={label} />
        {openBracket}
        {!isOpen && (
          <span className="muted">
            {" "}
            {entries.length} {isArray ? "items" : "keys"} {closeBracket}
            {comma}
          </span>
        )}
      </div>
      {isOpen && (
        <>
          {entries.map(([k, v], i) => (
            <JsonNode
              key={k}
              value={v}
              path={[...path, k]}
              label={isArray ? <span className="muted">{k}</span> : JSON.stringify(k)}
              ctx={ctx}
              last={i === entries.length - 1}
            />
          ))}
          <div className="json-row" style={indent}>
            {closeBracket}
            {comma}
          </div>
        </>
      )}
    </div>
  );
}

/** A scalar, highlighted by the claims, claim unit and target that cover it. */
function JsonLeaf({ value, path, ctx }: { value: unknown; path: string[]; ctx: TreeContext }) {
  const isString = typeof value === "string";
  const text = isString ? value : JSON.stringify(value);
  const pointer = formatPointer(path);
  const marks = useMemo(() => {
    const length = codePointLength(text);
    const claims = ctx.citations.flatMap((c) => {
      const r = jsonLeafRange(c.claim, path, length);
      return r ? [{ ...r, citation: c }] : [];
    });
    const units = ctx.units.has(pointer) ? [{ start: 0, end: length }] : [];
    const gaps = ctx.gaps.flatMap((g) => {
      const r = jsonLeafRange(g, path, length);
      return r ? [r] : [];
    });
    const target = ctx.target && jsonLeafRange(ctx.target, path, length);
    return buildMarks(text, claims, units, gaps, target);
    // `path` is a new array every render; `pointer` stands for it.
  }, [text, pointer, ctx.citations, ctx.units, ctx.gaps, ctx.target]);
  return (
    <span className={isString ? "json-string" : "json-scalar"}>
      {isString && '"'}
      <MarkedText source={text} from={0} to={text.length} marks={marks} onMark={ctx.onMark} />
      {isString && '"'}
    </span>
  );
}
