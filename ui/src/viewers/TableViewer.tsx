// A CSV or TSV version as a table, addressed by row and column (both from 0 in locators, shown from 1 as
// in handles). Each cell is highlighted by the citations whose claims cover it: the cell, part of it, its
// row, its column, or the whole version. Every row is data, so the first row is drawn like the rest.

import { useMemo } from "react";
import type { Annotations, Citation, Locator } from "../api/types";
import { buildMarks, csvCellRange } from "../nav/marks";
import { parseCsv } from "../util/csv";
import { codePointLength } from "../util/offsets";
import { MarkedText, type MarkClick } from "./MarkedText";

type CellContext = {
  citations: readonly Citation[];
  /** "row:column" of each claim unit. */
  units: ReadonlySet<string>;
  gaps: readonly Locator[];
  target: Locator | null;
  onMark: MarkClick;
};

export function TableViewer({
  content,
  delimiter,
  citations,
  annotations,
  target,
  onMark,
}: {
  content: string;
  delimiter: string;
  citations: readonly Citation[];
  annotations: Annotations;
  target: Locator | null;
  onMark: MarkClick;
}) {
  const rows = useMemo(() => parseCsv(content, delimiter), [content, delimiter]);
  const width = rows.reduce((w, r) => Math.max(w, r.length), 0);
  const units = useMemo(
    () =>
      new Set(
        annotations.units.flatMap((u) =>
          u.locator.type === "csv" && u.locator.row !== undefined && u.locator.column !== undefined
            ? [`${u.locator.row}:${u.locator.column}`]
            : [],
        ),
      ),
    [annotations],
  );
  const ctx: CellContext = { citations, units, gaps: annotations.gaps, target, onMark };
  const csvTarget = target?.type === "csv" ? target : null;
  const columns = Array.from({ length: width }, (_, c) => c);
  const isTargetColumn = (c: number) => csvTarget?.row === undefined && csvTarget?.column === c;
  return (
    <div className="table-wrap">
      <table className="csv">
        <thead>
          <tr>
            <th className="corner" />
            {columns.map((c) => (
              <th key={c} className={isTargetColumn(c) ? "target-line" : undefined} data-target={isTargetColumn(c) || undefined}>
                {c + 1}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((cells, r) => {
            const isTargetRow = csvTarget?.column === undefined && csvTarget?.row === r;
            return (
              <tr key={r} className={isTargetRow ? "target-line" : undefined} data-target={isTargetRow || undefined}>
                <th className="row-number">{r + 1}</th>
                {columns.map((c) =>
                  c < cells.length ? (
                    <TableCell
                      key={c}
                      text={cells[c] ?? ""}
                      row={r}
                      column={c}
                      ctx={ctx}
                      inLine={isTargetRow || isTargetColumn(c)}
                    />
                  ) : (
                    <td key={c} className="missing" title="this row has no such cell" />
                  ),
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * One cell's value, highlighted by the claims, claim unit and gaps that cover it. A target cell (or a range
 * in it) is marked in its text; a cell in a target row or column (inLine) is tinted instead.
 */
function TableCell(props: { text: string; row: number; column: number; ctx: CellContext; inLine: boolean }) {
  const { text, row, column, ctx, inLine } = props;
  const marks = useMemo(() => {
    const length = codePointLength(text);
    const claims = ctx.citations.flatMap((c) => {
      const r = csvCellRange(c.claim, row, column, length);
      return r ? [{ ...r, citation: c }] : [];
    });
    const units = ctx.units.has(`${row}:${column}`) ? [{ start: 0, end: length }] : [];
    const gaps = ctx.gaps.flatMap((g) => {
      const r = csvCellRange(g, row, column, length);
      return r ? [r] : [];
    });
    const t = ctx.target;
    const target = t?.type === "csv" && t.row !== undefined && t.column !== undefined
      ? csvCellRange(t, row, column, length)
      : null;
    return buildMarks(text, claims, units, gaps, target);
  }, [text, row, column, ctx.citations, ctx.units, ctx.gaps, ctx.target]);
  return (
    <td className={inLine ? "target-cell" : undefined} data-target={inLine || undefined}>
      <MarkedText source={text} from={0} to={text.length} marks={marks} onMark={ctx.onMark} />
    </td>
  );
}
