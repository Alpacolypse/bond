import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from "react";
import { ApiProvider, useApi } from "./api/ApiContext";
import { createApi, httpBackend, type Api } from "./api/client";
import type { Graph, GraphNode, Neighborhood, Overview, Workspace } from "./api/types";
import { useAsync, useLatest } from "./api/useAsync";
import { DocumentList } from "./browse/DocumentList";
import { Drawer, defaultDrawerWidth } from "./drawer/Drawer";
import { GraphView } from "./graph/GraphView";
import type { Layout } from "./graph/layout";
import { LayoutSwitch } from "./graph/LayoutSwitch";
import { Legend } from "./graph/Legend";
import { NavProvider, useNav } from "./nav/NavContext";
import { AddSourcesButton } from "./upload/AddSources";
import type { AddSources } from "./upload/sources";

type WorkspaceChoice = { stores: Workspace[]; selected: string; select(name: string): void };
const WorkspaceContext = createContext<WorkspaceChoice | null>(null);
const catalogBackend = httpBackend();

/** A fresh client and component tree per workspace keeps caches and navigation isolated. */
export function WorkspaceViewer({ browse }: { browse: boolean }) {
  const stores = useAsync(() => catalogBackend.getJson("/api/workspaces") as Promise<Workspace[]>, []);
  const [requested, setRequested] = useState(() => new URLSearchParams(location.search).get("workspace"));
  useEffect(() => {
    const back = () => setRequested(new URLSearchParams(location.search).get("workspace"));
    window.addEventListener("popstate", back);
    return () => window.removeEventListener("popstate", back);
  }, []);
  const selected = stores.data?.find((s) => s.name === requested)?.name ?? stores.data?.[0]?.name;
  const api = useMemo(() => createApi(httpBackend(fetch, selected)), [selected]);
  const select = (name: string) => {
    const url = new URL(location.href);
    url.searchParams.set("workspace", name);
    history.pushState(null, "", url);
    setRequested(name);
  };
  if (stores.error) return <ServerUnreachable message={stores.error.message} />;
  if (!stores.data) return <div className="fatal muted">Loading workspaces…</div>;
  if (!selected) return <div className="fatal">No workspaces available.</div>;
  return (
    <WorkspaceContext.Provider value={{ stores: stores.data, selected, select }}>
      <App key={selected} api={api} mock={false} browse={browse} />
    </WorkspaceContext.Provider>
  );
}

/**
 * The viewer over `api`. A store small enough is drawn whole; a bigger one (or any, with `browse`) is
 * browsed: pick a document from the list, and the graph shows the versions around it. `mock` only labels
 * the page as showing demo data. `open` is a version to open in the drawer at once; `addSources`, when
 * given, puts an "Add sources" button in the top bar (and the viewer reloads what it shows after an add);
 * `actions` are more controls for the top bar.
 */
export function App({
  api,
  mock,
  browse,
  open,
  addSources,
  actions,
}: {
  api: Api;
  mock: boolean;
  browse: boolean;
  open?: string;
  addSources?: AddSources;
  actions?: ReactNode;
}) {
  // Bumped after sources are added: the overview, graph and document list are fetched again.
  const [epoch, refresh] = useReducer((n: number) => n + 1, 0);
  const overview = useAsync(() => api.overview(), [api, epoch]);
  const o = useLatest(overview.data);

  if (overview.error) return <ServerUnreachable message={overview.error.message} />;
  if (!o) return <div className="fatal muted">Loading…</div>;
  const top: Top = {
    epoch,
    open,
    actions: (
      <>
        {addSources && <AddSourcesButton add={addSources} onAdded={refresh} />}
        {actions}
      </>
    ),
  };
  return (
    <ApiProvider api={api}>
      {browse || o.version_count > o.graph_limit ? (
        <Browse overview={o} mock={mock} top={top} />
      ) : (
        <Whole workspace={o.workspace.name} mock={mock} top={top} />
      )}
    </ApiProvider>
  );
}

/** What App hands both layouts: the refresh epoch, a version to open at once, and top bar controls. */
type Top = { epoch: number; open: string | undefined; actions: ReactNode };

/** Opens `version` in the drawer once, when given (inside a NavProvider). */
function OpenAt({ version }: { version: string | undefined }) {
  const nav = useNav();
  const navigate = useRef(nav.navigate);
  navigate.current = nav.navigate;
  const opened = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!version || opened.current === version) return;
    opened.current = version;
    navigate.current(version);
  }, [version]);
  return null;
}

function ServerUnreachable({ message }: { message: string }) {
  return (
    <div className="fatal">
      <h2>Could not reach the Bond viewer</h2>
      <p>{message}</p>
      <p className="muted">
        Start it with <code>bond view --workspace &lt;dir&gt;</code>, or open <a href="?mock">?mock</a> for the built-in
        demo data.
      </p>
    </div>
  );
}

function Brand({
  workspace,
  overview,
  mock,
  actions,
  children,
}: {
  workspace: string;
  overview?: Overview;
  mock: boolean;
  actions: ReactNode;
  children?: string;
}) {
  const choice = useContext(WorkspaceContext);
  return (
    <div className="brand">
      <span className="wordmark">Bond</span>
      {choice && choice.stores.length > 1 ? (
        <select className="workspace workspace-picker" aria-label="Workspace" value={choice.selected}
          onChange={(event) => choice.select(event.target.value)}>
          {choice.stores.map((store) => <option key={store.name} value={store.name}>{store.name}</option>)}
        </select>
      ) : <span className="workspace">{workspace}</span>}
      <span className="muted">{children}</span>
      {overview && <span className="muted">{plural(overview.version_count, "version")}</span>}
      {mock && <span className="badge">mock data</span>}
      <span className="brand-actions">{actions}</span>
    </div>
  );
}

