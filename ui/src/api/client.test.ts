import { describe, expect, it } from "vitest";
import { ApiError, apiUrl, createApi, httpBackend, type Backend } from "./client";

/** A backend that records requested URLs and answers each from `respond`. */
function recordingBackend(respond: (url: string) => Promise<unknown>) {
  const requested: string[] = [];
  const backend: Backend = {
    getJson: (url) => {
      requested.push(url);
      return respond(url);
    },
    blobUrl: async (v) => `blob:${v}`,
  };
  return { backend, requested };
}

describe("apiUrl", () => {
  it("adds only the defined params, encoded", () => {
    expect(apiUrl("/graph")).toBe("/api/graph");
    expect(apiUrl("/cited-by", { version: "a b", locator: undefined })).toBe("/api/cited-by?version=a+b");
  });
});

describe("createApi", () => {
  it("builds each route's URL, escaping the version", async () => {
    const { backend, requested } = recordingBackend(async () => ({}));
    const api = createApi(backend);
    await api.graph();
    await api.version("a/b");
    await api.citations("v");
    await api.annotations("v");
    await api.citedBy("v");
    expect(requested).toEqual([
      "/api/graph",
      "/api/versions/a%2Fb",
      "/api/versions/v/citations",
      "/api/versions/v/annotations",
      "/api/cited-by?version=v",
    ]);
    expect(await api.blobUrl("v")).toBe("blob:v");
  });

  it("caches responses by URL", async () => {
    const { backend, requested } = recordingBackend(async (url) => ({ url }));
    const api = createApi(backend);
    const first = await api.version("v");
    expect(await api.version("v")).toBe(first);
    expect(requested).toEqual(["/api/versions/v"]);
  });

  it("fetches what writes change afresh every time", async () => {
    const { backend, requested } = recordingBackend(async (url) => ({ url }));
    const api = createApi(backend);
    for (let i = 0; i < 2; i++) {
      await api.overview();
      await api.documents("", 0, 50);
      await api.neighborhood("v");
    }
    await api.documents("case 1", 50, 50);
    expect(requested).toEqual([
      "/api/overview",
      "/api/documents?offset=0&limit=50",
      "/api/neighborhood?version=v&depth=2",
      "/api/overview",
      "/api/documents?offset=0&limit=50",
      "/api/neighborhood?version=v&depth=2",
      "/api/documents?q=case+1&offset=50&limit=50",
    ]);
  });

  it("forgets a failed request so it can be retried", async () => {
    let fail = true;
    const { backend, requested } = recordingBackend(async () => {
      if (fail) throw new ApiError(500, "boom");
      return { nodes: [], edges: [] };
    });
    const api = createApi(backend);
    await expect(api.graph()).rejects.toThrow("boom");
    fail = false;
    expect(await api.graph()).toEqual({ nodes: [], edges: [] });
    expect(requested).toHaveLength(2);
  });
});

describe("httpBackend", () => {
  const respondWith = (response: Response) => async () => response;

  it("returns the parsed body of a successful response", async () => {
    const backend = httpBackend(respondWith(Response.json({ ok: 1 })));
    expect(await backend.getJson("/api/graph")).toEqual({ ok: 1 });
  });

  it("raises the status and the server's detail on an error", async () => {
    const backend = httpBackend(respondWith(Response.json({ detail: "unknown version x" }, { status: 404 })));
    const error = await backend.getJson("/api/versions/x").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, message: "unknown version x" });
  });

  it("falls back to the status text for a non-JSON error body", async () => {
    const backend = httpBackend(respondWith(new Response("oops", { status: 502, statusText: "Bad Gateway" })));
    await expect(backend.getJson("/api/graph")).rejects.toThrow("Bad Gateway");
  });

  it("serves blobs from the versions route", async () => {
    expect(await httpBackend(respondWith(new Response())).blobUrl("a/b")).toBe("/api/versions/a%2Fb/blob");
  });
});

describe("workspace isolation", () => {
  it("scopes JSON and PDF requests, preserves query parameters, and uses separate caches", async () => {
    const urls: string[] = [];
    const fetcher: typeof fetch = async (input) => {
      urls.push(String(input));
      return Response.json({ url: String(input) });
    };
    const a = createApi(httpBackend(fetcher, "case a"));
    const b = createApi(httpBackend(fetcher, "case b"));
    const first = await a.version("shared-policy");
    expect(await b.version("shared-policy")).not.toEqual(first);
    expect(await a.version("shared-policy")).toBe(first);
    await b.documents("fax", 0, 50);
    await b.citedBy("pdf");
    expect(urls).toEqual([
      "/api/versions/shared-policy?workspace=case+a",
      "/api/versions/shared-policy?workspace=case+b",
      "/api/documents?q=fax&offset=0&limit=50&workspace=case+b",
      "/api/cited-by?version=pdf&workspace=case+b",
    ]);
    expect(await b.blobUrl("pdf")).toBe("/api/versions/pdf/blob?workspace=case+b");
  });
});
