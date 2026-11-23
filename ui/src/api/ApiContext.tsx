// The API client, provided to the component tree so views never reach for a global.

import { createContext, useContext, type ReactNode } from "react";
import type { Api } from "./client";

const ApiCtx = createContext<Api | null>(null);

export function ApiProvider({ api, children }: { api: Api; children: ReactNode }) {
  return <ApiCtx.Provider value={api}>{children}</ApiCtx.Provider>;
}

export function useApi(): Api {
  const api = useContext(ApiCtx);
  if (!api) throw new Error("useApi outside ApiProvider");
  return api;
}
