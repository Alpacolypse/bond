import { describe, expect, it } from "vitest";
import { initialNav, navReducer } from "./NavContext";

const go = (version: string) => ({ type: "navigate" as const, ref: { version, locator: { type: "version" as const } } });

describe("navReducer", () => {
  it("records where each visit came from, but not across a closed drawer", () => {
    let s = navReducer(initialNav, go("a"));
    expect(s.open).toBe(true);
    expect(s.history[0]?.from).toBeNull();
    s = navReducer(s, go("b"));
    expect(s.history[1]?.from).toBe("a");
    s = navReducer(s, { type: "close" });
    expect(s.open).toBe(false);
    s = navReducer(s, go("c"));
    expect(s.history[2]?.from).toBeNull();
  });

  it("walks the history, reopening the drawer and re-flashing the entry", () => {
    let s = navReducer(navReducer(initialNav, go("a")), go("b"));
    const nonce = s.history[0]?.nonce;
    s = navReducer(s, { type: "close" });
    s = navReducer(s, { type: "go", delta: -1 });
    expect(s.open).toBe(true);
    expect(s.index).toBe(0);
    expect(s.history[0]?.nonce).not.toBe(nonce);
    expect(s.history[0]?.from).toBeNull();
    s = navReducer(s, { type: "go", delta: 1 });
    expect(s.history[1]?.from).toBe("a");
    expect(navReducer(s, { type: "go", delta: 1 })).toBe(s);
  });

  it("drops the forward history on a new visit", () => {
    let s = navReducer(navReducer(initialNav, go("a")), go("b"));
    s = navReducer(navReducer(s, { type: "go", delta: -1 }), go("c"));
    expect(s.history.map((e) => e.ref.version)).toEqual(["a", "c"]);
  });
});
