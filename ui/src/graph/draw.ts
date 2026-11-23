// Canvas painting for the graph: nodes with their rings and labels, curved links, and arrowheads,
// each styled by the current focus, selection and navigation trail.

import { ACCENT, formatColor } from "../theme";
import { edgePath, NOTCH, type EdgePath } from "./geometry";
import { levelLabel, type Layout, type Placement } from "./layout";
import { isOnTrail, placedEnds, type SimLink, type SimNode, type Trail } from "./model";

/** How far links bend (force-graph's `linkCurvature`). */
export const CURVATURE = 0.16;
/** The opacity of whatever is outside the focused neighbourhood. */
const DIM = 0.12;

export const INK = {
  label: "#3f4654",
  labelHalo: "rgba(250, 250, 248, 0.95)",
  edge: "rgba(110, 118, 136, 0.45)",
  arrow: "rgba(88, 96, 114, 0.85)",
  focusEdge: "rgba(60, 68, 84, 0.85)",
  selected: "#2b303b",
  guide: "rgba(110, 118, 136, 0.2)",
  guideLabel: "#9aa0ab",
};

/** Everything that decides how the graph is painted in one frame. */
export type DrawStyle = {
  /** The zoom: graph units per screen pixel is 1 / scale. */
  scale: number;
  labelAll: boolean;
  /** The hovered node, else the selected one. */
  focus: string | null;
  /** The focused node and its neighbours (null when nothing is focused). */
  lit: ReadonlySet<string> | null;
  selected: string | null;
  trail: Trail | null;
  /** Where each label goes this frame (below its node if missing). */
  labelSides?: ReadonlyMap<string, LabelSide>;
};

/** Ring gaps around a node, in screen pixels. */
const SOURCE_RING = 2.5;
const SELECTED_RING = 5;
const SELECTED_HALO = 9;

export function drawNode(n: SimNode, ctx: CanvasRenderingContext2D, s: DrawStyle) {
  const x = n.x ?? 0;
  const y = n.y ?? 0;
  const px = 1 / s.scale; // one screen pixel in graph units
  const isSelected = n.id === s.selected;
  const isLit = s.lit === null || s.lit.has(n.id);
  const color = formatColor(n.format);
  const circle = (r: number) => {
    ctx.beginPath();
    ctx.arc(x, y, r, 0, 2 * Math.PI);
  };

  if (isSelected) {
    ctx.globalAlpha = 0.16;
    ctx.fillStyle = color;
    circle(n.r + SELECTED_HALO * px);
    ctx.fill();
  }
  // The selected node stays at full strength even while another neighbourhood is focused.
  ctx.globalAlpha = isLit || isSelected ? 1 : DIM;

  if (n.outside) {
    // Outside the workspace: a dashed outline, since none of it is here.
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.4 * px;
    ctx.setLineDash([3 * px, 2.5 * px]);
    circle(n.r);
    ctx.stroke();
    ctx.setLineDash([]);
  } else {
    ctx.fillStyle = color;
    circle(n.r);
    ctx.fill();
  }

  if (n.is_source) {
    // Ingested sources wear a thin outer ring.
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.2 * px;
    circle(n.r + SOURCE_RING * px);
    ctx.stroke();
  }
  if (isSelected) {
    ctx.strokeStyle = INK.selected;
    ctx.lineWidth = 2 * px;
    circle(n.r + SELECTED_RING * px);
    ctx.stroke();
  }
  if (hasLabel(n, s)) drawLabel(n, ctx, s);
  ctx.globalAlpha = 1;
}

function hasLabel(n: SimNode, s: DrawStyle): boolean {
  return n.id === s.selected || (s.lit?.has(n.id) ?? false) || s.labelAll;
}

/** Which side of its node a label sits on. */
export type LabelSide = "below" | "above" | "right" | "left";
const SIDES: readonly LabelSide[] = ["below", "above", "right", "left"];

type Box = { x0: number; y0: number; x1: number; y1: number };
type LabelSpot = { x: number; y: number; align: CanvasTextAlign; baseline: CanvasTextBaseline; box: Box };

const labelFont = (n: SimNode, s: DrawStyle) =>
  `${n.id === s.focus ? 600 : 400} ${12 / s.scale}px Inter, system-ui, sans-serif`;

