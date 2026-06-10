import { ObjectId, type Db } from 'mongodb';
import type { InnerAuthDoc, WidgetUpstreamValue } from './widget-upstream-registry.js';
import { applyInnerAuthHeaders } from './widget-proxy-core.js';

export interface AgentInputResult {
  status: number;
  body: unknown;
}

/** A typed message (`{text}`) or a single navigation keystroke (`{key}`, for
 *  driving the TUI from an empty input box). The host's /input route validates
 *  the key against its allowlist; the API forwards it verbatim. */
export type AgentInputPayload = { text: string } | { key: string };

/**
 * Resolve the process's controlUpstream and POST the payload to its /input
 * route. One-shot, low-frequency — no caching. fetchImpl is injectable for tests.
 */
export async function forwardAgentInput(
  db: Db,
  prefix: string,
  processId: string,
  payload: AgentInputPayload,
  fetchImpl: typeof fetch = fetch,
): Promise<AgentInputResult> {
  let oid: ObjectId;
  try {
    oid = new ObjectId(processId);
  } catch {
    return { status: 400, body: { message: 'Invalid processId' } };
  }

  const doc = await db.collection(`${prefix}_processes`).findOne(
    { _id: oid },
    { projection: { controlUpstream: 1 } },
  );
  const upstream = (doc?.controlUpstream ?? null) as WidgetUpstreamValue | null;
  if (!upstream) {
    return { status: 404, body: { message: 'session not running' } };
  }

  const url = `${upstream.url.replace(/\/$/, '')}/input`;
  const headers = applyInnerAuthHeaders(
    (upstream.innerAuth ?? null) as InnerAuthDoc | null,
    { 'content-type': 'application/json' },
  ) as Record<string, string>;

  try {
    const resp = await fetchImpl(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
    });
    let body: unknown = null;
    try { body = await resp.json(); } catch { body = null; }
    return { status: resp.status, body };
  } catch {
    return { status: 502, body: { message: 'session not reachable' } };
  }
}
