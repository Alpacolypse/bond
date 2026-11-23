import { describe, expect, it } from "vitest";
import type { Locator } from "../api/types";
import store from "../mocks/store.json";
import { parseCsv } from "./csv";

describe("parseCsv", () => {
  it("decodes quoted fields, doubled quotes and newlines inside quotes", () => {
    expect(parseCsv('a,"b, c","say ""hi""","two\nlines"\r\n', ",")).toEqual([["a", "b, c", 'say "hi"', "two\nlines"]]);
  });

  it("ends records at \\r\\n, \\n or \\r, keeps blank lines as empty rows, and adds no row for a final newline", () => {
    expect(parseCsv("a\r\nb\nc\rd\n", ",")).toEqual([["a"], ["b"], ["c"], ["d"]]);
    expect(parseCsv("a\n\nb", ",")).toEqual([["a"], [], ["b"]]);
    expect(parseCsv("", ",")).toEqual([]);
  });

  it("keeps empty fields, ragged rows and quotes inside unquoted fields", () => {
    expect(parseCsv('a,,c,\n1,2\nx"y', ",")).toEqual([["a", "", "c", ""], ["1", "2"], ['x"y']]);
  });

  it("skips a byte-order mark and splits on the format's delimiter", () => {
    expect(parseCsv("﻿code\tname\nR300\tCeiling, bathroom\n", "\t")).toEqual([["code", "name"], ["R300", "Ceiling, bathroom"]]);
  });

  it("reads every cell of the mock store's tables exactly as bond does", () => {
    type Stored = { format: string; content: string | null; units: { locator: Locator; text: string | null }[] };
    const tables = (store.versions as Stored[]).filter((v) => v.format === "csv" || v.format === "tsv");
    expect(tables.length).toBeGreaterThan(0);
    for (const v of tables) {
      const rows = parseCsv(v.content ?? "", v.format === "tsv" ? "\t" : ",");
      for (const { locator, text } of v.units) {
        if (locator.type !== "csv" || locator.row === undefined || locator.column === undefined) continue;
        expect(rows[locator.row]?.[locator.column]).toBe(text);
      }
    }
  });
});
