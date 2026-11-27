// Navigation state: which ref the drawer shows, with back/forward history, and whether the drawer
// is open. Closing keeps the history (and the last entry, so the drawer can slide out with it).

import { createContext, useContext, useMemo, useReducer, type ReactNode } from "react";
import type { GraphNode, Locator, Ref } from "../api/types";

/**
 * One stop in the history. `nonce` changes on every visit so the viewer re-flashes the target;
 * `from` is the version shown just before (null if the drawer was closed), for the graph's trail.
 */
export type NavEntry = { ref: Ref; nonce: number; from: string | null };

type State = { history: NavEntry[]; index: number; open: boolean; nonce: number };

type Action = { type: "navigate"; ref: Ref } | { type: "go"; delta: number } | { type: "close" };

export const initialNav: State = { history: [], index: -1, open: false, nonce: 0 };

export function navReducer(s: State, a: Action): State {
  const shown = s.open ? (s.history[s.index]?.ref.version ?? null) : null;
  switch (a.type) {
    case "navigate": {
      const nonce = s.nonce + 1;
      const history = [...s.history.slice(0, s.index + 1), { ref: a.ref, nonce, from: shown }];
      return { history, index: history.length - 1, open: true, nonce };
    }
    case "go": {
      const index = Math.max(0, Math.min(s.history.length - 1, s.index + a.delta));
      if (index === s.index) return s;
      const nonce = s.nonce + 1;
      const history = s.history.map((e, i) => (i === index ? { ...e, nonce, from: shown } : e));
      return { history, index, open: true, nonce };
    }
    case "close":
      return s.open ? { ...s, open: false } : s;
  }
}

type Nav = {
  /** The latest entry, kept after the drawer closes. */
  current: NavEntry | null;
  open: boolean;
  canBack: boolean;
  canForward: boolean;
  navigate(version: string, locator?: Locator): void;
  back(): void;
  forward(): void;
  close(): void;
  /** Every version in the store, by hash (from /api/graph), for names and formats. */
  nodes: ReadonlyMap<string, GraphNode>;
  /** A version's display name. */
  name(version: string): string;
};

const NavCtx = createContext<Nav | null>(null);

export function NavProvider({ nodes, children }: { nodes: ReadonlyMap<string, GraphNode>; children: ReactNode }) {
  const [s, dispatch] = useReducer(navReducer, initialNav);
  const nav = useMemo<Nav>(
    () => ({
      current: s.history[s.index] ?? null,
      open: s.open,
      canBack: s.index > 0,
      canForward: s.index < s.history.length - 1,
      // An unreachable version has nothing here to open.
      navigate: (version, locator = { type: "version" }) => {
        if (!nodes.get(version)?.unreachable) dispatch({ type: "navigate", ref: { version, locator } });
      },
      back: () => dispatch({ type: "go", delta: -1 }),
      forward: () => dispatch({ type: "go", delta: 1 }),
      close: () => dispatch({ type: "close" }),
      nodes,
      name: (v) => {
        const n = nodes.get(v);
        return n ? (n.title ?? n.name) : v.slice(0, 12);
      },
    }),
    [s, nodes],
  );
  return <NavCtx.Provider value={nav}>{children}</NavCtx.Provider>;
}

export function useNav(): Nav {
  const nav = useContext(NavCtx);
  if (!nav) throw new Error("useNav outside NavProvider");
  return nav;
}
