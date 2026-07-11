import type { ValueOrFn } from './types.js';

export function resolve<T>(v: ValueOrFn<T> | undefined, fallback: T): T {
  if (v === undefined) return fallback;
  return typeof v === 'function' ? (v as () => T)() : v;
}
