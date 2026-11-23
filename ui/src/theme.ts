// One palette for the whole UI, so a sourcing or format reads the same in every view.

import type { Format } from "./api/types";

/** How text is accounted for, for display: derived from the kinds of the citations covering it. */
export const SOURCINGS = ["cited", "source", "unsourced", "uncited"] as const;
export type Sourcing = (typeof SOURCINGS)[number];

export const SOURCING_COLOR: Record<Sourcing, string> = {
  cited: "#4d7c5b",
  source: "#4a6fa5",
  unsourced: "#b07a2a",
  uncited: "#c0504d",
};

export const SOURCING_LABEL: Record<Sourcing, string> = {
  cited: "cited: declares support from another region; correctness is not evaluated",
  source: "declared source: origin of evidence; authority is not evaluated",
  unsourced: "unsourced: no supporting evidence declared",
  uncited: "undeclared: no citation covers this text",
};

export const FORMAT_COLOR: Record<Format, string> = {
  markdown: "#7b6fd6",
  json: "#2f9e8f",
  csv: "#4f8fd1",
  tsv: "#4f8fd1",
  pdf: "#e07b5f",
};

/** A version outside the workspace: something here rests on it, but only its id is here. */
export const OUTSIDE_COLOR = "#a3a9b4";

/** A format's colour, or the outside colour for a version of which nothing is here. */
export const formatColor = (format: Format | null) => (format === null ? OUTSIDE_COLOR : FORMAT_COLOR[format]);

/** Marks where navigation went: the graph edge just traversed (the CSS --accent flashes the target). */
export const ACCENT = "#e8a33d";
