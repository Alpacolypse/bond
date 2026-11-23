import { describe, expect, it } from "vitest";
import { codePointLength, offsetMap, sliceCodePoints } from "./offsets";

describe("offsetMap", () => {
  it("is the identity for BMP text", () => {
    const m = offsetMap("héllo ﬁ");
    expect(m.length).toBe(7);
    expect([0, 1, 2, 7].map(m.toUtf16)).toEqual([0, 1, 2, 7]);
  });

  it("counts an astral character as one code point but two UTF-16 units", () => {
    const text = "# 📄 Summary";
    const m = offsetMap(text);
    expect(m.length).toBe(11);
    expect(m.toUtf16(2)).toBe(2); // the emoji starts here
    expect(m.toUtf16(3)).toBe(4); // and ends two units later
    expect(text.slice(m.toUtf16(4), m.toUtf16(11))).toBe("Summary");
  });

  it("clamps out-of-range offsets", () => {
    const m = offsetMap("a😀b");
    expect(m.toUtf16(-1)).toBe(0);
    expect(m.toUtf16(99)).toBe(4);
  });

  it("treats a lone surrogate as one code point, like Python", () => {
    const m = offsetMap("a\ud800b");
    expect(m.length).toBe(3);
    expect(m.toUtf16(2)).toBe(2);
  });
});

describe("sliceCodePoints / codePointLength", () => {
  it("slice by code points", () => {
    expect(sliceCodePoints("😀😁😂x", 1, 3)).toBe("😁😂");
    expect(sliceCodePoints("😀😁😂x", 3)).toBe("x");
    expect(codePointLength("😀😁😂x")).toBe(4);
  });
});
