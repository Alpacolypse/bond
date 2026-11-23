// Adding source files to a workspace, from the MCP app (the add_sources tool; see app/mcp.ts). `bond view`
// only reads: people add sources there with `bond add`.

/** What adding sources answers: each file's document, and whether it was new (false: already held). */
export type AddResult = { added: { document: string; added: boolean }[]; note: string | null };

/** Adds `files` to a workspace; `update` adds a file whose name is taken as a new version of that document. */
export type AddSources = (files: File[], update: boolean) => Promise<AddResult>;

/** Whether an error refusing an upload says a name is taken, so adding as new versions would succeed. */
export function isNameTaken(message: string): boolean {
  return /\b(taken|already (has|holds|exists?)|exists already|in use)\b/i.test(message);
}

/** One line on what an add did. */
export function describeAdded({ added }: AddResult): string {
  const fresh = added.filter((a) => a.added).map((a) => a.document);
  const held = added.filter((a) => !a.added).map((a) => a.document);
  const parts = [];
  if (fresh.length) parts.push(`Added ${fresh.join(", ")}`);
  if (held.length) parts.push(`already held: ${held.join(", ")}`);
  return parts.length ? `${parts.join("; ")}.` : "Nothing added.";
}
