// The connection to the host (Claude, ChatGPT, …) through the ext-apps App: the tool call that opened
// the view (its input and result) and the host context (theme, display mode), as a store React reads.

import {
  App,
  applyDocumentTheme,
  applyHostFonts,
  applyHostStyleVariables,
  type McpUiDisplayMode,
  type McpUiHostContext,
} from "@modelcontextprotocol/ext-apps";
import type { CallToolResult } from "@modelcontextprotocol/client";
import { useSyncExternalStore } from "react";

export type HostState = {
  connected: boolean;
  /** Set if the handshake with the host failed. */
  error: string | null;
  input: Record<string, unknown> | undefined;
  result: CallToolResult | undefined;
  context: McpUiHostContext;
};

export class Host {
  readonly app = new App({ name: "Bond", version: "0.1.0" }, {}, { autoResize: true });
  private state: HostState = { connected: false, error: null, input: undefined, result: undefined, context: {} };
  private readonly listeners = new Set<() => void>();

  constructor() {
    // Handlers go on before connecting, so no notification sent during the handshake is missed.
    this.app.ontoolinput = ({ arguments: input }) => this.set({ input });
    this.app.ontoolresult = (result) => this.set({ result });
    this.app.onhostcontextchanged = (context) => this.setContext(context);
  }

  async connect(): Promise<void> {
    try {
      await this.app.connect();
      this.set({ connected: true });
      this.setContext(this.app.getHostContext() ?? {});
    } catch (e) {
      this.set({ error: e instanceof Error ? e.message : String(e) });
    }
  }

  /** Ask the host for a display mode; the mode it actually chose is recorded and returned. */
  async requestDisplayMode(mode: McpUiDisplayMode): Promise<McpUiDisplayMode> {
    const available = this.state.context.availableDisplayModes;
    if (available && !available.includes(mode)) return this.state.context.displayMode ?? "inline";
    try {
      const result = await this.app.requestDisplayMode({ mode });
      this.setContext({ displayMode: result.mode });
      return result.mode;
    } catch {
      return this.state.context.displayMode ?? "inline";
    }
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  snapshot = () => this.state;

  private set(patch: Partial<HostState>) {
    this.state = { ...this.state, ...patch };
    for (const l of this.listeners) l();
  }

  /** Context notifications carry only what changed: merge, and follow the host's theme and styles. */
  private setContext(patch: McpUiHostContext) {
    if (patch.theme) applyDocumentTheme(patch.theme);
    if (patch.styles?.variables) applyHostStyleVariables(patch.styles.variables);
    if (patch.styles?.css?.fonts) applyHostFonts(patch.styles.css.fonts);
    this.set({ context: { ...this.state.context, ...patch } });
  }
}

export function useHost(host: Host): HostState {
  return useSyncExternalStore(host.subscribe, host.snapshot);
}
