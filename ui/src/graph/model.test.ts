import { describe, expect, it } from "vitest";
import { makeEdge, makeNode } from "../testing/fixtures";
import { isOnTrail, neighbourMap, nodeRadius, placedEnds, toSimData, touches, type SimLink, type SimNode } from "./model";

const simNode = (id: string, at?: { x: number; y: number }): SimNode => ({ ...makeNode(id), id, r: 4, ...at });

describe("toSimData", () => {
  it("keys nodes by version and links them by version", () => {
    const graph = { nodes: [makeNode("a", { citation_count: 4 }), makeNode("b")], edges: [makeEdge("a", "b")] };
    const data = toSimData(graph);
    expect(data.nodes.map((n) => [n.id, n.r])).toEqual([
      ["a", nodeRadius(graph.nodes[0] ?? makeNode("a"))],
      ["b", 4],
    ]);
    expect(data.links).toEqual([{ source: "a", target: "b", edge: graph.edges[0] }]);
  });
});

describe("neighbourMap", () => {
  it("links both ends of every edge", () => {
    const m = neighbourMap([makeEdge("a", "b"), makeEdge("c", "a")]);
    expect([...(m.get("a") ?? [])].sort()).toEqual(["b", "c"]);
    expect([...(m.get("b") ?? [])]).toEqual(["a"]);
    expect(m.get("d")).toBeUndefined();
  });
});

describe("link questions", () => {
  const link: SimLink = { source: "a", target: simNode("b"), edge: makeEdge("a", "b") };

  it("matches a trail in either direction", () => {
    expect(isOnTrail(link, { a: "a", b: "b" })).toBe(true);
    expect(isOnTrail(link, { a: "b", b: "a" })).toBe(true);
    expect(isOnTrail(link, { a: "a", b: "c" })).toBe(false);
    expect(isOnTrail(link, null)).toBe(false);
  });

  it("knows which nodes it touches", () => {
    expect(touches(link, "b")).toBe(true);
    expect(touches(link, "c")).toBe(false);
    expect(touches(link, null)).toBe(false);
  });

  it("has placed ends only once both nodes are positioned", () => {
    expect(placedEnds(link)).toBeNull();
    expect(placedEnds({ ...link, source: simNode("a") })).toBeNull();
    const a = simNode("a", { x: 1, y: 2 });
    const b = simNode("b", { x: 3, y: 4 });
    expect(placedEnds({ ...link, source: a, target: b })).toEqual([a, b]);
  });
});
