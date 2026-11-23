import { useEffect, useRef, useState } from "react";

export type AsyncState<T> = { data: T | undefined; error: Error | undefined; loading: boolean };

const LOADING = { data: undefined, error: undefined, loading: true } as const;

/**
 * Run `load` whenever `deps` change. A superseded or unmounted load has its result dropped and its
 * `signal` aborted, so it can release what it holds.
 */
export function useAsync<T>(load: (signal: AbortSignal) => Promise<T>, deps: readonly unknown[]): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>(LOADING);
  useEffect(() => {
    const controller = new AbortController();
    const live = () => !controller.signal.aborted;
    setState(LOADING);
    load(controller.signal).then(
      (data) => live() && setState({ data, error: undefined, loading: false }),
      (error: unknown) => live() && setState({ data: undefined, error: asError(error), loading: false }),
    );
    return () => controller.abort();
    // `load` is a fresh closure every render; the caller names what it depends on in `deps`.
  }, deps);
  return state;
}

function asError(error: unknown): Error {
  return error instanceof Error ? error : new Error(String(error));
}

/** The last defined `value`: what was loaded stays shown while it is loaded again. */
export function useLatest<T>(value: T | undefined): T | undefined {
  const last = useRef(value);
  if (value !== undefined) last.current = value;
  return last.current;
}
