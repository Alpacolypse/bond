// "Add sources": pick files, add them to a workspace, say what happened. When a file's name is taken,
// offer to add the files again as new versions of those documents. Used by the browser viewer's top
// bar and by the MCP app.

import { useRef, useState } from "react";
import { describeAdded, isNameTaken, type AddResult, type AddSources } from "./sources";

type Status =
  | { state: "idle" }
  | { state: "busy" }
  | { state: "done"; result: AddResult }
  | { state: "failed"; message: string; files: File[] };

export function AddSourcesButton({
  add,
  onAdded,
  label = "Add sources",
}: {
  add: AddSources;
  onAdded?(result: AddResult): void;
  label?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<Status>({ state: "idle" });

  const run = async (files: File[], update: boolean) => {
    if (files.length === 0) return;
    setStatus({ state: "busy" });
    try {
      const result = await add(files, update);
      setStatus({ state: "done", result });
      onAdded?.(result);
    } catch (e) {
      setStatus({ state: "failed", message: e instanceof Error ? e.message : String(e), files });
    }
  };

  return (
    <span className="add-sources">
      <button
        type="button"
        disabled={status.state === "busy"}
        onClick={() => input.current?.click()}
        title="Add files to this workspace as sources"
      >
        {status.state === "busy" ? "Adding…" : label}
      </button>
      <input
        ref={input}
        type="file"
        multiple
        hidden
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = ""; // so picking the same files again fires change
          void run(files, false);
        }}
      />
      {status.state === "done" && (
        <span className="add-sources-status">
          {describeAdded(status.result)}
          {status.result.note && <span className="muted"> {status.result.note}</span>}
          <Dismiss onClick={() => setStatus({ state: "idle" })} />
        </span>
      )}
      {status.state === "failed" && (
        <span className="add-sources-status">
          <span className="error">{status.message}</span>
          {isNameTaken(status.message) && (
            <button type="button" onClick={() => void run(status.files, true)}>
              Add as new versions
            </button>
          )}
          <Dismiss onClick={() => setStatus({ state: "idle" })} />
        </span>
      )}
    </span>
  );
}

function Dismiss({ onClick }: { onClick(): void }) {
  return (
    <button type="button" className="link" onClick={onClick} aria-label="Dismiss">
      ×
    </button>
  );
}
