// Where documents sit in the structured layouts. A document's level is the length of the longest
// chain of citations from it down to a document that cites nothing (a source): sources are level 0,
// and every document is one level beyond everything it cites, so every arrow points inward (rings)
// or leftward (columns). Within a level, documents are ordered next to what they cite and what cites
// them (the barycenter heuristic of layered graph drawing), which keeps related documents together
// and edges short. Both layouts are deterministic: a given graph always looks the same.

import type { GraphEdge } from "../api/types";
import type { SimNode } from "./model";

export type Layout = "rings" | "columns" | "free";
export const LAYOUTS: readonly Layout[] = ["rings", "columns", "free"];

/** Room for a document and its label along a ring, in graph units. */
const NODE_SPACING = 130;
/**
 * The least distance between neighbouring rings. Kept tight, since the view is fitted to the outer
 * ring: crowded rings widen to fit their documents, sparse outer ones stay close.
 */
const RING_GAP = 60;
const COLUMN_GAP = 170;
const ROW_GAP = 56;
/** How far toward its place a node moves on each tick of the simulation. */
const SNAP = 0.3;

export type Point = { x: number; y: number };

export type Placement = {
  level: ReadonlyMap<string, number>;
  /** Each level's ring radius. */
  radii: readonly number[];
  /** Each level's column x. */
  columns: readonly number[];
  /** Where each ring's label goes: the clear spot on it nearest the top. */
  labelAngles: readonly number[];
  /** Each document's place in the rings and in the columns. */
  ring: ReadonlyMap<string, Point>;
  column: ReadonlyMap<string, Point>;
};

type Edge = Pick<GraphEdge, "from" | "to">;

export function placement(ids: readonly string[], edges: readonly Edge[]): Placement {
  const level = levels(ids, edges);
  const byLevel: string[][] = [];
  for (const id of ids) (byLevel[level.get(id) ?? 0] ??= []).push(id);
  const tiers = Array.from(byLevel, (t) => t ?? []);
  const cites = adjacency(ids, edges, "from");
  const citedBy = adjacency(ids, edges, "to");

  const radii = ringRadii(tiers.map((t) => t.length));
  const angle = relax(ids, edges, (id) => radii[level.get(id) ?? 0] ?? 0, order(tiers, cites, citedBy, circular));
  const ring = new Map(ids.map((id) => [id, polar(radii[level.get(id) ?? 0] ?? 0, angle.get(id) ?? 0)]));
  const labelAngles = tiers.map((tier, l) => labelAngle(radii[l] ?? 0, tier.map((id) => angle.get(id) ?? 0)));

  const columns = tiers.map((_, l) => (l - (tiers.length - 1) / 2) * COLUMN_GAP);
  const row = order(tiers, cites, citedBy, linear);
  const column = new Map(ids.map((id) => [id, { x: columns[level.get(id) ?? 0] ?? 0, y: row.get(id) ?? 0 }]));
  return { level, radii, columns, labelAngles, ring, column };
}

/** Rounds of relaxation, the pull of an edge per unit of length, and the push between documents. */
const RELAX_ROUNDS = 400;
const PULL = 0.04;
const PUSH = 1800;
/** The push between an edge and a document within EDGE_REACH of it. */
const EDGE_PUSH = 1200;
const EDGE_REACH = 90;

/**
 * Spread the documents around their rings: every document pushes every other away (force falling
 * with distance) and each edge pulls its two ends together, but a document only moves along its own
 * ring. Starting from the barycenter order, this keeps related documents near each other while
 * clusters fan out over the whole circle, and it staggers documents stacked on one spoke, so edges
 * that skip a ring no longer run alongside the ones that don't. Deterministic for a given graph.
 */