/** Where a label of `width` goes on one side of its node, and the box it covers. */
function labelSpot(n: SimNode, side: LabelSide, width: number, s: DrawStyle): LabelSpot {
  const px = 1 / s.scale;
  const x = n.x ?? 0;
  const y = n.y ?? 0;
  const gap = n.r + (n.is_source || n.id === s.selected ? 8 : 5) * px;
  const h = 13 * px;
  switch (side) {
    case "below":
      return { x, y: y + gap, align: "center", baseline: "top", box: { x0: x - width / 2, y0: y + gap, x1: x + width / 2, y1: y + gap + h } };
    case "above":
      return { x, y: y - gap, align: "center", baseline: "bottom", box: { x0: x - width / 2, y0: y - gap - h, x1: x + width / 2, y1: y - gap } };
    case "right":
      return { x: x + gap, y, align: "left", baseline: "middle", box: { x0: x + gap, y0: y - h / 2, x1: x + gap + width, y1: y + h / 2 } };
    case "left":
      return { x: x - gap, y, align: "right", baseline: "middle", box: { x0: x - gap - width, y0: y - h / 2, x1: x - gap, y1: y + h / 2 } };
  }
}

const overlaps = (a: Box, b: Box) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;

/**
 * A side for every shown label, chosen greedily so labels clear each other and the nodes: the
 * selected and focused labels first, then larger nodes. A label with no clear side goes below.
 */
export function placeLabels(nodes: readonly SimNode[], ctx: CanvasRenderingContext2D, s: DrawStyle): Map<string, LabelSide> {
  const px = 1 / s.scale;
  const shown = nodes.filter((n) => hasLabel(n, s));
  const rank = (n: SimNode) => (n.id === s.selected ? 2 : n.id === s.focus ? 1 : 0);
  shown.sort((a, b) => rank(b) - rank(a) || b.r - a.r);
  const taken: Box[] = nodes.map((n) => {
    const r = n.r + 2 * px;
    return { x0: (n.x ?? 0) - r, y0: (n.y ?? 0) - r, x1: (n.x ?? 0) + r, y1: (n.y ?? 0) + r };
  });
  const sides = new Map<string, LabelSide>();
  for (const n of shown) {
    ctx.font = labelFont(n, s);
    const width = ctx.measureText(n.title ?? n.name).width + 4 * px;
    const side = SIDES.find((side) => !taken.some((b) => overlaps(b, labelSpot(n, side, width, s).box))) ?? "below";
    sides.set(n.id, side);
    taken.push(labelSpot(n, side, width, s).box);
  }
  return sides;
}

function drawLabel(n: SimNode, ctx: CanvasRenderingContext2D, s: DrawStyle) {
  const px = 1 / s.scale;
  ctx.font = labelFont(n, s);
  const spot = labelSpot(n, s.labelSides?.get(n.id) ?? "below", 0, s);
  ctx.textAlign = spot.align;
  ctx.textBaseline = spot.baseline;
  ctx.lineJoin = "round";
  ctx.lineWidth = 3.5 * px;
  ctx.strokeStyle = INK.labelHalo;
  ctx.strokeText(n.title ?? n.name, spot.x, spot.y);
  ctx.fillStyle = INK.label;
  ctx.fillText(n.title ?? n.name, spot.x, spot.y);
}

/** Faint dashed rings or columns, one per level and labelled, painted under everything else. */
export function drawGuides(ctx: CanvasRenderingContext2D, scale: number, layout: Layout, p: Placement, nodes: SimNode[]) {
  if (layout === "free") return;
  const px = 1 / scale;
  ctx.save();
  ctx.strokeStyle = INK.guide;
  ctx.lineWidth = px;
  ctx.setLineDash([4 * px, 4 * px]);
  ctx.font = `500 ${10.5 * px}px Inter, system-ui, sans-serif`;
  ctx.fillStyle = INK.guideLabel;
  ctx.textAlign = "center";
  ctx.textBaseline = "bottom";
  if (layout === "rings") {
    p.radii.forEach((r) => {
      if (r === 0) return; // a lone source sits at the centre
      ctx.beginPath();
      ctx.arc(0, 0, r, 0, 2 * Math.PI);
      ctx.stroke();
    });
    // Each label sits on its ring, in a clear spot, haloed so the dashes break around it.
    ctx.textBaseline = "middle";
    ctx.lineJoin = "round";
    ctx.lineWidth = 4 * px;
    ctx.strokeStyle = INK.labelHalo;
    ctx.setLineDash([]);
    p.radii.forEach((r, l) => {
      if (r === 0) return;
      const a = p.labelAngles[l] ?? 0;
      ctx.strokeText(levelLabel(l), r * Math.cos(a), r * Math.sin(a));
      ctx.fillText(levelLabel(l), r * Math.cos(a), r * Math.sin(a));
    });
  } else {
    let top = Infinity;
    let bottom = -Infinity;
    for (const n of nodes) {
      top = Math.min(top, (n.y ?? 0) - n.r);
      bottom = Math.max(bottom, (n.y ?? 0) + n.r);
    }
    top -= 24 * px;
    bottom += 36 * px;
    p.columns.forEach((x, l) => {
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, bottom);
      ctx.stroke();
      ctx.fillText(levelLabel(l), x, top - 4 * px);
    });
  }
  ctx.restore();
}

