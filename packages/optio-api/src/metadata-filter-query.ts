import { createMongoFilterTranslator } from 'filtrum-mongo';
import {
  MetadataFilterQueryParamSchema,
  type ProcessMetadataFilter,
} from 'optio-contracts';

export type ParseResult =
  | { ok: true; value: ProcessMetadataFilter | undefined }
  | { ok: false; error: string };

export function parseMetadataFilterQuery(raw: unknown): ParseResult {
  if (raw === undefined || raw === null || raw === '') return { ok: true, value: undefined };
  if (typeof raw !== 'string') return { ok: false, error: 'metadataFilter must be a string' };
  const result = MetadataFilterQueryParamSchema.safeParse(raw);
  if (!result.success) {
    return { ok: false, error: result.error.issues[0]?.message ?? 'Invalid metadataFilter' };
  }
  return { ok: true, value: result.data as ProcessMetadataFilter };
}

// Kept for back-compat (was exported before the filtrum migration).
export function isLegacyFlatFilter(filter: ProcessMetadataFilter): boolean {
  for (const [k, v] of Object.entries(filter as Record<string, unknown>)) {
    if (k === 'AND' || k === 'OR' || k === 'NOT') return false;
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) return false;
  }
  return true;
}

const translate = createMongoFilterTranslator({ fieldPrefix: 'metadata.' });

// MongoDB query fragment ready to merge into a `find` filter or an aggregation `$match`.
export function metadataFilterToMongo(
  filter: ProcessMetadataFilter | undefined,
): Record<string, unknown> {
  if (!filter) return {};
  return translate(filter) as Record<string, unknown>;
}

export function detectLegacyMetadataParams(rawQuery: Record<string, unknown>): string[] {
  return Object.keys(rawQuery).filter((k) => k.startsWith('metadata.')).sort();
}

export function formatLegacyMetadataMessage(legacyKeys: string[]): string {
  return `Legacy 'metadata.*' query params are no longer supported. ` +
    `Use ?metadataFilter=<URL-encoded JSON>. Offending keys: ${legacyKeys.join(', ')}`;
}
