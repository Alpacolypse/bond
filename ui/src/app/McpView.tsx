// The MCP app view: what a Bond tool call shows in the conversation. A document (after show, or a
// write or edit that went through) is a compact card that opens the full viewer at its current
// version; a refused write lists its issues; the workspaces tool shows a picker that grants or
// revokes the agent's access to workspaces.

import { useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from "react";
import { App as Viewer } from "../App";
import { createApi } from "../api/client";
import { useAsync, useLatest } from "../api/useAsync";
import { AddSourcesButton } from "../upload/AddSources";
import { type Host, useHost } from "./host";
import { callTool, mcpAddSources, mcpBackend } from "./mcp";
import { grantsContext, SUPPORT_LABEL, viewFor, type Change, type Support, type View } from "./mode";

export function McpView({ host }: { host: Host }) {
  const state = useHost(host);
  const view = viewFor(state.input, state.result, state.context.toolInfo?.tool.name);

  if (state.error) return <Card title="Bond"><p className="error">Could not connect to the host: {state.error}</p></Card>;
  switch (view.kind) {
    case "waiting":
      return (
        <Card title={typeof state.input?.document === "string" ? state.input.document : "Bond"}>
          <p className="muted">Working…</p>
        </Card>
      );
    case "error":
      return (
        <Card title="Bond">
          <p className="error">{view.message}</p>
        </Card>
      );
    case "document":
      return <DocumentCard host={host} view={view} />;
    case "refused":
    case "checked":
      return <IssuesCard view={view} />;
    case "picker":
      return <Picker host={host} />;
  }
}

function Card({ title, badge, children }: { title: string; badge?: ReactNode; children?: ReactNode }) {
  useCompact(true);
  return (
    <section className="card">
      <header className="card-head">
        <span className="card-brand">Bond</span>
        <h1 title={title}>{title}</h1>
        {badge}
      </header>
      {children}
    </section>
  );
}

/** The compact (card) styles apply to the whole page except while the full viewer is shown. */
function useCompact(compact: boolean) {
  useEffect(() => {
    document.documentElement.classList.toggle("compact", compact);
  }, [compact]);
}

function SupportBadge({ support }: { support: Support | null }) {
  if (!support) return null;
  return <span className={`support support-${support}`}>{SUPPORT_LABEL[support]}</span>;
}

// --- a document --------------------------------------------------------------------------------

function DocumentCard({ host, view }: { host: Host; view: Extract<View, { kind: "document" }> }) {
  const { workspace, document } = view;
  const api = useMemo(() => (workspace ? createApi(mcpBackend(host.app, workspace)) : null), [host, workspace]);
  const addSources = useMemo(() => (workspace ? mcpAddSources(host.app, workspace) : null), [host, workspace]);
  const displayMode = useHost(host).context.displayMode;
  const [viewing, setViewing] = useState<string | null>(null);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The host left full screen on its own (its close button, Esc): back to the card.
  const lastMode = useRef(displayMode);
  useEffect(() => {
    if (lastMode.current === "fullscreen" && displayMode !== "fullscreen") setViewing(null);
    lastMode.current = displayMode;
  }, [displayMode]);

  const open = async () => {
    if (!api) return;
    setOpening(true);
    setError(null);
    try {
      // The document's current version, by its exact name.
      const { version } = await callTool<{ version: string }>(host.app, "view_head", { workspace, document });
      await host.requestDisplayMode("fullscreen");
      setViewing(version);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setOpening(false);
    }
  };

  const close = () => {
    setViewing(null);
    if (displayMode === "fullscreen") void host.requestDisplayMode("inline");
  };

  if (viewing && api) {
    return (
      <FullViewer>
        <Viewer
          api={api}
          mock={false}
          browse={false}
          open={viewing}
          addSources={addSources ?? undefined}
          actions={
            <button type="button" className="viewer-close" onClick={close} title="Back to the card in the conversation">
              Done
            </button>
          }
        />
      </FullViewer>
    );
  }

  return (
    <Card title={document} badge={<SupportBadge support={view.support} />}>
      <p className="card-meta muted">
        {view.written ? "Written" : "Shown"}
        {workspace && (
          <>
            {" in "}
            <b>{workspace}</b>
          </>
        )}
      </p>
      <div className="card-actions">
        <button type="button" className="primary" disabled={!api || opening} onClick={() => void open()}>
          {opening ? "Opening…" : "Open"}
        </button>
        {addSources && <AddSourcesButton add={addSources} />}
      </div>
      {!workspace && <p className="muted">The tool call named no workspace, so there is nothing to open.</p>}
      {error && <p className="error">{error}</p>}
    </Card>
  );
}

function FullViewer({ children }: { children: ReactNode }) {
  useCompact(false);
  return <div className="full-viewer">{children}</div>;
}

// --- a refused (or only checked) write ---------------------------------------------------------

function IssuesCard({ view }: { view: Extract<View, { kind: "refused" | "checked" }> }) {
  const refused = view.kind === "refused";
  return (
    <Card title={view.document ?? "Write"} badge={<SupportBadge support={view.support} />}>
      <p className="card-meta muted">
        {refused ? "Refused: nothing was written." : "Checked only (dry run): nothing was written."}
      </p>
      {view.issues.length > 0 && (
        <ul className="issues">
          {view.issues.map((issue, i) => (
            <li key={i}>{issue}</li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// --- the workspace picker ----------------------------------------------------------------------

type WorkspaceRow = { name: string; granted: boolean; links: string[] };
type WorkspaceList = { workspaces: WorkspaceRow[] };

function Picker({ host }: { host: Host }) {
  const app = host.app;
  const [epoch, reload] = useReducer((n: number) => n + 1, 0);
  const loaded = useAsync(() => callTool<WorkspaceList>(app, "view_workspaces"), [app, epoch]);
  const list = useLatest(loaded.data);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  // What the person did here, told to the model (each update replaces the last, so it says it all).
  const changes = useRef(new Map<string, Change>());

  // Grant a workspace, revoke it (granted: false), or create one, which is granted too.
  const act = async (target: string, granted: boolean | "new") => {
    setBusy(target);
    setError(null);
    try {
      const { workspace } = await callTool<{ workspace: string }>(
        app,
        granted === "new" ? "new_workspace" : "grant_workspace",
        granted === "new" ? { name: target } : { name: target, granted },
      );
      changes.current.set(workspace ?? target, granted === false ? "revoked" : "granted");
      if (granted === "new") setName("");
      reload();
      await app
        .updateModelContext({ content: [{ type: "text", text: grantsContext(changes.current) }] })
        .catch(() => undefined); // a host without model context still has the grant
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card title="Workspaces">
      {loaded.error && <p className="error">{loaded.error.message}</p>}
      {!list && !loaded.error && <p className="muted">Loading…</p>}
      {list && (
        <>
          <p className="card-meta muted">Grant the agent a workspace to let it read and write there.</p>
          <ul className="workspaces">
            {list.workspaces.map((w) => (
              <li key={w.name}>
                <div className="ws-row">
                  <span className="ws-name">{w.name}</span>
                  {w.granted && <span className="badge">granted</span>}
                  {w.links.length > 0 && <span className="muted ws-links">links {w.links.join(", ")}</span>}
                  <span className="ws-actions">
                    <button
                      type="button"
                      className={w.granted ? undefined : "primary"}
                      disabled={busy !== null}
                      onClick={() => void act(w.name, !w.granted)}
                    >
                      {busy === w.name ? "…" : w.granted ? "Revoke" : "Grant"}
                    </button>
                  </span>
                </div>
                {w.granted && <AddSourcesButton add={mcpAddSources(app, w.name)} />}
              </li>
            ))}
            {list.workspaces.length === 0 && <li className="muted">No workspaces yet.</li>}
          </ul>
          {/* Not a <form>: a sandbox without allow-forms blocks submitting one, even when handled. */}
          <div className="ws-new">
              <input
                type="text"
                placeholder="New workspace name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && name.trim() && busy === null) void act(name.trim(), "new");
                }}
                disabled={busy !== null}
              />
              <button
                type="button"
                disabled={busy !== null || !name.trim()}
                onClick={() => void act(name.trim(), "new")}
              >
                New workspace
              </button>
            </div>
        </>
      )}
      {error && <p className="error">{error}</p>}
    </Card>
  );
}