/** The area that counts as the node for hover and click: a little larger than the node. */
export function drawNodeHitArea(n: SimNode, color: string, ctx: CanvasRenderingContext2D) {
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(n.x ?? 0, n.y ?? 0, n.r + 3, 0, 2 * Math.PI);
  ctx.fill();
}

type LinkLook = { path: EdgePath; arrow: number; color: string; head: string; width: number; alpha: number };

/** How a link is drawn under the current focus, or null before the layout has positioned it. */
function linkLook(l: SimLink, s: DrawStyle): LinkLook | null {
  const ends = placedEnds(l);
  if (!ends) return null;
  const [a, b] = ends;
  const px = 1 / s.scale;
  const traversed = isOnTrail(l, s.trail);
  const focused = s.focus !== null && (a.id === s.focus || b.id === s.focus);
  const weight = 1 + 0.35 * Math.log2(l.edge.count);
  const arrow = (traversed || focused ? 11 : 9) * px;
  // Stop at the outermost ring drawn around a node, so the arrowhead is never hidden under it.
  const ringOf = (n: SimNode) => n.r + (n.id === s.selected ? SELECTED_RING : n.is_source ? SOURCE_RING : 0) * px;
  return {
    path: edgePath(a, b, ringOf(a), ringOf(b), CURVATURE, arrow, 1.5 * px),
    arrow,
    color: traversed ? ACCENT : focused ? INK.focusEdge : INK.edge,
    head: traversed ? ACCENT : focused ? INK.focusEdge : INK.arrow,
    width: (traversed ? 2.4 : focused ? 1.6 : 1) * weight * px,
    alpha: s.lit !== null && !focused && !traversed ? DIM : 1,
  };
}

export function drawLink(l: SimLink, ctx: CanvasRenderingContext2D, s: DrawStyle) {
  const look = linkLook(l, s);
  if (!look) return;
  const { start, control, end } = look.path;
  ctx.globalAlpha = look.alpha;
  ctx.strokeStyle = look.color;
  ctx.lineWidth = look.width;
  ctx.lineCap = "butt";
  ctx.beginPath();
  ctx.moveTo(start.x, start.y);
  ctx.quadraticCurveTo(control.x, control.y, end.x, end.y);
  ctx.stroke();
  ctx.globalAlpha = 1;
}

/** A notched arrowhead with its tip on the target's boundary. Drawn after nodes and labels, so never covered. */
export function drawArrow(l: SimLink, ctx: CanvasRenderingContext2D, s: DrawStyle) {
  const look = linkLook(l, s);
  if (!look) return;
  const { tip, direction: d } = look.path;
  const { arrow } = look;
  const half = arrow * 0.46;
  const backX = tip.x - d.x * arrow;
  const backY = tip.y - d.y * arrow;
  ctx.globalAlpha = look.alpha;
  ctx.fillStyle = look.head;
  ctx.beginPath();
  ctx.moveTo(tip.x, tip.y);
  ctx.lineTo(backX - d.y * half, backY + d.x * half);
  ctx.lineTo(tip.x - d.x * arrow * NOTCH, tip.y - d.y * arrow * NOTCH);
  ctx.lineTo(backX + d.y * half, backY - d.x * half);
  ctx.closePath();
  ctx.fill();
  ctx.globalAlpha = 1;
}
