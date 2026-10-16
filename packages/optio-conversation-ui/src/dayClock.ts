import { useSyncExternalStore } from 'react';

// A coarse clock for message times. formatMessageTime only needs the day of
// "now" (it shows the time alone for today, and the date for any other day),
// so a time label reads local midnight of today, which changes once a day,
// rather than the current time, which would re-render it on every render.
// One timer, set for just after the next midnight, serves every label while
// any is mounted; none runs otherwise.

/** Local midnight of the day `t` falls on. */
export function startOfDay(t: number): number {
  const d = new Date(t);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

const listeners = new Set<() => void>();
let timer: ReturnType<typeof setTimeout> | null = null;

function schedule(): void {
  const now = Date.now();
  const d = new Date(now);
  const nextMidnight = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1).getTime();
  // A second past midnight, so the new day has begun when it fires.
  timer = setTimeout(() => {
    timer = null;
    for (const listener of [...listeners]) listener();
    if (listeners.size > 0 && timer === null) schedule();
  }, nextMidnight - now + 1000);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (timer === null) schedule();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };
}

const today = (): number => startOfDay(Date.now());

/** Local midnight of today; re-renders the caller when the day changes. */
export function useToday(): number {
  return useSyncExternalStore(subscribe, today, today);
}
