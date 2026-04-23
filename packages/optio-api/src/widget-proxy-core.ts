import { ObjectId, type Db } from 'mongodb';
import type { InnerAuthDoc, WidgetUpstreamRegistry, WidgetUpstreamValue } from './widget-upstream-registry.js';

export async function resolveWidgetUpstream(
  db: Db,
  prefix: string,
  registry: WidgetUpstreamRegistry,
  processId: string,
): Promise<WidgetUpstreamValue | null> {
  // Scope the cache by (database, prefix, processId) so the same ObjectId
  // used in two different databases does not collide.
  const cacheKey = `${db.databaseName}/${prefix}/${processId}`;

  if (registry.has(cacheKey)) {
    return registry.get(cacheKey) ?? null;
  }

  let oid: ObjectId;
  try {
    oid = new ObjectId(processId);
  } catch {
    // Don't cache: a malformed processId is a one-off URL and caching
    // the negative would waste an entry; the 404 is cheap to re-derive.
    return null;
  }

  const doc = await db.collection(`${prefix}_processes`).findOne(
    { _id: oid },
    { projection: { widgetUpstream: 1 } },
  );
  const upstream = (doc?.widgetUpstream ?? null) as WidgetUpstreamValue | null;
  // Only cache positive lookups.  A null (no widgetUpstream registered yet
  // or just cleared at teardown) must NOT stick in the cache: when the
  // worker subsequently calls set_widget_upstream, the dashboard's first
  // iframe load would otherwise see a stale negative entry and 404 until
  // the TTL expired.  The design spec calls for tree-poller-based
  // invalidation to handle this; until that's wired, skipping the negative
  // cache is the minimal correct behavior.
  if (upstream !== null) {
    registry.set(cacheKey, upstream);
  }
  return upstream;
}

export function applyInnerAuthHeaders(
  innerAuth: InnerAuthDoc | null,
  headers: Record<string, string | string[] | undefined>,
): Record<string, string | string[] | undefined> {
  if (!innerAuth) return headers;

  if (innerAuth.kind === 'basic') {
    const encoded = Buffer.from(`${innerAuth.username}:${innerAuth.password}`).toString('base64');
    return { ...headers, authorization: `Basic ${encoded}` };
  }

  if (innerAuth.kind === 'header') {
    return { ...headers, [innerAuth.name.toLowerCase()]: innerAuth.value };
  }

  // query auth does not touch headers
  return headers;
}

export function applyInnerAuthQuery(
  innerAuth: InnerAuthDoc | null,
  url: string,
): string {
  if (!innerAuth || innerAuth.kind !== 'query') return url;
  const separator = url.includes('?') ? '&' : '?';
  const encodedValue = encodeURIComponent(innerAuth.value);
  return `${url}${separator}${innerAuth.name}=${encodedValue}`;
}

export function isWriteMethod(method: string): boolean {
  const m = method.toUpperCase();
  return m !== 'GET' && m !== 'HEAD' && m !== 'OPTIONS';
}
