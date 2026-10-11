import type { Db, MongoClient } from 'mongodb';
import type { ConversationFailureReason, ConversationRecord } from 'optio-contracts';

/**
 * The proxy side of the engine's conversation archive (optio-core
 * `conversation_archive.py`): one aiohttp listener per engine that serves a
 * suspended process's decrypted conversation record. The engine publishes
 * `{ _id: 'conversationArchive', url, password, updatedAt }` in
 * `{prefix}_engine_endpoints`; this module finds that address, caches it for
 * 30 s, and asks `GET <url>conversation/<_id>` with basic auth
 * `optio:<password>`.
 *
 * Nothing here logs: the password and the record (plaintext conversation)
 * must never reach a log line, and the 503 messages carry neither the address
 * nor the password.
 *
 * Spec: docs/2026-10-12-suspended-conversation-view-design.md §3.
 */

export const CONVERSATION_ARCHIVE_ENDPOINT_ID = 'conversationArchive';
/** How long a resolved endpoint is reused before MongoDB is read again. */
export const CONVERSATION_ARCHIVE_CACHE_TTL_MS = 30_000;
/** The whole upstream exchange (connect, headers and body) must finish within this. */
export const CONVERSATION_ARCHIVE_TIMEOUT_MS = 15_000;

export interface ConversationArchiveEndpoint {
  url: string;
  password: string;
}

export type ConversationErrorBody = { reason: ConversationFailureReason; message: string };

/** What the archive (or its absence) answered, as optio-api relays it. */
export type ConversationFetchResult =
  | { status: 200; body: ConversationRecord }
  | { status: 404 | 409 | 500 | 503; body: ConversationErrorBody };

interface CacheEntry {
  endpoint: ConversationArchiveEndpoint;
  expiresAt: number;
}

// Per MongoClient (as the process-change hubs are), then per database name and
// prefix: two clusters that both have a database `x` never share an entry.
const cache = new WeakMap<MongoClient, Map<string, CacheEntry>>();

function entries(db: Db): Map<string, CacheEntry> {
  let map = cache.get(db.client);
  if (!map) {
    map = new Map();
    cache.set(db.client, map);
  }
  return map;
}

const keyOf = (db: Db, prefix: string) => `${db.databaseName}\u0000${prefix}`;

/**
 * The published archive address of `(db, prefix)`, or null when no engine
 * publishes one (or the document is malformed). A found address is cached for
 * 30 s; an absence is not, so a freshly started engine is found at once.
 */
export async function resolveConversationArchive(
  db: Db,
  prefix: string,
): Promise<ConversationArchiveEndpoint | null> {
  const map = entries(db);
  const key = keyOf(db, prefix);
  const cached = map.get(key);
  if (cached && Date.now() < cached.expiresAt) return cached.endpoint;
  if (cached) map.delete(key);

  const doc = await db
    .collection(`${prefix}_engine_endpoints`)
    .findOne({ _id: CONVERSATION_ARCHIVE_ENDPOINT_ID as any }, { projection: { url: 1, password: 1 } });
  if (!doc || typeof doc.url !== 'string' || typeof doc.password !== 'string' || !doc.url) return null;
  const endpoint = { url: doc.url, password: doc.password };
  map.set(key, { endpoint, expiresAt: Date.now() + CONVERSATION_ARCHIVE_CACHE_TTL_MS });
  return endpoint;
}

/**
 * Drop the cached address of `(db, prefix)`: the engine is gone, restarted
 * elsewhere, or restarted with a new password. With `endpoint`, only when the
 * cache still holds that very address (a concurrent request may already have
 * cached a newer one).
 */
export function forgetConversationArchive(
  db: Db,
  prefix: string,
  endpoint?: ConversationArchiveEndpoint,
): void {
  const map = entries(db);
  const key = keyOf(db, prefix);
  if (endpoint === undefined || map.get(key)?.endpoint === endpoint) map.delete(key);
}

function unavailable(message: string): ConversationFetchResult {
  return { status: 503, body: { reason: 'engine-unavailable', message } };
}

const NO_ENDPOINT = 'No engine serves conversation records for this instance.';
const UNREACHABLE = 'The engine serving conversation records is not reachable.';
const TOO_SLOW = 'The engine did not send the conversation record in time.';
const NOT_THE_ARCHIVE = 'The engine serving conversation records gave an unusable answer.';
const STALE_CREDENTIALS = 'The engine serving conversation records refused the stored credentials (it may have restarted); try again.';

function isErrorBody(body: unknown): body is ConversationErrorBody {
  return typeof body === 'object' && body !== null
    && typeof (body as any).reason === 'string' && typeof (body as any).message === 'string';
}

function isRecord(body: unknown): body is ConversationRecord {
  return typeof body === 'object' && body !== null && Array.isArray((body as any).events)
    && ((body as any).capturedAt === null || typeof (body as any).capturedAt === 'string');
}

/**
 * Ask the archive for the record of process `processObjectId` (the process
 * document's `_id`, 24 hex). The archive's 200 / 404 / 409 / 500 answers pass
 * through as they are. Everything else is 503 `engine-unavailable` and drops
 * the cached address, so the next request reads MongoDB again: no published
 * address, a connection failure, no complete answer within `timeoutMs`, a 401
 * (the engine restarted on the same port with a new password), or an answer
 * that is not the archive's (another server on a reused port).
 */
export async function fetchConversation(
  db: Db,
  prefix: string,
  processObjectId: string,
  opts: { timeoutMs?: number } = {},
): Promise<ConversationFetchResult> {
  const endpoint = await resolveConversationArchive(db, prefix);
  if (!endpoint) return unavailable(NO_ENDPOINT);

  const base = endpoint.url.endsWith('/') ? endpoint.url : `${endpoint.url}/`;
  const authorization = `Basic ${Buffer.from(`optio:${endpoint.password}`).toString('base64')}`;
  let status: number;
  let body: unknown;
  try {
    const res = await fetch(`${base}conversation/${processObjectId}`, {
      headers: { authorization, accept: 'application/json' },
      redirect: 'manual',
      // Covers reading the body too: undici aborts the body stream.
      signal: AbortSignal.timeout(opts.timeoutMs ?? CONVERSATION_ARCHIVE_TIMEOUT_MS),
    });
    status = res.status;
    const text = await res.text();
    try {
      body = JSON.parse(text);
    } catch {
      body = undefined;
    }
  } catch (err) {
    forgetConversationArchive(db, prefix, endpoint);
    const timedOut = err instanceof Error && (err.name === 'TimeoutError' || err.name === 'AbortError');
    return unavailable(timedOut ? TOO_SLOW : UNREACHABLE);
  }

  if (status === 200 && isRecord(body)) {
    return { status: 200, body: { events: body.events, capturedAt: body.capturedAt } };
  }
  if ((status === 404 || status === 409 || status === 500) && isErrorBody(body)) {
    return { status, body: { reason: body.reason, message: body.message } };
  }
  forgetConversationArchive(db, prefix, endpoint);
  return unavailable(status === 401 ? STALE_CREDENTIALS : NOT_THE_ARCHIVE);
}
