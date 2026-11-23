import type { CallToolResult } from "@modelcontextprotocol/client";
import { describe, expect, it } from "vitest";
import { ApiError, createApi } from "../api/client";
import {
  base64ToBytes,
  bytesToBase64,
  callTool,
  fileToBase64,
  mcpAddSources,
  mcpBackend,
  PART,
  personSecret,
  ToolError,
  type ToolHost,
  uploadId,
} from "./mcp";

type Call = { name: string; arguments: Record<string, unknown> | undefined };

/** A stand-in for the App: records each tool call and answers it from `respond`. */
function fakeHost(respond: (call: Call) => CallToolResult) {
  const calls: Call[] = [];
  const host: ToolHost = {
    callServerTool: async (params) => {
      const call = { name: params.name, arguments: params.arguments };
      calls.push(call);
      return respond(call);
    },
  };
  return { host, calls };
}

const ok = (structuredContent: Record<string, unknown>): CallToolResult => ({ content: [], structuredContent });
const failed = (text: string): CallToolResult => ({ content: [{ type: "text", text }], isError: true });
const bytes = (s: string) => new TextEncoder().encode(s);

describe("callTool", () => {
  it("returns the structured content", async () => {
    const { host, calls } = fakeHost(() => ok({ workspace: "smith" }));
    expect(await callTool(host, "grant_workspace", { name: "smith" })).toEqual({ workspace: "smith" });
    // No page in a test, so no secret: an acting tool is sent the empty one, which the server refuses.
    expect(calls).toEqual([{ name: "grant_workspace", arguments: { name: "smith", person: "" } }]);
  });

  it("falls back to the text content as JSON", async () => {
    const { host } = fakeHost(() => ({ content: [{ type: "text", text: '{"workspace": "smith"}' }] }));
    expect(await callTool(host, "grant_workspace", { name: "smith" })).toEqual({ workspace: "smith" });
  });

  it("raises a failed tool's message", async () => {
    const { host } = fakeHost(() => failed("no workspace named smith"));
    const error = await callTool(host, "grant_workspace", { name: "smith" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ToolError);
    expect(error).toMatchObject({ message: "no workspace named smith" });
  });
});

describe("mcpBackend", () => {
  it("passes each API URL to view_api for the workspace, and returns the body", async () => {
    const { host, calls } = fakeHost(({ arguments: args }) => ok({ status: 200, body: { url: args?.url } }));
    const api = createApi(mcpBackend(host, "smith"));
    expect(await api.citations("a/b")).toEqual({ url: "/api/versions/a%2Fb/citations" });
    await api.documents("summary", 0, 500);
    expect(calls).toEqual([
      { name: "view_api", arguments: { workspace: "smith", url: "/api/versions/a%2Fb/citations" } },
      { name: "view_api", arguments: { workspace: "smith", url: "/api/documents?q=summary&offset=0&limit=500" } },
    ]);
  });

  it("raises an ApiError with the detail for a status of 400 or more", async () => {
    const { host } = fakeHost(() => ok({ status: 404, body: { detail: "unknown version x" } }));
    const error = await mcpBackend(host, "smith")
      .getJson("/api/versions/x")
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, message: "unknown version x" });
  });

  it("raises an ApiError with the status when the error body has no detail", async () => {
    const { host } = fakeHost(() => ok({ status: 500, body: null }));
    await expect(mcpBackend(host, "smith").getJson("/api/graph")).rejects.toMatchObject({ status: 500 });
  });

  it("returns a body under 400 as is, even an error-shaped one", async () => {
    const { host } = fakeHost(() => ok({ status: 200, body: { detail: "fine" } }));
    expect(await mcpBackend(host, "smith").getJson("/api/text")).toEqual({ detail: "fine" });
  });

  it("raises a failed view_api call as a ToolError", async () => {
    const { host } = fakeHost(() => failed("workspace smith is not granted"));
    await expect(mcpBackend(host, "smith").getJson("/api/graph")).rejects.toBeInstanceOf(ToolError);
  });

  // "%PDF-1.7\n" in three chunks: [0, 4), [4, 7), [7, 9).
  const chunks: Record<number, Record<string, unknown>> = {
    0: { data: bytesToBase64(bytes("%PDF")), offset: 0, next: 4, size: 9, media_type: "application/pdf" },
    4: { data: bytesToBase64(bytes("-1.")), offset: 4, next: 7, size: 9, media_type: "application/pdf" },
    7: { data: bytesToBase64(bytes("7\n")), offset: 7, next: null, size: 9, media_type: "application/pdf" },
  };
  const blobHost = () => fakeHost(({ arguments: args }) => ok(chunks[args?.offset as number] ?? {}));

  it("assembles a version's bytes from view_blob chunks, following next", async () => {
    const { host, calls } = blobHost();
    const data = await mcpBackend(host, "smith").blobData!("v1");
    expect(new TextDecoder().decode(data)).toBe("%PDF-1.7\n");
    expect(calls).toEqual([0, 4, 7].map((offset) => ({ name: "view_blob", arguments: { workspace: "smith", version: "v1", offset } })));
  });

  it("serves the assembled bytes as an object URL of the media type, reading them once", async () => {
    const { host, calls } = blobHost();
    const backend = mcpBackend(host, "smith");
    const url = await backend.blobUrl("v1");
    expect(url).toMatch(/^blob:/);
    const blob = await (await fetch(url)).blob();
    expect(blob.type).toBe("application/pdf");
    expect(await blob.text()).toBe("%PDF-1.7\n");
    await backend.blobData!("v1");
    expect(calls).toHaveLength(3);
    URL.revokeObjectURL(url);
  });

  it("refuses a chunk that does not advance", async () => {
    const { host } = fakeHost(() => ok({ data: "", offset: 0, next: 0, size: 9, media_type: "application/pdf" }));
    await expect(mcpBackend(host, "smith").blobData!("v1")).rejects.toThrow("did not advance");
  });
});

