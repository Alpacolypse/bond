// Bond addresses text by Unicode code point; JavaScript strings index UTF-16 code units.
// A character outside the Basic Multilingual Plane (e.g. an emoji) is one code point but two
// code units, so every Bond offset must be converted before slicing or comparing with
// UTF-16 positions (such as mdast's `position.offset`).

export type OffsetMap = {
  /** Number of code points in the string. */
  readonly length: number;
  /** UTF-16 index of code point offset `cp` (clamped to the string). */
  toUtf16(cp: number): number;
};

export function offsetMap(text: string): OffsetMap {
  // utf16[i] is the UTF-16 index where code point i starts; the last entry is text.length.
  const utf16: number[] = [];
  for (let i = 0; i < text.length; i++) {
    utf16.push(i);
    const c = text.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff && i + 1 < text.length) {
      const next = text.charCodeAt(i + 1);
      if (next >= 0xdc00 && next <= 0xdfff) i++;
    }
  }
  utf16.push(text.length);
  const length = utf16.length - 1;
  return {
    length,
    toUtf16: (cp) => utf16[Math.max(0, Math.min(cp, length))] ?? text.length,
  };
}

/** Slice `text` by code point offsets [start, end). */
export function sliceCodePoints(text: string, start: number, end?: number): string {
  const map = offsetMap(text);
  return text.slice(map.toUtf16(start), end === undefined ? undefined : map.toUtf16(end));
}

/** Length of `text` in code points. */
export function codePointLength(text: string): number {
  let n = 0;
  for (const _ of text) n++;
  return n;
}
