import { describe, expect, it } from "vitest";
import { formatPointer, isPointerPrefix, overlaps, parsePointer, resolvePointer } from "./locators";
import type { Locator } from "./types";

describe("isPointerPrefix", () => {
  it("compares whole tokens", () => {
    expect(isPointerPrefix([], ["a"])).toBe(true);
    expect(isPointerPrefix(["a"], ["a", "b"])).toBe(true);
    expect(isPointerPrefix(["a", "b"], ["a"])).toBe(false);
    expect(isPointerPrefix(["1"], ["10"])).toBe(false);
  });
});

describe("JSON pointers", () => {
  it("round-trip escapes", () => {
    expect(parsePointer("/a~1b/c~0d/0")).toEqual(["a/b", "c~d", "0"]);
    expect(formatPointer(["a/b", "c~d", "0"])).toBe("/a~1b/c~0d/0");
    expect(parsePointer("")).toEqual([]);
  });

  it("resolve like bond.locators.resolve_pointer", () => {
    const doc = { blocks: [{ text: "hi" }, null], "": 1 };
    expect(resolvePointer(doc, parsePointer("/blocks/0/text"))).toBe("hi");
    expect(resolvePointer(doc, parsePointer("/blocks/1"))).toBeNull();
    expect(resolvePointer(doc, parsePointer("/blocks/01"))).toBeUndefined();
    expect(resolvePointer(doc, parsePointer("/blocks/2"))).toBeUndefined();
    expect(resolvePointer(doc, parsePointer("/"))).toBe(1);
    expect(resolvePointer(doc, parsePointer("/blocks/0/text/x"))).toBeUndefined();
  });
});

describe("overlaps", () => {
  const text = (start: number, end: number): Locator => ({ type: "text", start, end });
  const json = (pointer: string, range?: [number, number]): Locator =>
    range === undefined ? { type: "json", pointer } : { type: "json", pointer, start: range[0], end: range[1] };
  const pdf = (page: number, bbox: [number, number, number, number]): Locator => ({ type: "pdf", page, bbox });

  it("compares text ranges half-open", () => {
    expect(overlaps(text(0, 5), text(4, 9))).toBe(true);
    expect(overlaps(text(0, 5), text(5, 9))).toBe(false);
  });

  it("compares json pointers by prefix, ranges only at the same pointer", () => {
    expect(overlaps(json("/blocks/1"), json("/blocks/1/text", [0, 3]))).toBe(true);
    expect(overlaps(json("/blocks/1"), json("/blocks/10"))).toBe(false);
    expect(overlaps(json("/a", [0, 3]), json("/a", [3, 5]))).toBe(false);
    expect(overlaps(json("/a", [0, 4]), json("/a", [3, 5]))).toBe(true);
    expect(overlaps(json("/a", [0, 4]), json("/a"))).toBe(true);
  });

  it("compares pdf boxes on the same page", () => {
    expect(overlaps(pdf(0, [0, 0, 0.5, 0.5]), pdf(0, [0.4, 0.4, 1, 1]))).toBe(true);
    expect(overlaps(pdf(0, [0, 0, 0.5, 0.5]), pdf(1, [0.4, 0.4, 1, 1]))).toBe(false);
    expect(overlaps(pdf(0, [0, 0, 0.5, 0.5]), pdf(0, [0.5, 0, 1, 1]))).toBe(false);
  });

  it("crosses csv rows and columns at a cell", () => {
    const cell = (row: number, column: number, start?: number, end?: number): Locator =>
      start === undefined ? { type: "csv", row, column } : { type: "csv", row, column, start, end };
    expect(overlaps({ type: "csv", row: 2 }, { type: "csv", column: 9 })).toBe(true);
    expect(overlaps({ type: "csv", row: 2 }, cell(2, 9))).toBe(true);
    expect(overlaps({ type: "csv", row: 1 }, cell(2, 9))).toBe(false);
    expect(overlaps(cell(2, 9, 0, 3), cell(2, 9, 2, 5))).toBe(true);
    expect(overlaps(cell(2, 9, 0, 3), cell(2, 9, 3, 5))).toBe(false);
    expect(overlaps(cell(2, 9), cell(2, 8))).toBe(false);
  });

  it("lets a version locator overlap everything", () => {
    expect(overlaps({ type: "version" }, pdf(3, [0, 0, 1, 1]))).toBe(true);
  });
});
