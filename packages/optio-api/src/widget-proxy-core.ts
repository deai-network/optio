import { ObjectId, type Db } from 'mongodb';
import type { InnerAuthDoc, WidgetUpstreamRegistry, WidgetUpstreamValue } from './widget-upstream-registry.js';

export async function resolveWidgetUpstream(
  db: Db,
  prefix: string,
  registry: WidgetUpstreamRegistry,
  processId: string,
): Promise<WidgetUpstreamValue | null> {
  if (registry.has(processId)) {
    return registry.get(processId) ?? null;
  }

  let oid: ObjectId;
  try {
    oid = new ObjectId(processId);
  } catch {
    registry.set(processId, null);
    return null;
  }

  const doc = await db.collection(`${prefix}_processes`).findOne(
    { _id: oid },
    { projection: { widgetUpstream: 1 } },
  );
  const upstream = (doc?.widgetUpstream ?? null) as WidgetUpstreamValue | null;
  registry.set(processId, upstream);
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
