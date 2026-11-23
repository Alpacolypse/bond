// The store as a graph filling the window: one node per document, one curved arrow per
// (citing -> cited) pair, in concentric rings around the sources, in columns, or free (layout.ts). Hover a node to focus its neighbourhood; click it to open it in the
// drawer, which keeps it ringed and centred in the part of the window the drawer leaves visible.

import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, { type ForceGraphMethods } from "react-force-graph-2d";
import type { Graph } from "../api/types";
import { useNav, type NavEntry } from "../nav/NavContext";
import { ACCENT } from "../theme";
import { useElementSize } from "../util/useElementSize";
import {
  CURVATURE,
  drawArrow,
  drawGuides,
  drawLink,
  drawNode,
  drawNodeHitArea,
  INK,
  placeLabels,
  type DrawStyle,
  type LabelSide,
} from "./draw";
import { placeForce, placement, type Layout } from "./layout";
import { isOnTrail, neighbourMap, toSimData, touches, type SimLink, type SimNode, type Trail } from "./model";

/** Below this zoom only focused nodes are labelled, unless the graph is small enough to label everything. */
const LABEL_ZOOM = 1.5;
const LABEL_ALL_MAX_NODES = 40;
const FIT_PADDING = 90;
const TRAIL_MS = 2600;
/** Room around the outermost ring for its label and its nodes' labels, in screen pixels. */
const RING_PADDING = 60;

/** `leftInset` is how much of the window's left edge a panel covers, like the drawer on the right. */
type Props = { graph: Graph; drawerWidth: number; layout: Layout; leftInset?: number };

