import { describe, expect, it } from "vitest";
import type { PdfLocator, Support } from "../api/types";
import { makeCitation } from "../testing/fixtures";
import { citedBoxes } from "./pdfBoxes";

const box = (page: number): PdfLocator => ({ type: "pdf", page, bbox: [0, 0, 0.5, 0.5] });
const support = (version: string, locator: Support["locator"]): Support => ({
  version,
  name: version,
  format: "pdf",
  locator,
  text: null,
  outside: false,
});

describe("citedBoxes", () => {
  it("groups citations by the PDF region of this version they rest on", () => {
    const c0 = makeCitation({ id: "c0", supports: [support("pdf", box(0)), support("pdf", box(1))] });
    const c1 = makeCitation({ id: "c1", supports: [support("pdf", box(0)), support("pdf", box(0))] });
    const boxes = citedBoxes([c0, c1], "pdf");
    expect(boxes.map((b) => [b.locator.page, b.citations.map((c) => c.id)])).toEqual([
      [0, ["c0", "c1"]],
      [1, ["c0"]],
    ]);
  });

  it("ignores supports in other versions and non-PDF locators", () => {
    const c = makeCitation({ supports: [support("other", box(0)), support("pdf", { type: "version" })] });
    expect(citedBoxes([c], "pdf")).toEqual([]);
  });
});
