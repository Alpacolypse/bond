// Locator helpers mirroring bond.locators: JSON Pointer (RFC 6901), overlap and display.

import { isJsonObject } from "../util/json";
import type { Locator } from "./types";

export function parsePointer(pointer: string): string[] {
  if (pointer === "") return [];
  if (!pointer.startsWith("/")) throw new Error(`json pointer must be empty or start with '/': ${pointer}`);
  return pointer
    .slice(1)
    .split("/")
    .map((t) => t.replace(/~1/g, "/").replace(/~0/g, "~"));
}

export function formatPointer(tokens: readonly string[]): string {
  return tokens.map((t) => "/" + t.replace(/~/g, "~0").replace(/\//g, "~1")).join("");
}

const ARRAY_INDEX = /^(0|[1-9][0-9]*)$/;

/** The value at a pointer, or undefined if it doesn't exist. */
export function resolvePointer(doc: unknown, tokens: readonly string[]): unknown {
  let current: unknown = doc;
  for (const token of tokens) {
    current = childAt(current, token);
    if (current === undefined) return undefined;
  }
  return current;
}

/** One step of pointer resolution: an array element or an own property, else undefined. */
function childAt(value: unknown, token: string): unknown {
  if (Array.isArray(value)) {
    return ARRAY_INDEX.test(token) ? value[Number(token)] : undefined;
  }
  if (isJsonObject(value)) {
    return Object.hasOwn(value, token) ? value[token] : undefined;
  }
  return undefined;
}

/** True if pointer tokens `prefix` lead to `tokens` (or are equal to them). */
export function isPointerPrefix(prefix: readonly string[], tokens: readonly string[]): boolean {
  return prefix.length <= tokens.length && prefix.every((t, i) => t === tokens[i]);
}

/** True if a and b (locators in the same version) share any content. Mirrors bond.locators.overlaps. */
export function overlaps(a: Locator, b: Locator): boolean {
  if (a.type === "version" || b.type === "version") return true;
  if (a.type === "text" && b.type === "text") return a.start < b.end && b.start < a.end;
  if (a.type === "json" && b.type === "json") {
    const at = parsePointer(a.pointer);
    const bt = parsePointer(b.pointer);
    if (!isPointerPrefix(at, bt) && !isPointerPrefix(bt, at)) return false;
    if (at.length !== bt.length || a.start === undefined || b.start === undefined) return true;
    return a.start < b.end && b.start < a.end;
  }
  if (a.type === "csv" && b.type === "csv") {
    const aCell = a.row !== undefined && a.column !== undefined;
    const bCell = b.row !== undefined && b.column !== undefined;
    if (aCell && bCell) {
      if (a.row !== b.row || a.column !== b.column) return false;
      if (a.start === undefined || b.start === undefined || a.end === undefined || b.end === undefined) return true;
      return a.start < b.end && b.start < a.end;
    }
    if (a.row !== undefined && b.row !== undefined && a.row !== b.row) return false;
    if (a.column !== undefined && b.column !== undefined && a.column !== b.column) return false;
    return true; // a row and a column cross at a cell
  }
  if (a.type === "pdf" && b.type === "pdf") {
    if (a.page !== b.page) return false;
    const [ax0, ay0, ax1, ay1] = a.bbox;
    const [bx0, by0, bx1, by1] = b.bbox;
    return ax0 < bx1 && bx0 < ax1 && ay0 < by1 && by0 < ay1;
  }
  return false;
}

/** A stable string key for a locator (for React keys and de-duplication). */
export function locatorKey(l: Locator): string {
  switch (l.type) {
    case "text":
      return `text:${l.start}-${l.end}`;
    case "json":
      return l.start === undefined ? `json:${l.pointer}` : `json:${l.pointer}:${l.start}-${l.end}`;
    case "csv":
      return `csv:${l.row ?? ""}:${l.column ?? ""}` + (l.start === undefined ? "" : `:${l.start}-${l.end}`);
    case "pdf":
      return `pdf:${l.page}:${l.bbox.join(",")}`;
    case "version":
      return "version";
  }
}

/** A short human description of where a locator points. */
export function describeLocator(l: Locator): string {
  switch (l.type) {
    case "text":
      return `chars ${l.start}–${l.end}`;
    case "json":
      return (l.pointer || "/") + (l.start === undefined ? "" : ` [${l.start}–${l.end}]`);
    case "csv":
      return (
        [l.row === undefined ? "" : `row ${l.row + 1}`, l.column === undefined ? "" : `column ${l.column + 1}`]
          .filter(Boolean)
          .join(", ") + (l.start === undefined ? "" : ` [${l.start}–${l.end}]`)
      );
    case "pdf":
      return `page ${l.page + 1}, [${l.bbox.map((v) => v.toFixed(2)).join(", ")}]`;
    case "version":
      return "whole document";
  }
}
