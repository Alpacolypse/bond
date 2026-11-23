import { describe, expect, it } from "vitest";
import type { Citation, Kind, Locator } from "../api/types";
import { makeCitation } from "../testing/fixtures";
import { buildMarks, jsonLeafRange, markdownMarks, textClaims, type Mark } from "./marks";

const citation = (index: number, kind: Kind, claim: Locator): Citation => makeCitation({ id: `c${index}`, kind, claim });

const render = (text: string, marks: readonly Mark[]) =>
  marks.map((m) => `${text.slice(m.start, m.end)}:${m.sourcing ?? "-"}${m.target ? "*" : ""}`);

describe("markdownMarks", () => {
  it("uses text gaps and a text target, and ignores other locator types", () => {
    const text = "one two";
    const annotations = {
      units: [
        { locator: { type: "text" as const, start: 0, end: 3 }, kinds: [], text: "one" },
        { locator: { type: "json" as const, pointer: "/x" }, kinds: [], text: null },
      ],
      gaps: [{ type: "text" as const, start: 0, end: 3 }, { type: "json" as const, pointer: "/x" }],
    };
    const marks = markdownMarks(text, [citation(0, "quote", { type: "text", start: 4, end: 7 })], annotations, {
      type: "text",
      start: 0,
      end: 1,
    });
    expect(render(text, marks)).toEqual(["o:uncited*", "ne:uncited", " :-", "two:cited"]);
    expect(render(text, markdownMarks(text, [], annotations, { type: "pdf", page: 0, bbox: [0, 0, 1, 1] }))).toEqual([
      "one:uncited",
      " two:-",
    ]);
  });
});

describe("buildMarks", () => {
  it("partitions the string and colors by the strongest sourcing", () => {
    const text = "abc def ghi";
    const cs = [citation(0, "quote", { type: "text", start: 4, end: 7 }), citation(1, "source", { type: "version" })];
    const marks = buildMarks(text, textClaims(cs, 11), [], [], null);
    expect(render(text, marks)).toEqual(["abc :source", "def:cited", " ghi:source"]);
    expect(marks[1]?.citations.map((c) => c.id)).toEqual(["c0", "c1"]);
  });

  it("marks gaps as uncited, even beside a citation in the same unit, and leaves syntax unmarked", () => {
    const text = "# H\n\npara one\n\npara two";
    const cs = [citation(0, "quote", { type: "text", start: 15, end: 19 })];
    const units = [
      { start: 2, end: 3 },
      { start: 5, end: 13 },
      { start: 15, end: 23 },
    ];
    const gaps = [
      { start: 2, end: 3 },
      { start: 5, end: 13 },
      { start: 20, end: 23 },
    ];
    const marks = buildMarks(text, textClaims(cs, 23), units, gaps, null);
    expect(render(text, marks)).toEqual(["# :-", "H:uncited", "\n\n:-", "para one:uncited", "\n\n:-", "para:cited", " :-", "two:uncited"]);
    expect(marks.map((m) => m.unit)).toEqual([null, 0, null, 1, null, 2, 2, 2]);
  });

  it("converts code point offsets past astral characters to UTF-16", () => {
    const text = "📄 x 😀 yes";
    // code points: 📄=0 ' '=1 x=2 ' '=3 😀=4 ' '=5 y=6 e=7 s=8
    const cs = [citation(0, "unsourced", { type: "text", start: 6, end: 9 })];
    const marks = buildMarks(text, textClaims(cs, 9), [], [], { start: 2, end: 3 });
    expect(render(text, marks)).toEqual(["📄 :-", "x:-*", " 😀 :-", "yes:unsourced"]);
  });
});

describe("jsonLeafRange", () => {
  const leaf = ["blocks", "0", "text"];
  it("covers the whole leaf from an ancestor pointer or the whole version", () => {
    expect(jsonLeafRange({ type: "json", pointer: "/blocks/0" }, leaf, 5)).toEqual({ start: 0, end: 5 });
    expect(jsonLeafRange({ type: "version" }, leaf, 5)).toEqual({ start: 0, end: 5 });
  });
  it("uses the range only at the leaf itself", () => {
    expect(jsonLeafRange({ type: "json", pointer: "/blocks/0/text", start: 1, end: 3 }, leaf, 5)).toEqual({
      start: 1,
      end: 3,
    });
  });
  it("misses siblings and other locator types", () => {
    expect(jsonLeafRange({ type: "json", pointer: "/blocks/1" }, leaf, 5)).toBeNull();
    expect(jsonLeafRange({ type: "json", pointer: "/blocks/0/text/x" }, leaf, 5)).toBeNull();
    expect(jsonLeafRange({ type: "text", start: 0, end: 1 }, leaf, 5)).toBeNull();
  });
});
