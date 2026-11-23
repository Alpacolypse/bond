import { describe, expect, it } from "vitest";
import { controlPoint, edgePath, NOTCH, pointAt } from "./geometry";

const dist = (p: { x: number; y: number }, q: { x: number; y: number }) => Math.hypot(p.x - q.x, p.y - q.y);

describe("edgePath", () => {
  const a = { x: 0, y: 0 };
  const b = { x: 100, y: 0 };

  it("bends like force-graph: opposite edges of a pair curve to opposite sides", () => {
    expect(controlPoint(a, b, 0.2).y).toBeCloseTo(-20);
    expect(controlPoint(b, a, 0.2).y).toBeCloseTo(20);
  });

  it("starts on the source boundary and puts the arrow tip on the target boundary", () => {
    const p = edgePath(a, b, 5, 10, 0.2, 8, 0);
    expect(dist(p.start, a)).toBeCloseTo(5, 3);
    expect(dist(p.tip, b)).toBeCloseTo(10, 3);
    expect(dist(p.end, b)).toBeCloseTo(10 + 8 * NOTCH, 3);
    // The arrow points into the target.
    expect(p.direction.x).toBeGreaterThan(0.9);
  });

  it("keeps the trimmed line on the original curve", () => {
    const p = edgePath(a, b, 5, 10, 0.3, 8, 2);
    const c = controlPoint(a, b, 0.3);
    // The sub-curve's midpoint lies on the full curve.
    const mid = pointAt(p.start, p.control, p.end, 0.5);
    let best = Infinity;
    for (let t = 0; t <= 1; t += 0.001) best = Math.min(best, dist(pointAt(a, c, b, t), mid));
    expect(best).toBeLessThan(0.1);
  });
});
