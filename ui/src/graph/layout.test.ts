import { describe, expect, it } from "vitest";
import { levels, placement, ringRadii, type Point } from "./layout";

const edge = (from: string, to: string) => ({ from, to });
const dist = (a: Point | undefined, b: Point | undefined) => Math.hypot((a?.x ?? 0) - (b?.x ?? 0), (a?.y ?? 0) - (b?.y ?? 0));

describe("levels", () => {
  it("puts sources at 0 and every document one level beyond everything it cites", () => {
    // fax <- parse <- summary <- determination, and determination also cites the policy directly.
    const l = levels(
      ["fax", "parse", "policy", "summary", "determination"],
      [edge("parse", "fax"), edge("summary", "parse"), edge("determination", "summary"), edge("determination", "policy")],
    );
    expect(Object.fromEntries(l)).toEqual({ fax: 0, policy: 0, parse: 1, summary: 2, determination: 3 });
  });

  it("ignores self-citations and stays finite on a cycle", () => {
    const l = levels(["a", "b", "c"], [edge("a", "a"), edge("a", "b"), edge("b", "a"), edge("b", "c")]);
    expect(l.get("c")).toBe(0);
    for (const v of l.values()) expect(Number.isFinite(v)).toBe(true);
  });
});

describe("ringRadii", () => {
  it("centres a lone source and keeps rings apart", () => {
    const radii = ringRadii([1, 1, 1]);
    expect(radii[0]).toBe(0);
    expect(radii[1]).toBeGreaterThan(0);
    expect((radii[2] ?? 0) - (radii[1] ?? 0)).toBeGreaterThan(0);
  });

  it("widens a crowded ring", () => {
    expect(ringRadii([1, 30])[1]).toBeGreaterThan(300);
  });
});

describe("placement", () => {
  // Two independent chains: each parse should sit next to its own fax.
  const ids = ["fax1", "fax2", "fax3", "fax4", "p1", "p3"];
  const p = placement(ids, [edge("p1", "fax1"), edge("p3", "fax3")]);

  it("puts each document on its ring, next to what it cites", () => {
    expect(dist(p.ring.get("p1"), { x: 0, y: 0 })).toBeCloseTo(p.radii[1] ?? 0);
    expect(dist(p.ring.get("p1"), p.ring.get("fax1"))).toBeLessThan(dist(p.ring.get("p1"), p.ring.get("fax3")));
    expect(dist(p.ring.get("p3"), p.ring.get("fax3"))).toBeLessThan(dist(p.ring.get("p3"), p.ring.get("fax1")));
  });

  it("puts each document in its column, level with what it cites", () => {
    expect(p.column.get("p1")?.x).toBe(p.columns[1]);
    expect(p.column.get("fax2")?.x).toBe(p.columns[0]);
    const below = (a: string, b: string) => (p.column.get(a)?.y ?? 0) > (p.column.get(b)?.y ?? 0);
    expect(below("p3", "p1")).toBe(below("fax3", "fax1"));
  });

  it("is deterministic", () => {
    const edges = [edge("c", "a"), edge("d", "b"), edge("d", "c")];
    expect(placement(["a", "b", "c", "d"], edges)).toEqual(placement(["a", "b", "c", "d"], edges));
  });
});