function relax(
  ids: readonly string[],
  edges: readonly Edge[],
  radius: (id: string) => number,
  start: ReadonlyMap<string, number>,
): Map<string, number> {
  // A tiny fixed nudge, so documents stacked exactly on one spoke can slide apart.
  const angle = new Map(ids.map((id, i) => [id, (start.get(id) ?? 0) + 1e-3 * ((i % 7) - 3)]));
  const links = edges.filter((e) => e.from !== e.to && angle.has(e.from) && angle.has(e.to));
  for (let round = 0; round < RELAX_ROUNDS; round++) {
    const step = 0.08 * (1 - round / RELAX_ROUNDS); // the most a document turns per round, cooling
    const pos = new Map(ids.map((id) => [id, polar(radius(id), angle.get(id) ?? 0)]));
    const force = new Map(ids.map((id) => [id, { x: 0, y: 0 }]));
    const push = (id: string, x: number, y: number) => {
      const f = force.get(id);
      if (f) [f.x, f.y] = [f.x + x, f.y + y];
    };
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const a = pos.get(ids[i] ?? "");
        const b = pos.get(ids[j] ?? "");
        if (!a || !b) continue;
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const k = PUSH / Math.max(dx * dx + dy * dy, 1);
        push(ids[i] ?? "", dx * k, dy * k);
        push(ids[j] ?? "", -dx * k, -dy * k);
      }
    }
    for (const e of links) {
      const a = pos.get(e.from);
      const b = pos.get(e.to);
      if (!a || !b) continue;
      push(e.from, (b.x - a.x) * PULL, (b.y - a.y) * PULL);
      push(e.to, (a.x - b.x) * PULL, (a.y - b.y) * PULL);
      // An edge and a document it passes close to push apart, so lines go around documents.
      const lx = b.x - a.x;
      const ly = b.y - a.y;
      const len2 = Math.max(lx * lx + ly * ly, 1);
      for (const id of ids) {
        if (id === e.from || id === e.to) continue;
        const p = pos.get(id);
        if (!p) continue;
        const t = Math.max(0, Math.min(1, ((p.x - a.x) * lx + (p.y - a.y) * ly) / len2));
        const dx = p.x - (a.x + t * lx);
        const dy = p.y - (a.y + t * ly);
        const d2 = dx * dx + dy * dy;
        if (d2 > EDGE_REACH * EDGE_REACH) continue;
        const k = EDGE_PUSH / Math.max(d2, 1);
        push(id, dx * k, dy * k);
        push(e.from, -dx * k * (1 - t), -dy * k * (1 - t));
        push(e.to, -dx * k * t, -dy * k * t);
      }
    }
    for (const id of ids) {
      const r = radius(id);
      const a = angle.get(id) ?? 0;
      const f = force.get(id);
      if (r === 0 || !f) continue; // a lone source at the centre has nowhere to turn
      const along = -f.x * Math.sin(a) + f.y * Math.cos(a); // the force along the ring
      angle.set(id, a + Math.max(-step, Math.min(step, along / r)));
    }
  }
  return angle;
}

/** Clearance from the nearest document that is enough for a ring's label, in graph units. */
const LABEL_CLEARANCE = 100;

/** The angle on a ring of radius `r` nearest the top with enough room from the documents on it. */
function labelAngle(r: number, angles: readonly number[]): number {
  const top = -Math.PI / 2;
  let best = top;
  let bestScore = -Infinity;
  for (let step = 0; step < 72; step++) {
    const a = top + (step / 72) * TAU;
    const room = Math.min(LABEL_CLEARANCE, ...angles.map((n) => r * gap(a, n)));
    const score = room - 0.05 * r * gap(a, top);
    if (score > bestScore) [best, bestScore] = [a, score];
  }
  return best;
}

/** The smaller angle between two directions. */
const gap = (a: number, b: number) => {
  const d = Math.abs(a - b) % TAU;
  return Math.min(d, TAU - d);
};

/**
 * Each document's level. Self-citations are ignored, and so is a citation that closes a cycle (two
 * documents citing each other), so the levels stay finite.
 */
export function levels(ids: readonly string[], edges: readonly Edge[]): Map<string, number> {
  const cites = adjacency(ids, edges, "from");
  const level = new Map<string, number>();
  const onPath = new Set<string>();
  const visit = (id: string): number => {
    const known = level.get(id);
    if (known !== undefined) return known;
    onPath.add(id);
    let l = 0;
    for (const to of cites.get(id) ?? []) if (!onPath.has(to)) l = Math.max(l, visit(to) + 1);
    onPath.delete(id);
    level.set(id, l);
    return l;
  };
  for (const id of ids) visit(id);
  return level;
}

/**
 * Each ring's radius, from how many documents it holds: wide enough to fit them and at least RING_GAP
 * beyond the previous ring. A lone source sits at the centre.
 */
export function ringRadii(counts: readonly number[]): number[] {
  const radii: number[] = [];
  counts.forEach((n, l) => {
    const fit = n > 1 ? (n * NODE_SPACING) / TAU : 0;
    const least = l === 0 ? (n > 1 ? RING_GAP : 0) : (radii[l - 1] ?? 0) + RING_GAP;
    radii.push(Math.max(least, fit));
  });
  return radii;
}

