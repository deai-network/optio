export interface WidgetUpstreamValue {
  url: string;
  innerAuth: InnerAuthDoc | null;
  // When true, the proxy injects the location.pathname prefix-strip shim (for
  // client-routed SPAs: opencode/kimicode). Off/absent for ttyd widgets, whose
  // client derives backend URLs from location.pathname and must keep the prefix.
  stripProxyPrefix?: boolean;
}

export type InnerAuthDoc =
  | { kind: 'basic'; username: string; password: string }
  | { kind: 'query'; name: string; value: string }
  | { kind: 'header'; name: string; value: string };

export interface WidgetUpstreamRegistry {
  get(processId: string): WidgetUpstreamValue | null | undefined;
  has(processId: string): boolean;
  set(processId: string, value: WidgetUpstreamValue | null): void;
  invalidate(processId: string): void;
}

interface CachedEntry {
  value: WidgetUpstreamValue | null;
  expiresAt: number;
}

export function createWidgetUpstreamRegistry(opts: { ttlMs: number }): WidgetUpstreamRegistry {
  const cache = new Map<string, CachedEntry>();

  function getEntry(processId: string): CachedEntry | undefined {
    const entry = cache.get(processId);
    if (!entry) return undefined;
    if (Date.now() > entry.expiresAt) {
      cache.delete(processId);
      return undefined;
    }
    return entry;
  }

  return {
    get(processId) {
      return getEntry(processId)?.value;
    },
    has(processId) {
      return getEntry(processId) !== undefined;
    },
    set(processId, value) {
      cache.set(processId, { value, expiresAt: Date.now() + opts.ttlMs });
    },
    invalidate(processId) {
      cache.delete(processId);
    },
  };
}
