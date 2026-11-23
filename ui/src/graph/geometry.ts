// Geometry of a curved, directed edge: a quadratic Bézier from the citing node to the cited one,
// bent like force-graph's `linkCurvature` (so its particles ride the same curve), and trimmed so the
// line starts at the source's boundary and the arrowhead's tip touches the target's boundary.

/** Where the line meets the arrowhead, as a fraction of the arrow's length back from its tip. */
export const NOTCH = 0.72;

export type Point = { x: number; y: number };

/** The control point force-graph uses for a link with this curvature. */
export function controlPoint(a: Point, b: Point, curvature: number): Point {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const d = Math.hypot(dx, dy) * curvature;
  const angle = Math.atan2(dy, dx) - Math.PI / 2;
  return { x: (a.x + b.x) / 2 + d * Math.cos(angle), y: (a.y + b.y) / 2 + d * Math.sin(angle) };
}

/** The polar form ("blossom") of the quadratic a-c-b: f(t, t) is the curve, f(u, v) a sub-curve's control. */
function blossom(a: Point, c: Point, b: Point, u: number, v: number): Point {
  const wa = (1 - u) * (1 - v);
  const wc = (1 - u) * v + u * (1 - v);
  const wb = u * v;
  return { x: wa * a.x + wc * c.x + wb * b.x, y: wa * a.y + wc * c.y + wb * b.y };
}

export function pointAt(a: Point, c: Point, b: Point, t: number): Point {
  return blossom(a, c, b, t, t);
}

/** The curve's direction at t, as a unit vector. */
export function tangentAt(a: Point, c: Point, b: Point, t: number): Point {
  const x = 2 * (1 - t) * (c.x - a.x) + 2 * t * (b.x - c.x);
  const y = 2 * (1 - t) * (c.y - a.y) + 2 * t * (b.y - c.y);
  const len = Math.hypot(x, y) || 1;
  return { x: x / len, y: y / len };
}

/**
 * The parameter where the curve, walking from `from` toward the far end, first gets `distance` away
 * from `center` (one of its ends). The distance from an end is monotonic along these gentle curves,
 * so bisection is enough.
 */
export function paramAtDistance(a: Point, c: Point, b: Point, fromEnd: boolean, distance: number): number {
  const center = fromEnd ? b : a;
  let near = fromEnd ? 1 : 0;
  let far = fromEnd ? 0 : 1;
  if (Math.hypot(b.x - a.x, b.y - a.y) <= distance) return (near + far) / 2;
  for (let i = 0; i < 24; i++) {
    const mid = (near + far) / 2;
    const p = pointAt(a, c, b, mid);
    if (Math.hypot(p.x - center.x, p.y - center.y) < distance) near = mid;
    else far = mid;
  }
  return (near + far) / 2;
}

export type EdgePath = {
  /** The visible line, as a quadratic from `start` through `control` to `end` (the arrow's base). */
  start: Point;
  control: Point;
  end: Point;
  /** The arrowhead: tip on the target's boundary, pointing along `direction`. */
  tip: Point;
  direction: Point;
};

/**
 * The trimmed path of an edge between circles of radius `sourceR` and `targetR`, with an arrowhead
 * of length `arrow` whose tip sits `gap` outside the target.
 */
export function edgePath(
  a: Point,
  b: Point,
  sourceR: number,
  targetR: number,
  curvature: number,
  arrow: number,
  gap: number,
): EdgePath {
  const c = controlPoint(a, b, curvature);
  const t0 = paramAtDistance(a, c, b, false, sourceR + gap);
  const tTip = paramAtDistance(a, c, b, true, targetR + gap);
  const tBase = Math.max(t0, paramAtDistance(a, c, b, true, targetR + gap + arrow * NOTCH));
  return {
    start: pointAt(a, c, b, t0),
    control: blossom(a, c, b, t0, tBase),
    end: pointAt(a, c, b, tBase),
    tip: pointAt(a, c, b, tTip),
    direction: tangentAt(a, c, b, tTip),
  };
}
