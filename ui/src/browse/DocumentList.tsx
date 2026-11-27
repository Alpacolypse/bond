// The store's documents as a filterable, paged list: how a store too big to draw whole is entered.
// Opening one shows its head in the drawer, and the graph around it.

import { useEffect, useState } from "react";
import { useApi } from "../api/ApiContext";
import type { DocumentItem } from "../api/types";
import { useAsync } from "../api/useAsync";
import { FormatDot } from "../nav/chips";

const PAGE = 50;
const FILTER_DELAY_MS = 200;

/** `refresh` changing fetches the page again (after sources are added). */
export function DocumentList({
  selected,
  onOpen,
  refresh = 0,
}: {
  selected: string | null;
  onOpen(doc: DocumentItem): void;
  refresh?: number;
}) {
  const api = useApi();
  const [typed, setTyped] = useState("");
  const q = useDebounced(typed.trim(), FILTER_DELAY_MS);
  const [offset, setOffset] = useState(0);
  useEffect(() => setOffset(0), [q]);
  const page = useAsync(() => api.documents(q, offset, PAGE), [api, q, offset, refresh]);
  const docs = page.data?.documents ?? [];
  const total = page.data?.total ?? 0;

  return (
    <nav className="doclist" aria-label="Documents">
      <input
        className="doclist-filter"
        type="search"
        placeholder="Filter documents"
        value={typed}
        onChange={(e) => setTyped(e.target.value)}
      />
      <div className="doclist-count muted">
        {page.error ? (
          <span className="error">{page.error.message}</span>
        ) : page.data ? (
          `${total} document${total === 1 ? "" : "s"}${q ? ` matching “${q}”` : ""}`
        ) : (
          "Loading…"
        )}
      </div>
      <ul className="doclist-items">
        {docs.map((d) => (
          <li key={d.id}>
            <button
              type="button"
              className={d.head === selected ? "on" : undefined}
              onClick={() => onOpen(d)}
              title={d.title ? `${d.title} (${d.name})` : d.name}
            >
              <FormatDot format={d.format} />
              <span className="doclist-name">{d.title ?? d.name}</span>
              {d.version_count > 1 && <span className="muted">v{d.version_count}</span>}
            </button>
          </li>
        ))}
      </ul>
      {total > PAGE && (
        <div className="doclist-pager">
          <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
            ‹ Prev
          </button>
          <span className="muted">
            {offset + 1}–{offset + docs.length} of {total}
          </span>
          <button type="button" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
            Next ›
          </button>
        </div>
      )}
    </nav>
  );
}

/** `value`, once it has stopped changing for `ms`. */
function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}
