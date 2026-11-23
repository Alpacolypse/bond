import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Citation } from "../api/types";
import { buildMarks, textClaims } from "../nav/marks";
import { makeCitation } from "../testing/fixtures";
import { codePointLength } from "../util/offsets";
import { MarkdownViewer } from "./MarkdownViewer";

const citation = (start: number, end: number): Citation => makeCitation({ claim: { type: "text", start, end } });

function highlighted(content: string, start: number, end: number, sourceView = false): string[] {
  const marks = buildMarks(content, textClaims([citation(start, end)], codePointLength(content)), [], [], null);
  const html = renderToStaticMarkup(
    createElement(MarkdownViewer, { content, marks, onMark: () => {}, sourceView }),
  );
  return [...html.matchAll(/<span class="hl hl-cited">([^<]*)<\/span>/g)].map((m) => m[1] ?? "");
}

describe("MarkdownViewer", () => {
  it("splits a highlight across inline markup, after an astral character", () => {
    const content = "# 📄 Title\n\nSee **bold claim** and `code` end.\n";
    // Claim "bold claim** and `code" by code point; the emoji shifts UTF-16 offsets by one.
    const start = [...content].indexOf("b");
    const end = start + codePointLength("bold claim** and `code");
    expect(highlighted(content, start, end)).toEqual(["bold claim", " and ", "code"]);
  });

  it("highlights the raw source in source view", () => {
    const content = "📄 **x** y\n";
    expect(highlighted(content, 2, 7, true)).toEqual(["**x**"]);
  });
});