/** Each document's neighbours along `from -> to` edges (or against them), without self-loops. */
function adjacency(ids: readonly string[], edges: readonly Edge[], side: "from" | "to") {
  const m = new Map<string, string[]>(ids.map((id) => [id, []]));
  for (const e of edges) {
    const [a, b] = side === "from" ? [e.from, e.to] : [e.to, e.from];
    if (a !== b && m.has(b)) m.get(a)?.push(b);
  }
  return m;
}

const TAU = 2 * Math.PI;
const polar = (r: number, a: number): Point => ({ x: r * Math.cos(a), y: r * Math.sin(a) });

/** How positions within a level are averaged and spaced: around a circle, or along a line. */
type Axis = {
  start: (i: number, n: number) => number;
  mean: (values: readonly number[]) => number | undefined;
  /** Evenly spaced positions for a level, in the order of the wanted ones and close to them. */
  spread: (wanted: readonly number[]) => number[];
};

const circular: Axis = {
  start: (i, n) => (i / n) * TAU,
  mean: (angles) => {
    if (angles.length === 0) return undefined;
    let x = 0;
    let y = 0;
    for (const a of angles) {
      x += Math.cos(a);
      y += Math.sin(a);
    }
    return Math.hypot(x, y) < 1e-9 ? angles[0] : Math.atan2(y, x);
  },
  spread: (wanted) => {
    const step = TAU / wanted.length;
    const turn = (a: number) => ((a % TAU) + TAU) % TAU;
    const rank = ranks(wanted.map(turn));
    // Rotate the evenly spaced ring to sit as close as it can to where its documents want to be.
    const offset = circular.mean(wanted.map((a, i) => a - (rank[i] ?? 0) * step)) ?? 0;
    return wanted.map((_, i) => offset + (rank[i] ?? 0) * step);
  },
};

const linear: Axis = {
  start: (i, n) => (i - (n - 1) / 2) * ROW_GAP,
  mean: (values) => (values.length === 0 ? undefined : values.reduce((a, b) => a + b, 0) / values.length),
  spread: (wanted) => {
    const rank = ranks(wanted);
    return wanted.map((_, i) => ((rank[i] ?? 0) - (wanted.length - 1) / 2) * ROW_GAP);
  },
};

/** Each value's position in sorted order (ties keep their input order). */
function ranks(values: readonly number[]): number[] {
  const order = values.map((v, i) => [v, i] as const).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const rank: number[] = [];
  order.forEach(([, i], r) => (rank[i] = r));
  return rank;
}

/**
 * A position along each level's axis for every document: evenly spaced, and ordered so documents
 * face what they cite and what cites them.
 */
function order(tiers: string[][], cites: Map<string, string[]>, citedBy: Map<string, string[]>, axis: Axis) {
  const pos = new Map<string, number>();
  for (const tier of tiers) tier.forEach((id, i) => pos.set(id, axis.start(i, tier.length)));
  const pull = (tier: string[], toward: Map<string, string[]>) => {
    const wanted = tier.map((id) => axis.mean((toward.get(id) ?? []).map((n) => pos.get(n) ?? 0)) ?? pos.get(id) ?? 0);
    axis.spread(wanted).forEach((p, i) => pos.set(tier[i] ?? "", p));
  };
  // Sweep outward (face what you cite), then inward (face what cites you), a few times over.
  for (let sweep = 0; sweep < 6; sweep++) {
    for (let l = 1; l < tiers.length; l++) pull(tiers[l] ?? [], cites);
    for (let l = tiers.length - 2; l >= 0; l--) pull(tiers[l] ?? [], citedBy);
  }
  for (let l = 1; l < tiers.length; l++) pull(tiers[l] ?? [], cites);
  return pos;
}

export const levelLabel = (level: number) => (level === 0 ? "sources" : `level ${level}`);

/**
 * A d3 force that moves every node part of the way to its place on every tick, however cool the
 * simulation, so nodes glide into a new layout and back into place after a drag.
 */
export function placeForce(places: ReadonlyMap<string, Point>) {
  let nodes: SimNode[] = [];
  const force = () => {
    for (const n of nodes) {
      const to = places.get(n.id);
      if (!to) continue;
      n.vx = (n.vx ?? 0) + (to.x - (n.x ?? 0) - (n.vx ?? 0)) * SNAP;
      n.vy = (n.vy ?? 0) + (to.y - (n.y ?? 0) - (n.vy ?? 0)) * SNAP;
    }
  };
  force.initialize = (ns: SimNode[]) => {
    nodes = ns;
  };
  return force;
}