const plural = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;

// --- the whole store, drawn at once ------------------------------------------------------------

function Whole({ workspace, mock, top }: { workspace: string; mock: boolean; top: Top }) {
  const api = useApi();
  const loaded = useAsync(() => api.graph(), [api, top.epoch]);
  const graph = useLatest(loaded.data);
  const nodes = useMemo(() => new Map((graph?.nodes ?? []).map((n) => [n.version, n])), [graph]);

  if (loaded.error) return <ServerUnreachable message={loaded.error.message} />;
  if (!graph) return <div className="fatal muted">Loading…</div>;
  return (
    <NavProvider nodes={nodes}>
      <OpenAt version={top.open} />
      <WholeLayout workspace={workspace} graph={graph} mock={mock} actions={top.actions} />
    </NavProvider>
  );
}

function WholeLayout({
  workspace,
  graph,
  mock,
  actions,
}: {
  workspace: string;
  graph: Graph;
  mock: boolean;
  actions: ReactNode;
}) {
  const [drawerWidth, setDrawerWidth] = useState(defaultDrawerWidth);
  const [layout, setLayout] = useState<Layout>("rings");
  return (
    <div className="app">
      <GraphView graph={graph} drawerWidth={drawerWidth} layout={layout} />
      <LayoutSwitch layout={layout} onChange={setLayout} />
      <Brand workspace={workspace} mock={mock} actions={actions}>
        {plural(graph.nodes.filter((n) => n.workspace === workspace).length, "document")}
      </Brand>
      <Legend />
      <Drawer width={drawerWidth} onWidthChange={setDrawerWidth} />
    </div>
  );
}

// --- a big store, browsed document by document ----------------------------------------------

/** The width of the window's left edge the document list covers (.doclist's left plus width, and a gap). */
const DOCLIST_INSET = 310;

/** Holds every version seen so far, from the neighborhoods loaded, for names and formats. */
function Browse({ overview, mock, top }: { overview: Overview; mock: boolean; top: Top }) {
  const [nodes, setNodes] = useState<ReadonlyMap<string, GraphNode>>(new Map());
  const remember = useCallback(
    (g: Graph) =>
      setNodes((seen) => {
        const next = new Map(seen);
        for (const n of g.nodes) next.set(n.version, n);
        return next;
      }),
    [],
  );
  return (
    <NavProvider nodes={nodes}>
      <OpenAt version={top.open} />
      <BrowseLayout overview={overview} mock={mock} remember={remember} top={top} />
    </NavProvider>
  );
}

function BrowseLayout({
  overview,
  mock,
  remember,
  top,
}: {
  overview: Overview;
  mock: boolean;
  remember(g: Graph): void;
  top: Top;
}) {
  const api = useApi();
  const nav = useNav();
  const [drawerWidth, setDrawerWidth] = useState(defaultDrawerWidth);
  const [layout, setLayout] = useState<Layout>("rings");
  const [center, setCenter] = useState<string | null>(null);
  const [shown, setShown] = useState<Neighborhood | null>(null);
  const hood = useAsync(() => (center ? api.neighborhood(center) : Promise.resolve(null)), [api, center]);
  useEffect(() => {
    if (!hood.data) return;
    remember(hood.data);
    setShown(hood.data);
  }, [hood.data, remember]);

  // The version open in the drawer. Navigating to one outside the graph shown re-centres the graph on it.
  const open = nav.open ? (nav.current?.ref.version ?? null) : null;
  useEffect(() => {
    if (open && !shown?.nodes.some((n) => n.version === open)) setCenter(open);
  }, [open, shown]);

  return (
    <div className="app browse">
      {shown ? (
        // Keyed by centre: a new neighborhood is laid out afresh.
        <GraphView
          key={shown.center}
          graph={shown}
          drawerWidth={drawerWidth}
          layout={layout}
          leftInset={DOCLIST_INSET}
        />
      ) : (
        <div className="graph graph-empty">
          <p className="muted">Open a document to see what it rests on, and what rests on it.</p>
        </div>
      )}
      <LayoutSwitch layout={layout} onChange={setLayout} />
      <Brand workspace={overview.workspace.name} overview={overview} mock={mock} actions={top.actions}>
        {plural(overview.document_count, "document")}
      </Brand>
      <DocumentList selected={open} onOpen={(d) => nav.navigate(d.head)} refresh={top.epoch} />
      {(shown || hood.error) && (
        // Kept to the space between the document list and the drawer.
        <div
          className="browse-status"
          style={{ maxWidth: `calc(100vw - ${DOCLIST_INSET + 16 + (nav.open ? drawerWidth : 0)}px)` }}
        >
          {hood.error ? (
            <span className="error">{hood.error.message}</span>
          ) : (
            shown && (
              <span>
                Within {shown.depth} citation hops of <b>{nav.name(shown.center)}</b>
                {shown.truncated && ` · ${shown.nodes.length} shown, some left out`}
                {hood.loading && " · loading…"}
              </span>
            )
          )}
          {open && shown && open !== shown.center && (
            <button type="button" onClick={() => setCenter(open)}>
              Centre on {nav.name(open)}
            </button>
          )}
        </div>
      )}
      <Legend />
      <Drawer width={drawerWidth} onWidthChange={setDrawerWidth} />
    </div>
  );
}
