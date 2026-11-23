import { describe, expect, it } from "vitest";
import { ApiError, createApi } from "../api/client";
import { blobUrl, handle } from "./server";

// The mock answers from the bundled snapshot, so these check routing and shape, not exact data.
const api = createApi({ getJson: async (url) => handle(url), blobUrl: async (v) => blobUrl(v) });

async function versionOfFormat(format: string): Promise<string> {
  const node = (await api.graph()).nodes.find((n) => n.format === format);
  if (!node) throw new Error(`the snapshot has no ${format} version`);
  return node.version;
}

describe("mock server", () => {
  it("serves a graph whose edges join distinct nodes", async () => {
    const graph = await api.graph();
    const ids = new Set(graph.nodes.map((n) => n.version));
    expect(graph.nodes.length).toBeGreaterThan(0);
    for (const e of graph.edges) {
      expect(ids.has(e.from) && ids.has(e.to)).toBe(true);
      expect(e.from).not.toBe(e.to);
    }
  });

  it("routes a version and its sub-resources", async () => {
    const v = await versionOfFormat("markdown");
    expect((await api.version(v)).version).toBe(v);
    expect((await api.citations(v)).every((c) => c.owner_version === v)).toBe(true);
    expect((await api.annotations(v)).units.length).toBeGreaterThan(0);
  });

  it("finds what cites a version", async () => {
    const v = await versionOfFormat("pdf");
    const citing = await api.citedBy(v);
    expect(citing.length).toBeGreaterThan(0);
    expect(citing.every((c) => c.supports.some((s) => s.version === v))).toBe(true);
  });

  it("raises 404 for unknown versions and routes", () => {
    expect(() => handle("/api/versions/nope")).toThrow(ApiError);
    expect(() => handle("/api/nowhere")).toThrow(/no route/);
  });

  it("lists documents by name, filtered and paged", async () => {
    const all = await api.documents("", 0, 50);
    const names = all.documents.map((d) => d.name);
    expect(all.total).toBe(names.length);
    expect(names).toEqual([...names].sort());
    expect((await api.overview()).document_count).toBe(all.total);
    const fax = await api.documents("FAX", 0, 50);
    expect(fax.total).toBeGreaterThan(0);
    expect(fax.documents.every((d) => d.name.toLowerCase().includes("fax"))).toBe(true);
    const second = await api.documents("", 1, 1);
    expect(second.documents.map((d) => d.name)).toEqual(names.slice(1, 2));
  });

  it("serves the neighborhood of a version, within its depth", async () => {
    const pdf = await versionOfFormat("pdf");
    const near = await api.neighborhood(pdf, 1);
    const ids = new Set(near.nodes.map((n) => n.version));
    expect(near.center).toBe(pdf);
    expect(ids.has(pdf)).toBe(true);
    // Everything citing the PDF is one hop away; edges only join versions shown.
    for (const c of await api.citedBy(pdf)) expect(ids.has(c.owner_version)).toBe(true);
    for (const e of near.edges) expect(ids.has(e.from) && ids.has(e.to)).toBe(true);
    expect((await api.neighborhood(pdf, 0)).nodes.map((n) => n.version)).toEqual([pdf]);
    expect((await api.neighborhood(pdf, 3)).nodes.length).toBeGreaterThanOrEqual(ids.size);
  });

  it("serves blobs for PDFs only", async () => {
    expect(await api.blobUrl(await versionOfFormat("pdf"))).toEqual(expect.any(String));
    await expect(api.blobUrl(await versionOfFormat("markdown"))).rejects.toThrow(ApiError);
  });
});
