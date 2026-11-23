import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Annotations } from "../api/types";
import { makeCitation } from "../testing/fixtures";
import { JsonViewer } from "./JsonViewer";

/** The text of each highlight of one sourcing in the rendered tree. */
function highlighted(content: string, annotations: Annotations, sourcing: string): string[] {
  const citations = [makeCitation({ kind: "quote", claim: { type: "json", pointer: "/finding", start: 0, end: 19 } })];
  const html = renderToStaticMarkup(
    createElement(JsonViewer, { content, citations, annotations, target: null, onMark: () => {} }),
  );
  return [...html.matchAll(new RegExp(`<span class="hl hl-${sourcing}">([^<]*)</span>`, "g"))].map((m) => m[1] ?? "");
}

describe("JsonViewer", () => {
  const content = JSON.stringify({ finding: "Habitability defect, water intrusion", id: 42 });
  const annotations: Annotations = {
    units: [
      { locator: { type: "json", pointer: "/finding" }, kinds: ["quote"], text: null },
      { locator: { type: "json", pointer: "/id" }, kinds: [], text: null },
    ],
    gaps: [
      { type: "json", pointer: "/finding", start: 19, end: 36 },
      { type: "json", pointer: "/id" },
    ],
  };

  it("marks a gap inside a partly cited string, and a whole uncited value, as uncited", () => {
    expect(highlighted(content, annotations, "cited")).toEqual(["Habitability defect"]);
    expect(highlighted(content, annotations, "uncited")).toEqual([", water intrusion", "42"]);
  });

  it("marks nothing uncited when there are no gaps", () => {
    expect(highlighted(content, { ...annotations, gaps: [] }, "uncited")).toEqual([]);
  });
});