export function GraphView({ graph, drawerWidth, layout, leftInset = 0 }: Props) {
  const nav = useNav();
  const [box, size] = useElementSize<HTMLDivElement>();
  const fg = useRef<ForceGraphMethods<SimNode, SimLink>>(undefined);
  const [hover, setHover] = useState<string | null>(null);
  const entry = nav.current;
  const selected = nav.open ? (entry?.ref.version ?? null) : null;

  const place = useMemo(() => placement(graph.nodes.map((n) => n.version), graph.edges), [graph]);
  // Every layout starts from the rings, so even the free layout starts untangled.
  const data = useMemo(() => {
    const d = toSimData(graph);
    for (const n of d.nodes) Object.assign(n, place.ring.get(n.id));
    return d;
  }, [graph, place]);
  const byId = useMemo(() => new Map(data.nodes.map((n) => [n.id, n])), [data]);
  const neighbours = useMemo(() => neighbourMap(graph.edges), [graph]);

  const settled = useRef(false);
  const stopped = useRef(false);

  // The free layout is force-directed. The others pull every node to its place and nothing else,
  // and glide there when the layout changes.
  const linkStrength = useRef<unknown>(undefined);
  useEffect(() => {
    const g = fg.current;
    if (!g) return;
    const free = layout === "free";
    const link = g.d3Force("link");
    linkStrength.current ??= link?.strength?.();
    link?.distance?.(64);
    link?.strength?.(free ? linkStrength.current : 0);
    g.d3Force("charge")?.strength?.(free ? -260 : 0);
    g.d3Force("center")?.strength?.(free ? 1 : 0);
    g.d3Force("place", free ? null : placeForce(layout === "rings" ? place.ring : place.column));
    stopped.current = false;
    g.d3ReheatSimulation();
  }, [layout, place, data]);

  // Frame the view: the selected node centred in the part of the window left of the drawer, or
  // everything when nothing is open. Kept in a ref so effects and engine callbacks see the latest.
  const frameView = useRef<(ms: number) => void>(() => {});
  frameView.current = (ms) => {
    const g = fg.current;
    if (!g || !settled.current) return;
    const n = selected ? byId.get(selected) : undefined;
    const outer = place.radii.at(-1) ?? 0;
    // Centred between the panels: the drawer's width (when open) and the left inset shift the centre.
    const shift = ((selected ? drawerWidth : 0) - leftInset) / 2 / g.zoom();
    if (n?.x !== undefined && n.y !== undefined) g.centerAt(n.x + shift, n.y, ms);
    else if (layout === "rings" && outer > 0) {
      // Fit the rings themselves, not just the nodes on them.
      g.centerAt(0, 0, ms);
      g.zoom(Math.max(0.1, (Math.min(size.width, size.height) / 2 - RING_PADDING) / outer), ms);
    } else g.zoomToFit(ms, FIT_PADDING);
  };
  useEffect(() => frameView.current(650), [selected, entry?.nonce]);
  useEffect(() => frameView.current(0), [size.width, size.height, drawerWidth, leftInset]);

  const focus = hover ?? selected;
  const lit = useMemo(() => (focus ? new Set([focus, ...(neighbours.get(focus) ?? [])]) : null), [focus, neighbours]);
  const recentTrail = useTrail(entry);
  // The trail only means something while the drawer shows where navigation went.
  const trail = selected ? recentTrail : null;
  // Label sides are worked out once per frame, before any node is painted.
  const labelSides = useRef<ReadonlyMap<string, LabelSide>>(new Map());
  const styleAt = (scale: number): DrawStyle => ({
    scale,
    labelAll: scale >= LABEL_ZOOM || data.nodes.length <= LABEL_ALL_MAX_NODES,
    focus,
    lit,
    selected,
    trail,
    labelSides: labelSides.current,
  });

  return (
    <div className="graph" ref={box} onPointerLeave={() => setHover(null)}>
      <ForceGraph2D<SimNode, SimLink>
        ref={fg}
        graphData={data}
        width={size.width}
        height={size.height}
        backgroundColor="rgba(0,0,0,0)"
        nodeRelSize={1}
        nodeVal={(n) => n.r * n.r}
        nodeLabel={() => ""} // labels are drawn on the canvas; no tooltips
        nodeCanvasObject={(n, ctx, scale) => drawNode(n, ctx, styleAt(scale))}
        nodePointerAreaPaint={drawNodeHitArea}
        onRenderFramePre={(ctx, scale) => {
          drawGuides(ctx, scale, layout, place, data.nodes);
          labelSides.current = placeLabels(data.nodes, ctx, styleAt(scale));
        }}
        linkCurvature={CURVATURE}
        linkCanvasObjectMode={() => "replace"}
        linkCanvasObject={(l, ctx, scale) => drawLink(l, ctx, styleAt(scale))}
        onRenderFramePost={(ctx, scale) => {
          for (const l of data.links) drawArrow(l, ctx, styleAt(scale));
        }}
        linkDirectionalParticles={(l) => (isOnTrail(l, trail) ? 3 : touches(l, focus) ? 2 : 0)}
        linkDirectionalParticleSpeed={(l) => (isOnTrail(l, trail) ? 0.012 : 0.006)}
        linkDirectionalParticleWidth={(l) => (isOnTrail(l, trail) ? 3.5 : 2.5)}
        linkDirectionalParticleColor={(l) => (isOnTrail(l, trail) ? ACCENT : INK.focusEdge)}
        onNodeClick={(n) => nav.navigate(n.id)}
        onNodeHover={(n) => setHover(n?.id ?? null)}
        onBackgroundClick={nav.close}
        warmupTicks={120}
        cooldownTicks={90}
        onEngineTick={() => {
          // Frame the pre-settled layout at once, then glide to the final one when it settles.
          if (settled.current) return;
          settled.current = true;
          frameView.current(0);
        }}
        onEngineStop={() => {
          if (stopped.current) return;
          stopped.current = true;
          frameView.current(500);
        }}
      />
    </div>
  );
}

/** The edge just traversed by navigation, for a short while after each visit. */
function useTrail(entry: NavEntry | null): Trail | null {
  const [trail, setTrail] = useState<Trail | null>(null);
  useEffect(() => {
    if (!entry?.from || entry.from === entry.ref.version) {
      setTrail(null);
      return;
    }
    setTrail({ a: entry.from, b: entry.ref.version });
    const timer = setTimeout(() => setTrail(null), TRAIL_MS);
    return () => clearTimeout(timer);
  }, [entry]);
  return trail;
}