describe("mcpAddSources", () => {
  it("sends each file up as an upload, small ones in one part, then adds them with the update flag", async () => {
    const { host, calls } = fakeHost((c) =>
      c.name === "upload_part" ? ok({ upload: c.arguments?.upload, size: 0 }) : ok({ added: [{ document: "fax", added: true }], note: null }),
    );
    const files = [new File([bytes("hello")], "fax.pdf"), new File([bytes("{}")], "notes.json")];
    let n = 0;
    const result = await mcpAddSources(host, "smith", () => `u-test000${++n}`)(files, true);
    expect(result).toEqual({ added: [{ document: "fax", added: true }], note: null });
    expect(calls).toEqual([
      { name: "upload_part", arguments: { upload: "u-test0001", offset: 0, data: "aGVsbG8=", person: "" } },
      { name: "upload_part", arguments: { upload: "u-test0002", offset: 0, data: "e30=", person: "" } },
      {
        name: "add_sources",
        arguments: {
          workspace: "smith",
          files: [
            { name: "fax.pdf", upload: "u-test0001" },
            { name: "notes.json", upload: "u-test0002" },
          ],
          update: true,
          person: "",
        },
      },
    ]);
  });

  it("sends a big file up in parts and then adds it by its upload", async () => {
    const { host, calls } = fakeHost((c) =>
      c.name === "upload_part" ? ok({ upload: c.arguments?.upload, size: 0 }) : ok({ added: [], note: null }),
    );
    const big = Uint8Array.from({ length: PART * 2 + 5 }, (_, i) => (i * 7) % 256);
    await mcpAddSources(host, "smith", () => "u-test0001")([new File([big], "scan.pdf")], false);
    expect(calls.map((c) => [c.name, c.arguments?.offset])).toEqual([
      ["upload_part", 0],
      ["upload_part", PART],
      ["upload_part", PART * 2],
      ["add_sources", undefined],
    ]);
    const parts = calls.slice(0, 3).map((c) => base64ToBytes(c.arguments?.data as string));
    expect(parts.map((p) => p.length)).toEqual([PART, PART, 5]);
    expect(new Uint8Array(parts.flatMap((p) => Array.from(p)))).toEqual(big);
    expect(calls.at(-1)?.arguments).toEqual({ workspace: "smith", files: [{ name: "scan.pdf", upload: "u-test0001" }], update: false, person: "" });
  });

  it("makes upload ids the server accepts", () => {
    expect(uploadId()).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
    expect(uploadId()).not.toBe(uploadId());
  });

  it("raises the server's message when a name is taken", async () => {
    const { host } = fakeHost(() => failed("fax is taken: pass update to add a new version"));
    await expect(mcpAddSources(host, "smith")([new File(["x"], "fax.pdf")], false)).rejects.toThrow("fax is taken");
  });
});

describe("acting as the person", () => {
  it("sends the page's secret with the tools that act as the person, and only with them", async () => {
    const { host, calls } = fakeHost(() => ok({ workspace: "smith" }));
    await callTool(host, "grant_workspace", { name: "smith", granted: true }, "s3cret");
    await callTool(host, "view_workspaces", {}, "s3cret");
    expect(calls.map((c) => c.arguments)).toEqual([{ name: "smith", granted: true, person: "s3cret" }, {}]);
  });

  it("reads the secret from the page", () => {
    const page = { querySelector: () => ({ getAttribute: () => "abc" }) } as unknown as Pick<Document, "querySelector">;
    expect(personSecret(page)).toBe("abc");
    expect(personSecret({ querySelector: () => null } as unknown as Pick<Document, "querySelector">)).toBe("");
  });
});

describe("base64", () => {
  it("round-trips every byte value", () => {
    const all = Uint8Array.from({ length: 256 }, (_, i) => i);
    expect(base64ToBytes(bytesToBase64(all))).toEqual(all);
  });

  it("encodes like btoa, across slice boundaries", () => {
    const big = Uint8Array.from({ length: 100_003 }, (_, i) => (i * 31) % 256);
    expect(bytesToBase64(big)).toBe(Buffer.from(big).toString("base64"));
    expect(bytesToBase64(new Uint8Array())).toBe("");
  });

  it("reads a file", async () => {
    expect(await fileToBase64(new Blob([bytes("hello")]))).toBe("aGVsbG8=");
  });
});
