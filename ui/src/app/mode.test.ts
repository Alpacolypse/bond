import type { CallToolResult } from "@modelcontextprotocol/client";
import { describe, expect, it } from "vitest";
import { grantsContext, viewFor } from "./mode";

const ok = (structuredContent: Record<string, unknown>): CallToolResult => ({ content: [], structuredContent });

describe("viewFor", () => {
  const input = { workspace: "smith", document: "summary" };

  it("waits for the result", () => {
    expect(viewFor(input, undefined)).toEqual({ kind: "waiting" });
  });

  it("shows a document after show", () => {
    const shown = ok({ document: "summary", support: "partially_supported", status: "shown" });
    expect(viewFor(input, shown)).toEqual({
      kind: "document",
      workspace: "smith",
      document: "summary",
      support: "partially_supported",
      written: false,
    });
  });

  it("shows a document after a write that went through", () => {
    const written = ok({ status: "written", base: "v2", advanced: true, support: "supported" });
    expect(viewFor(input, written)).toMatchObject({ kind: "document", document: "summary", written: true });
  });

  it("lists a refused write's issues", () => {
    const refused = ok({ status: "refused", issues: ["claim 2 cites nothing", "unknown handle fax@9"] });
    expect(viewFor(input, refused)).toEqual({
      kind: "refused",
      document: "summary",
      support: null,
      issues: ["claim 2 cites nothing", "unknown handle fax@9"],
    });
  });

  it("shows the picker after workspaces", () => {
    expect(viewFor({}, ok({ workspaces: ["smith"] }))).toEqual({ kind: "picker" });
  });

  it("shows a failed call's message", () => {
    const failed: CallToolResult = { content: [{ type: "text", text: "no document summary" }], isError: true };
    expect(viewFor(input, failed)).toEqual({ kind: "error", message: "no document summary" });
  });

  it("ignores an unknown support status", () => {
    expect(viewFor(input, ok({ status: "written", support: "great" }))).toMatchObject({ support: null });
  });
});

describe("grantsContext", () => {
  it("tells the model every grant and revocation", () => {
    const changes = new Map([
      ["smith", "granted"],
      ["old", "revoked"],
    ] as const);
    expect(grantsContext(changes)).toBe(
      `The person granted Bond workspace "smith"; pass workspace="smith" to Bond's tools.\n` +
        `The person revoked Bond workspace "old"; Bond's tools can no longer work in it.`,
    );
  });
});
