// The API graph as force-graph data, and the pure questions the view asks of it.

import type { Graph, GraphEdge, GraphNode } from "../api/types";

/** A node in the simulation. force-graph adds `x`, `y` and their velocities once it has placed the node. */
export type SimNode = GraphNode & { id: string; r: number; x?: number; y?: number; vx?: number; vy?: number };

/** A link in the simulation. force-graph replaces the ends' ids with their nodes once it starts. */
export type SimLink = { source: string | SimNode; target: string | SimNode; edge: GraphEdge };

export type SimData = { nodes: SimNode[]; links: SimLink[] };

/** A node the layout has positioned. */
export type PlacedNode = SimNode & { x: number; y: number };

/** The two documents of the last navigation, whose edge is briefly marked. */
export type Trail = { a: string; b: string };

/** A node's radius in graph units: grows with how much it cites. */
export function nodeRadius(n: GraphNode): number {
  return 4 + Math.sqrt(n.citation_count) * 0.9;
}

export function toSimData(graph: Graph): SimData {
  return {
    nodes: graph.nodes.map((n) => ({ ...n, id: n.version, r: nodeRadius(n) })),
    links: graph.edges.map((e) => ({ source: e.from, target: e.to, edge: e })),
  };
}

/** Each version's neighbours, ignoring edge direction. */
export function neighbourMap(edges: readonly GraphEdge[]): Map<string, Set<string>> {
  const m = new Map<string, Set<string>>();
  const link = (a: string, b: string) => m.set(a, (m.get(a) ?? new Set()).add(b));
  for (const e of edges) {
    link(e.from, e.to);
    link(e.to, e.from);
  }
  return m;
}

export function endId(end: string | SimNode): string {
  return typeof end === "string" ? end : end.id;
}

/** True if the link joins the trail's two documents, in either direction. */
export function isOnTrail(l: SimLink, trail: Trail | null): boolean {
  if (!trail) return false;
  const s = endId(l.source);
  const d = endId(l.target);
  return (s === trail.a && d === trail.b) || (s === trail.b && d === trail.a);
}

/** True if either end of the link is `id`. */
export function touches(l: SimLink, id: string | null): boolean {
  return id !== null && (endId(l.source) === id || endId(l.target) === id);
}

function isPlaced(end: string | SimNode): end is PlacedNode {
  return typeof end !== "string" && end.x !== undefined && end.y !== undefined;
}

/** Both ends of a link, or null before the layout has positioned them. */
export function placedEnds(l: SimLink): [PlacedNode, PlacedNode] | null {
  return isPlaced(l.source) && isPlaced(l.target) ? [l.source, l.target] : null;
}
