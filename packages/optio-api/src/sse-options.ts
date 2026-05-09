/**
 * Shared query-parsing helper for SSE / poller routes.
 *
 * Each adapter (fastify, express, nextjs-app, nextjs-pages) historically
 * duplicated the same SSE/poller query-parsing block:
 *   - reject legacy `metadata.*` keys with a 400
 *   - parse the `metadataFilter` JSON via parseMetadataFilterQuery
 *   - coerce `maxDepth` from string to number
 *   - pass `database` / `prefix` through to resolveDb
 *
 * `parseSseOptions(rawQuery)` returns the parsed shape; it throws on bad
 * `metadataFilter` JSON or bad `maxDepth` so adapters can map to a 400.
 *
 * `checkLegacyMetadataParams(rawQuery)` throws `LegacyMetadataParamError`
 * when legacy `metadata.*` keys are present; adapters catch and emit 400.
 *
 * Database resolution stays in the adapter (a single
 * `resolveDb(dbOpts, sseOpts)` line), per Task 5 / commit A4 of
 * docs/2026-05-08-engine-rpc-migration-phase-3-design.md §3.
 */
import {
  parseMetadataFilterQuery,
  detectLegacyMetadataParams,
  formatLegacyMetadataMessage,
} from './metadata-filter-query.js';
import type { ProcessMetadataFilter } from './types.js';

export class LegacyMetadataParamError extends Error {
  public readonly keys: string[];
  constructor(keys: string[]) {
    super(formatLegacyMetadataMessage(keys));
    this.name = 'LegacyMetadataParamError';
    this.keys = keys;
  }
}

export interface ParsedSseOptions {
  database?: string;
  prefix?: string;
  metadataFilter?: ProcessMetadataFilter;
  maxDepth?: number;
}

/**
 * Parse SSE/poller query parameters into a typed shape. Throws Error on
 * invalid `metadataFilter` JSON or on a non-finite/negative `maxDepth`;
 * adapters should map a thrown Error to a 400 response.
 *
 * Does NOT inspect legacy `metadata.*` keys — call
 * `checkLegacyMetadataParams` separately for that, since adapters want to
 * distinguish the two error cases.
 */
export function parseSseOptions(
  rawQuery: Record<string, unknown>,
): ParsedSseOptions {
  const out: ParsedSseOptions = {};

  if (typeof rawQuery.database === 'string') out.database = rawQuery.database;
  if (typeof rawQuery.prefix === 'string') out.prefix = rawQuery.prefix;

  // metadataFilter — parseMetadataFilterQuery returns a discriminated union
  // { ok: true, value } | { ok: false, error }. We only forward the parsed
  // object on success; on failure we throw so the adapter maps to a 400.
  const mfRaw = rawQuery.metadataFilter;
  if (mfRaw !== undefined && mfRaw !== null && mfRaw !== '') {
    const parsed = parseMetadataFilterQuery(
      typeof mfRaw === 'string' ? mfRaw : undefined,
    );
    if (!parsed.ok) {
      throw new Error(parsed.error);
    }
    out.metadataFilter = parsed.value;
  }

  // maxDepth — coerce string to int. parseInt returns NaN on garbage, which
  // !Number.isFinite catches. Negative depths are also invalid.
  const maxDepthRaw = rawQuery.maxDepth;
  if (typeof maxDepthRaw === 'string' && maxDepthRaw.length > 0) {
    const n = parseInt(maxDepthRaw, 10);
    if (!Number.isFinite(n) || n < 0) {
      throw new Error(`Invalid maxDepth: ${maxDepthRaw}`);
    }
    out.maxDepth = n;
  } else if (typeof maxDepthRaw === 'number' && Number.isFinite(maxDepthRaw) && maxDepthRaw >= 0) {
    out.maxDepth = maxDepthRaw;
  }

  return out;
}

/**
 * Throws `LegacyMetadataParamError` if the raw query contains any legacy
 * `metadata.*` keys (a removed pre-`metadataFilter` API). Adapters catch
 * this and emit a 400 with the formatted migration message.
 */
export function checkLegacyMetadataParams(
  rawQuery: Record<string, unknown>,
): void {
  const legacyKeys = detectLegacyMetadataParams(rawQuery);
  if (legacyKeys.length > 0) {
    throw new LegacyMetadataParamError(legacyKeys);
  }
}
