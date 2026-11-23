// Builders for API objects in tests: sensible defaults, overridden per test.

import type { Citation, GraphEdge, GraphNode } from "../api/types";

export function makeCitation(overrides: Partial<Citation> = {}): Citation {
  return {
    id: "c0",
    owner_version: "v",
    kind: "quote",
    claim: { type: "version" },
    claim_text: null,
    supports: [],
    attrs: {},
    ...overrides,
  };
}

export function makeNode(version: string, overrides: Partial<GraphNode> = {}): GraphNode {
  return {
    version,
    content_hash: "",
    name: version,
    title: null,
    documents: [version],
    format: "markdown",
    created_at: "",
    version_index: 1,
    version_count: 1,
    latest: true,
    citation_count: 0,
    self_citation_count: 0,
    is_source: false,
    outside: false,
    ...overrides,
  };
}

export function makeEdge(from: string, to: string): GraphEdge {
  return { id: `${from}->${to}`, from, to, count: 1, kinds: { quote: 1 } };
}
