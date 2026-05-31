import {
  MetadataFilterQueryParamSchema,
  type ProcessMetadataFilter,
  type ProcessMetadataPredicate,
  type FilterLeafOps,
} from 'optio-contracts';

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

// Public entry point: dispatches between the legacy flat shape and the new
// predicate tree by structural inspection. Output is a MongoDB query
// fragment ready to merge into a `find` filter (or pass into an aggregation
// `$match`).
export function metadataFilterToMongo(
  filter: ProcessMetadataFilter | undefined,
): Record<string, unknown> {
  if (!filter) return {};
  if (isLegacyFlatFilter(filter)) return legacyToMongo(filter as Record<string, unknown>);
  return predicateToMongo(filter as ProcessMetadataPredicate);
}

// A filter is legacy iff every top-level key is a field name (not a
// combinator keyword) and every value is a scalar (not an operator object).
export function isLegacyFlatFilter(filter: ProcessMetadataFilter): boolean {
  for (const [k, v] of Object.entries(filter as Record<string, unknown>)) {
    if (k === 'AND' || k === 'OR' || k === 'NOT') return false;
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) return false;
  }
  return true;
}

function legacyToMongo(filter: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(filter)) {
    out[`metadata.${k}`] = v;
  }
  return out;
}

function predicateToMongo(p: ProcessMetadataPredicate): Record<string, unknown> {
  const pp = p as Record<string, unknown>;
  if (Array.isArray(pp.AND)) {
    return { $and: (pp.AND as ProcessMetadataPredicate[]).map(predicateToMongo) };
  }
  if (Array.isArray(pp.OR)) {
    return { $or: (pp.OR as ProcessMetadataPredicate[]).map(predicateToMongo) };
  }
  if (pp.NOT !== undefined) {
    return { $nor: [predicateToMongo(pp.NOT as ProcessMetadataPredicate)] };
  }
  // Record of field→leaf-ops. Single-leaf nodes emit a flat object; multi-leaf
  // nodes split into $and of single-key objects to keep translator output regular.
  const entries = Object.entries(pp) as [string, FilterLeafOps][];
  if (entries.length === 1) {
    const [field, ops] = entries[0]!;
    return { [`metadata.${field}`]: leafOpsToMongo(ops) };
  }
  return {
    $and: entries.map(([field, ops]) => ({
      [`metadata.${field}`]: leafOpsToMongo(ops),
    })),
  };
}

function leafOpsToMongo(ops: FilterLeafOps): Record<string, unknown> {
  const m: Record<string, unknown> = {};
  if (ops.eq !== undefined) m.$eq = ops.eq;
  if (ops.ne !== undefined) m.$ne = ops.ne;
  if (ops.in !== undefined) m.$in = ops.in;
  if (ops.nin !== undefined) m.$nin = ops.nin;
  if (ops.exists !== undefined) m.$exists = ops.exists;
  if (ops.gt !== undefined) m.$gt = ops.gt;
  if (ops.gte !== undefined) m.$gte = ops.gte;
  if (ops.lt !== undefined) m.$lt = ops.lt;
  if (ops.lte !== undefined) m.$lte = ops.lte;
  return m;
}

export function detectLegacyMetadataParams(rawQuery: Record<string, unknown>): string[] {
  return Object.keys(rawQuery)
    .filter(k => k.startsWith('metadata.'))
    .sort();
}

export function formatLegacyMetadataMessage(legacyKeys: string[]): string {
  return `Legacy 'metadata.*' query params are no longer supported. ` +
    `Use ?metadataFilter=<URL-encoded JSON>. Offending keys: ${legacyKeys.join(', ')}`;
}
