// Talking to Bond's MCP server from the app view, through the host: tool calls with their results'
// structured content, the viewer's API over the app-only `view_api` and `view_blob` tools, and
// adding sources with `add_sources`.

import type { App } from "@modelcontextprotocol/ext-apps";
import type { CallToolResult } from "@modelcontextprotocol/client";
import { ApiError, type Backend } from "../api/client";
import type { AddResult, AddSources } from "../upload/sources";

/** What the view needs of the ext-apps App to call tools (so tests can stand in for it). */
export type ToolHost = Pick<App, "callServerTool">;

/** A tool answered with `isError`: its message is the result's text. */
export class ToolError extends Error {}

/** A tool result's text content, joined. */
export function resultText(result: CallToolResult): string {
  return result.content
    .flatMap((c) => (c.type === "text" ? [c.text] : []))
    .join("\n")
    .trim();
}

/** A tool result's structured content, else its text parsed as JSON (for hosts that drop structuredContent). */
export function structured(result: CallToolResult): unknown {
  if (result.structuredContent !== undefined) return result.structuredContent;
  try {
    return JSON.parse(resultText(result));
  } catch {
    return undefined;
  }
}

/** The tools that act as the person (add sources, grant, revoke, create): the server wants its secret with each. */
export const AS_PERSON = new Set(["add_sources", "upload_part", "grant_workspace", "new_workspace"]);

/**
 * The secret the server wrote into this page (`<meta name="bond-person">`). Hosts render the page but don't give
 * it to their models, so with it the server knows a call comes from the person using the view.
 */
export function personSecret(doc: Pick<Document, "querySelector"> | undefined = globalThis.document): string {
  return doc?.querySelector('meta[name="bond-person"]')?.getAttribute("content") ?? "";
}

/** Call a server tool, returning its structured content, or throwing a ToolError if it failed. */
export async function callTool<T>(
  host: ToolHost,
  name: string,
  args: Record<string, unknown> = {},
  person: string = personSecret(),
): Promise<T> {
  const sent = AS_PERSON.has(name) ? { ...args, person } : args;
  const result = await host.callServerTool({ name, arguments: sent });
  if (result.isError) throw new ToolError(resultText(result) || `${name} failed`);
  return structured(result) as T;
}

type ViewApiResult = { status: number; body: unknown };
type ViewBlobResult = { data: string; offset: number; next: number | null; size: number; media_type: string };

/**
 * The viewer's Backend over MCP, for one workspace: each API URL is answered by `view_api` (the same
 * handlers as bond view), and a version's bytes are read in chunks with `view_blob`.
 */
export function mcpBackend(host: ToolHost, workspace: string): Backend {
  const blobs = new Map<string, Promise<Blob>>();

  function blob(version: string): Promise<Blob> {
    let cached = blobs.get(version);
    if (!cached) {
      cached = readBlob(host, workspace, version);
      cached.catch(() => blobs.delete(version)); // let a failed read be retried
      blobs.set(version, cached);
    }
    return cached;
  }

  return {
    async getJson(url) {
      const { status, body } = await callTool<ViewApiResult>(host, "view_api", { workspace, url });
      if (status >= 400) throw new ApiError(status, errorMessage(body, status));
      return body;
    },
    blobUrl: async (version) => URL.createObjectURL(await blob(version)),
    blobData: async (version) => new Uint8Array(await (await blob(version)).arrayBuffer()),
  };
}

/** A version's bytes, read chunk by chunk until `view_blob` says there is no next offset. */
async function readBlob(host: ToolHost, workspace: string, version: string): Promise<Blob> {
  const parts: Uint8Array<ArrayBuffer>[] = [];
  let offset: number | null = 0;
  let type = "application/octet-stream";
  while (offset !== null) {
    const chunk: ViewBlobResult = await callTool<ViewBlobResult>(host, "view_blob", { workspace, version, offset });
    parts.push(base64ToBytes(chunk.data));
    type = chunk.media_type || type;
    if (chunk.next !== null && chunk.next <= offset) throw new Error(`view_blob did not advance past ${offset}`);
    offset = chunk.next;
  }
  return new Blob(parts, { type });
}

function errorMessage(body: unknown, status: number): string {
  if (body !== null && typeof body === "object" && "detail" in body && typeof body.detail === "string") return body.detail;
  return `HTTP ${status}`;
}

/** Bytes per tool call, as the server sends view_blob: 128k characters of base64, under hosts' ~150k limit. */
export const PART = 96 * 1024;

/** A fresh upload id (8–64 of [A-Za-z0-9_-]); crypto.randomUUID needs a secure context, getRandomValues doesn't. */
export function uploadId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return "u-" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Adding sources to `workspace` with `add_sources`. Each file first goes up in parts with `upload_part`, as
 * `view_blob` brings bytes down, so no tool call outgrows the host's limit; add_sources then names the uploads.
 */
export function mcpAddSources(host: ToolHost, workspace: string, newId: () => string = uploadId): AddSources {
  return async (files, update) => {
    const sent = [];
    for (const f of files) {
      const bytes = new Uint8Array(await f.arrayBuffer());
      const upload = newId();
      for (let offset = 0; offset === 0 || offset < bytes.length; offset += PART) {
        await callTool(host, "upload_part", { upload, offset, data: bytesToBase64(bytes.subarray(offset, offset + PART)) });
      }
      sent.push({ name: f.name, upload });
    }
    return callTool<AddResult>(host, "add_sources", { workspace, files: sent, update });
  };
}

// --- base64 ------------------------------------------------------------------------------------

/** Bytes as base64 (in slices, so a large file doesn't overflow the call stack). */
export function bytesToBase64(bytes: Uint8Array): string {
  const SLICE = 0x8000;
  let binary = "";
  for (let i = 0; i < bytes.length; i += SLICE) binary += String.fromCharCode(...bytes.subarray(i, i + SLICE));
  return btoa(binary);
}

export function base64ToBytes(data: string): Uint8Array<ArrayBuffer> {
  const binary = atob(data);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

export async function fileToBase64(file: Blob): Promise<string> {
  return bytesToBase64(new Uint8Array(await file.arrayBuffer()));
}
