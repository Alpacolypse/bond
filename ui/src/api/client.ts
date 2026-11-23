// Typed client for the Bond viewer API (ui/API.md).
//
// The client is built over a `Backend` (the real server over HTTP, or the in-browser mock), which
// main.tsx picks and hands down through ApiProvider. Versions are immutable, so responses about them
// are cached by URL for the life of the client. The overview, the document list, the graph and
// neighborhoods are not: new writes change them.

import type { Annotations, Citation, DocumentPage, Graph, Neighborhood, Overview, VersionDetail } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** Where responses come from. */
export type Backend = {
  /** The parsed JSON body for `url`: "/api" + a route + its query string. */
  getJson(url: string): Promise<unknown>;
  /** A URL for the raw bytes of a version (fetched by pdf.js). */
  blobUrl(version: string): Promise<string>;
  /** The raw bytes themselves, from a backend whose URLs can't be fetched (the MCP app's sandbox). */
  blobData?(version: string): Promise<Uint8Array>;
};

export type Api = {
  /** Fetched afresh each time, like `documents`, `graph` and `neighborhood`: they change as the store is written. */
  overview(): Promise<Overview>;
  documents(q: string, offset: number, limit: number): Promise<DocumentPage>;
  graph(): Promise<Graph>;
  neighborhood(version: string, depth?: number): Promise<Neighborhood>;
  version(version: string): Promise<VersionDetail>;
  citations(version: string): Promise<Citation[]>;
  annotations(version: string): Promise<Annotations>;
  /** Every citation with a support anywhere in the version (the PDF viewer's boxes). */
  citedBy(version: string): Promise<Citation[]>;
  blobUrl(version: string): Promise<string>;
  /** Set when the backend hands over bytes: pdf.js is then given them rather than a URL. */
  blobData?(version: string): Promise<Uint8Array>;
};

const enc = encodeURIComponent;

/** The URL of an API route, with the defined params as its query string. */
export function apiUrl(path: string, params: Record<string, string | undefined> = {}): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined) query.set(key, value);
  const qs = query.toString();
  return `/api${path}${qs ? `?${qs}` : ""}`;
}

export function createApi(backend: Backend): Api {
  const cache = new Map<string, Promise<unknown>>();

  function cachedGet(url: string): Promise<unknown> {
    const cached = cache.get(url);
    if (cached) return cached;
    const request = backend.getJson(url);
    request.catch(() => cache.delete(url)); // let a failed request be retried
    cache.set(url, request);
    return request;
  }

  // The trust boundary: responses are typed by the API contract (API.md), not validated at runtime.
  const get = <T>(path: string, params?: Record<string, string | undefined>) =>
    cachedGet(apiUrl(path, params)) as Promise<T>;

  const fresh = <T>(path: string, params?: Record<string, string | undefined>) =>
    backend.getJson(apiUrl(path, params)) as Promise<T>;

  return {
    overview: () => fresh<Overview>("/overview"),
    documents: (q, offset, limit) =>
      fresh<DocumentPage>("/documents", { q: q || undefined, offset: String(offset), limit: String(limit) }),
    graph: () => fresh<Graph>("/graph"),
    neighborhood: (v, depth = 2) => fresh<Neighborhood>("/neighborhood", { version: v, depth: String(depth) }),
    version: (v) => get<VersionDetail>(`/versions/${enc(v)}`),
    citations: (v) => get<Citation[]>(`/versions/${enc(v)}/citations`),
    annotations: (v) => get<Annotations>(`/versions/${enc(v)}/annotations`),
    citedBy: (v) => get<Citation[]>("/cited-by", { version: v }),
    blobUrl: (v) => backend.blobUrl(v),
    blobData: backend.blobData?.bind(backend),
  };
}

/** The real bond.http.server, at the page's origin (Vite proxies /api in development). */
export function httpBackend(fetchFn: typeof fetch = fetch, workspace?: string): Backend {
  const scoped = (url: string) => {
    if (workspace === undefined) return url;
    const [path, query] = url.split("?");
    const params = new URLSearchParams(query);
    params.set("workspace", workspace);
    return `${path}?${params}`;
  };
  return {
    async getJson(url) {
      const res = await fetchFn(scoped(url));
      if (!res.ok) throw new ApiError(res.status, await errorDetail(res));
      return res.json();
    },
    blobUrl: async (v) => scoped(`/api/versions/${enc(v)}/blob`),
  };
}

/** The `detail` of a FastAPI-style JSON error body, else the status text. */
export async function errorDetail(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (body !== null && typeof body === "object" && "detail" in body && typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // not a JSON error body
  }
  return res.statusText;
}

/** The in-browser mock (src/mocks), imported on first use so it stays out of the main bundle. */
export const mockBackend: Backend = {
  getJson: async (url) => (await import("../mocks/server")).handle(url),
  blobUrl: async (v) => (await import("../mocks/server")).blobUrl(v),
};
