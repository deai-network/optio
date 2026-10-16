import { useCallback, useLayoutEffect, useRef } from 'react';

/** A function whose identity never changes and that calls the `fn` of the
 *  latest render. An engine view passes new callbacks on every wire event; the
 *  view hands its memoized rows these instead, so a new callback re-renders
 *  none of them. Call it from event handlers and effects, not while rendering. */
export function useStableCallback<A extends unknown[], R>(fn: (...args: A) => R): (...args: A) => R {
  const latest = useRef(fn);
  useLayoutEffect(() => {
    latest.current = fn;
  });
  return useCallback((...args: A) => latest.current(...args), []);
}
