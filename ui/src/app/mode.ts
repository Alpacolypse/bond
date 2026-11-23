// What the app view shows, from the tool call that opened it: its input (workspace, document) and its
// result. Its status says what happened (shown, written, refused, checked), and the workspaces tool's
// result is a list; the tool's name in the host context, when the host sends it, is only a tiebreaker.

import type { CallToolResult } from "@modelcontextprotocol/client";
import { resultText, structured } from "./mcp";

export const SUPPORTS = ["supported", "partially_supported", "unsupported", "unknown", "not_applicable"] as const;
export type Support = (typeof SUPPORTS)[number];

export type View =
  | { kind: "waiting" }
  | { kind: "error"; message: string }
  /** A document shown or written: a card that opens the viewer at its current version. */
  | {
      kind: "document";
      workspace: string | null;
      document: string;
      support: Support | null;
      written: boolean;
    }
  /** A write or edit refused (or only checked, with `dry_run`): its issues, nothing to open. */
  | { kind: "refused" | "checked"; document: string | null; support: Support | null; issues: string[] }
  | { kind: "picker" };

type Json = Record<string, unknown>;

const isObject = (v: unknown): v is Json => v !== null && typeof v === "object" && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
const support = (v: unknown): Support | null => (SUPPORTS as readonly unknown[]).includes(v) ? (v as Support) : null;
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : []);

export function viewFor(input: Json | undefined, result: CallToolResult | undefined, tool?: string): View {
  if (!result) return { kind: "waiting" };
  if (result.isError) return { kind: "error", message: resultText(result) || "The tool failed." };
  const out = structured(result);
  if (!isObject(out)) return { kind: "error", message: "The tool returned nothing to show." };
  const workspace = str(input?.workspace) ?? str(out.workspace);
  const document = str(input?.document) ?? str(out.document);

  if (Array.isArray(out.workspaces) || tool === "workspaces") return { kind: "picker" };
  if (out.status === "refused" || out.status === "checked") {
    return { kind: out.status, document, support: support(out.support), issues: strings(out.issues) };
  }
  if (document && (out.status === "written" || out.status === "shown")) {
    return {
      kind: "document",
      workspace,
      document: str(out.document) ?? document,
      support: support(out.support),
      written: out.status === "written",
    };
  }
  return { kind: "error", message: "This tool's result has nothing for this view to show." };
}

export const SUPPORT_LABEL: Record<Support, string> = {
  supported: "supported",
  partially_supported: "partially supported",
  unsupported: "unsupported",
  unknown: "support unknown",
  not_applicable: "not applicable",
};

/** What the person did to a workspace in the picker. */
export type Change = "granted" | "revoked";

/** Model context saying what the person granted and revoked in the picker, so the agent can act on it. */
export function grantsContext(changes: ReadonlyMap<string, Change>): string {
  return [...changes]
    .map(([name, change]) =>
      change === "granted"
        ? `The person granted Bond workspace "${name}"; pass workspace="${name}" to Bond's tools.`
        : `The person revoked Bond workspace "${name}"; Bond's tools can no longer work in it.`,
    )
    .join("\n");
}
