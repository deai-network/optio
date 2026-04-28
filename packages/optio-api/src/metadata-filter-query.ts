import { MetadataFilterQueryParamSchema, type ProcessMetadataFilter } from 'optio-contracts';

export type ParseResult =
  | { ok: true; value: ProcessMetadataFilter | undefined }
  | { ok: false; error: string };

export function parseMetadataFilterQuery(raw: unknown): ParseResult {
  if (raw === undefined || raw === null || raw === '') {
    return { ok: true, value: undefined };
  }
  if (typeof raw !== 'string') {
    return { ok: false, error: 'metadataFilter must be a string' };
  }
  const result = MetadataFilterQueryParamSchema.safeParse(raw);
  if (!result.success) {
    return {
      ok: false,
      error: result.error.issues[0]?.message ?? 'Invalid metadataFilter',
    };
  }
  return { ok: true, value: result.data };
}

export function metadataFilterToMongo(
  filter: ProcessMetadataFilter | undefined,
): Record<string, unknown> {
  if (!filter) return {};
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(filter)) {
    out[`metadata.${k}`] = v;
  }
  return out;
}

export function detectLegacyMetadataParams(rawQuery: Record<string, unknown>): string[] {
  return Object.keys(rawQuery)
    .filter(k => k.startsWith('metadata.'))
    .sort();
}
