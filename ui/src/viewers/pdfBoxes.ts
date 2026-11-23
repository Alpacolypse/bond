import { locatorKey } from "../api/locators";
import type { Citation, PdfLocator } from "../api/types";

/** One cited region of a PDF and every citation with a support exactly there. */
export type CitedBox = { locator: PdfLocator; citations: Citation[] };

/** The distinct PDF regions of `version` that `citations` rest on, each with the citations resting on it. */
export function citedBoxes(citations: readonly Citation[], version: string): CitedBox[] {
  const byKey = new Map<string, CitedBox>();
  for (const c of citations) {
    for (const s of c.supports) {
      if (s.version !== version || s.locator.type !== "pdf") continue;
      const key = locatorKey(s.locator);
      const box = byKey.get(key) ?? { locator: s.locator, citations: [] };
      if (!box.citations.includes(c)) box.citations.push(c);
      byKey.set(key, box);
    }
  }
  return [...byKey.values()];
}
