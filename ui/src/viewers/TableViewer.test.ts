import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Annotations, Citation, Locator } from "../api/types";
import { makeCitation } from "../testing/fixtures";
import { TableViewer } from "./TableViewer";

const CONTENT = 'code,description,amount\nR300,"Ceiling repair, bathroom",1250.00\nR100,Rental\n';

function render(citations: Citation[], annotations: Annotations = { units: [], gaps: [] }, target: Locator | null = null) {
  return renderToStaticMarkup(
    createElement(TableViewer, { content: CONTENT, delimiter: ",", citations, annotations, target, onMark: () => {} }),
  );
}

/** The text of each highlight of one sourcing in the rendered table. */
function highlighted(html: string, sourcing: string): string[] {
  return [...html.matchAll(new RegExp(`<span class="hl hl-${sourcing}"[^>]*>([^<]*)</span>`, "g"))].map((m) => m[1] ?? "");
}

describe("TableViewer", () => {
  it("draws every row as data, numbered from 1, with decoded cells and a gap for a short row", () => {
    const html = render([]);
    expect(html).toContain("<td>code</td>");
    expect(html).toContain("<td>Ceiling repair, bathroom</td>");
    expect(html).toContain('<th class="row-number">3</th>');
    expect(html.match(/class="missing"/g)).toHaveLength(1);
  });

  it("highlights a cell, part of a cell, a row and a column by the citations covering them", () => {
    const html = render([
      makeCitation({ id: "c1", kind: "quote", claim: { type: "csv", row: 1, column: 1, start: 0, end: 7 } }),
      makeCitation({ id: "c2", kind: "source", claim: { type: "csv", row: 2 } }),
      makeCitation({ id: "c3", kind: "unsourced", claim: { type: "csv", column: 2 } }),
      makeCitation({ id: "c4", kind: "paraphrase", claim: { type: "csv", row: 0, column: 0 } }),
    ]);
    expect(highlighted(html, "cited")).toEqual(["code", "Ceiling"]);
    expect(highlighted(html, "source")).toEqual(["R100", "Rental"]);
    expect(highlighted(html, "unsourced")).toEqual(["amount", "1250.00"]);
  });

  it("marks uncited cells and parts of cells from the gaps", () => {
    const html = render([], {
      units: [],
      gaps: [
        { type: "csv", row: 1, column: 0 },
        { type: "csv", row: 1, column: 1, start: 8, end: 24 },
      ],
    });
    expect(highlighted(html, "uncited")).toEqual(["R300", "repair, bathroom"]);
  });

  it("marks a target row, column or cell for the flash", () => {
    const row = render([], undefined, { type: "csv", row: 2 });
    expect(row).toContain('<tr class="target-line" data-target="true">');
    expect(row).toContain('<td class="target-cell" data-target="true">Rental</td>');
    const column = render([], undefined, { type: "csv", column: 1 });
    expect(column).toContain('<th class="target-line" data-target="true">2</th>');
    expect(column.match(/class="target-cell"/g)).toHaveLength(3);
    expect(render([], undefined, { type: "csv", row: 1, column: 2 })).toContain('<span data-target="true">1250.00</span>');
  });
});
