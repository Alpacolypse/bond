// Types for the Bond viewer HTTP API. Source of truth: ui/API.md.

export type TextLocator = { type: "text"; start: number; end: number };
/** A JSON pointer, optionally narrowed to a code point range inside a string value (both bounds or neither). */
export type JsonLocator =
  | { type: "json"; pointer: string; start?: undefined; end?: undefined }
  | { type: "json"; pointer: string; start: number; end: number };
/** A table's cell, row or column, 0-based; a cell may be narrowed to a code point range (both bounds or neither). */
export type CsvLocator = { type: "csv"; row?: number; column?: number; start?: number; end?: number };
export type PdfLocator = { type: "pdf"; page: number; bbox: [number, number, number, number] };
export type VersionLocator = { type: "version" };
export type Locator = TextLocator | JsonLocator | CsvLocator | PdfLocator | VersionLocator;

export type Ref = { version: string; locator: Locator };

export const FORMATS = ["markdown", "json", "csv", "tsv", "pdf"] as const;
export type Format = (typeof FORMATS)[number];

export type Kind =
  | "transcription"
  | "quote"
  | "paraphrase"
  | "unsourced"
  | "source"
  | "classification"
  | "assessment";

export type VersionSummary = {
  version: string;
  /** sha256 of the version's bytes; versions with the same bytes share it. */
  content_hash: string;
  /** The version's home: the first document it was filed in. */
  name: string;
  /** The home document's title for people, if it has one. */
  title: string | null;
  /** Every document whose history holds the version. */
  documents: string[];
  format: Format;
  created_at: string;
  version_index: number;
  version_count: number;
  latest: boolean;
};

export type Support = {
  version: string;
  name: string;
  /** null for a version outside the workspace: nothing of it is here. */
  format: Format | null;
  locator: Locator;
  text: string | null;
  /** The version stayed in the workspace this was imported from: it can't be opened here. */
  outside: boolean;
};

export type Citation = {
  id: string;
  owner_version: string;
  kind: Kind;
  claim: Locator;
  claim_text: string | null;
  supports: Support[];
  attrs: Record<string, unknown>;
};

export type GraphNode = Omit<VersionSummary, "format" | "content_hash" | "created_at"> & {
  /** null only for a version outside the workspace, of which only the id is known here. */
  format: Format | null;
  content_hash: string | null;
  created_at: string | null;
  citation_count: number;
  self_citation_count: number;
  is_source: boolean;
  /** Something here rests on this version, but an import left it in the workspace it came from. */
  outside: boolean;
};

export type GraphEdge = {
  id: string;
  from: string;
  to: string;
  count: number;
  kinds: Partial<Record<Kind, number>>;
};

export type Graph = { nodes: GraphNode[]; edges: GraphEdge[] };

export type Workspace = { id: string; name: string; created_at: string };

/** Which workspace this is and how big: past `graph_limit` versions it is browsed by document, not drawn whole. */
export type Overview = {
  workspace: Workspace;
  version_count: number;
  document_count: number;
  /** Versions something here rests on that an import left outside. */
  outside_count: number;
  graph_limit: number;
};

export type DocumentItem = {
  id: string;
  name: string;
  /** Its title for people, if it has one. */
  title: string | null;
  /** The version the document points at now. */
  head: string;
  format: Format;
  created_at: string;
  /** When the document last moved. */
  updated_at: string;
  /** How many distinct versions its history holds. */
  version_count: number;
  /** The name of the workspace its current version was imported from, if it was. */
  imported_from: string | null;
};

export type DocumentPage = { total: number; offset: number; documents: DocumentItem[] };

/** The versions within `depth` citation hops of `center`, either way; `truncated` if some were left out. */
export type Neighborhood = Graph & { center: string; depth: number; truncated: boolean };

export type VersionDetail = VersionSummary & {
  content: string | null;
  siblings: VersionSummary[];
};

/** A claim unit and the kinds of the citations covering it (empty if uncited). */
export type AnnotationUnit = { locator: Locator; kinds: Kind[]; text: string | null };
/** Claim units, and gaps: the exact text in them that no citation covers. */
export type Annotations = { units: AnnotationUnit[]; gaps: Locator[] };
