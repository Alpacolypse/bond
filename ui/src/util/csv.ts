// Reading a table's cells the way bond's CSV parser does (Python's csv module, default dialect), so a
// cell here is the cell a csv locator addresses: rows and columns from 0, values decoded.
//
// A record ends at "\r\n", "\n" or "\r" outside quotes; a blank line is a row with no cells. A field that
// starts with a quote is quoted: inside it a doubled quote is one quote, and newlines and delimiters are
// text. A quote elsewhere is text. A leading byte-order mark is skipped. Every row is data.

export function parseCsv(text: string, delimiter: string): string[][] {
  const body = text.startsWith("﻿") ? text.slice(1) : text;
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  // "record": at the start of a row; "field": at the start of a later field; "unquoted"/"quoted": inside one;
  // "quote": just after a quote inside a quoted field (a closing quote, or the first of a doubled one).
  let state: "record" | "field" | "unquoted" | "quoted" | "quote" = "record";
  const endField = () => {
    row.push(field);
    field = "";
  };
  for (let i = 0; i < body.length; i++) {
    const ch = body.charAt(i);
    if (state === "quoted") {
      if (ch === '"') state = "quote";
      else field += ch;
      continue;
    }
    const newline = ch === "\n" || ch === "\r";
    if (state === "quote") {
      if (ch === '"') {
        field += '"';
        state = "quoted";
        continue;
      }
      state = "unquoted";
      if (ch !== delimiter && !newline) {
        field += ch; // malformed (bond refuses it), kept as text
        continue;
      }
    }
    if (newline) {
      if (state === "record") rows.push([]);
      else {
        endField();
        rows.push(row);
        row = [];
      }
      state = "record";
      if (ch === "\r" && body.charAt(i + 1) === "\n") i++;
    } else if (ch === delimiter) {
      endField();
      state = "field";
    } else if (ch === '"' && (state === "record" || state === "field")) {
      state = "quoted";
    } else {
      field += ch;
      state = "unquoted";
    }
  }
  if (state !== "record") {
    endField();
    rows.push(row);
  }
  return rows;
}
