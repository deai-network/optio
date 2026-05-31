# Process Metadata Filter Language

This spec was written against the following baseline:

**Base revision:** `5fc9f659230cbbef9cd98406d58b716aa385d0cd` on branch `main` (as of 2026-05-31T02:01:42Z)

## Summary

Extend `ProcessMetadataFilter` from a flat `{key: value}` AND-of-equality dictionary to a nested predicate tree supporting `AND` / `OR` / `NOT` composition and a small per-leaf operator vocabulary, while remaining backwards compatible with the existing flat shape. The new shape applies to every API surface that already accepts `ProcessMetadataFilter`: REST list, REST resync, SSE stream, and five engine RPCs.

The design adopts the Prisma/Hasura-style nested input object pattern (typed end-to-end via Zod), keeps a single canonical schema in `optio-contracts`, ships ergonomic builder helpers alongside it, and extends the existing Mongo translator in `optio-api`. No new packages, no DSL parser, no third-party filter library.

## Motivation

The current `ProcessMetadataFilter` is `z.record(z.unknown())`. The server translator (`metadataFilterToMongo` in `packages/optio-api/src/metadata-filter-query.ts`) flattens it into `{ "metadata.<k>": v }` and hands the result to Mongo, which interprets multiple keys as implicit conjunction. There is no way to express disjunction, negation, value-list membership, comparisons, or existence at the filter layer.

Concretely, the UI needs to express `(A AND B) OR (C AND D)` against metadata sub-paths. The flat shape cannot express it.

Two unattractive alternatives were considered and rejected:

- **Pass Mongo Query Language through verbatim.** Binds the public contract to MongoDB forever, lets clients send `$where` / `$function` (security risk), forces every consumer to know Mongo's operator quirks, and produces an open-ended validation surface.
- **Adopt an OData / RSQL string DSL.** Adds a parser, mismatches the TS-typed contracts-first style of this codebase, and gives no ergonomic win when callers want to build filters programmatically.

The Prisma/Hasura pattern fits this codebase: it's already TS, already Zod-typed, callers compose object literals, and the schema is the contract.

## Schema (in `optio-contracts`)

Defined in `packages/optio-contracts/src/schemas/process.ts`, replacing the current `ProcessMetadataFilterSchema = z.record(z.unknown())`.

```ts
// Allowed leaf value types.
const FilterScalar = z.union([z.string(), z.number(), z.boolean(), z.null()]);

// Operator object that lives at each leaf.
const FilterLeafOps = z
  .object({
    eq:     FilterScalar.optional(),
    ne:     FilterScalar.optional(),
    in:     z.array(FilterScalar).optional(),
    nin:    z.array(FilterScalar).optional(),
    exists: z.boolean().optional(),
    gt:     FilterScalar.optional(),
    gte:    FilterScalar.optional(),
    lt:     FilterScalar.optional(),
    lte:    FilterScalar.optional(),
  })
  .strict()
  .refine(o => Object.keys(o).length > 0, 'leaf needs at least one operator');

// Field-path validator. Dotted segments under `metadata.`. Non-empty
// segments separated by single dots; no `$` anywhere.
const FilterFieldPath = z.string().regex(/^[^.$]+(\.[^.$]+)*$/, 'invalid field path');

// Recursive predicate tree. A predicate node is exactly one of:
//   { AND: [...] }   { OR: [...] }   { NOT: ... }   { "field": LeafOps, ... }
// No mixing combinator keys with field keys in the same object.
export const ProcessMetadataPredicateSchema: z.ZodType = z.lazy(() =>
  z.union([
    z.object({ AND: z.array(ProcessMetadataPredicateSchema).min(1) }).strict(),
    z.object({ OR:  z.array(ProcessMetadataPredicateSchema).min(1) }).strict(),
    z.object({ NOT: ProcessMetadataPredicateSchema }).strict(),
    z.record(FilterFieldPath, FilterLeafOps),
  ]),
);

// Legacy flat shape: top-level keys are field names, values are scalars,
// semantics = AND of equalities.
const ProcessMetadataFilterLegacySchema = z.record(FilterFieldPath, FilterScalar);

// Public union (backwards compatible).
export const ProcessMetadataFilterSchema = z.union([
  ProcessMetadataPredicateSchema,
  ProcessMetadataFilterLegacySchema,
]);

export type FilterScalar              = z.infer<typeof FilterScalar>;
export type FilterLeafOps             = z.infer<typeof FilterLeafOps>;
export type ProcessMetadataPredicate  = z.infer<typeof ProcessMetadataPredicateSchema>;
export type ProcessMetadataFilter     = z.infer<typeof ProcessMetadataFilterSchema>;
```

### Union disambiguation

The two branches are structurally distinguishable:

- A node is **predicate** iff it has a top-level `AND` / `OR` / `NOT` key, OR any value is an operator object (record-of-operators).
- Otherwise it is **legacy** (all values are scalars, no combinator keys).

Zod's `union` tries branches in declaration order; both branches succeed only on input that is also valid in the other (empty object, single-key scalar-only object). For those overlap cases, semantics are identical (legacy `{k: v}` == predicate `{k: {eq: v}}`), so picking either is safe. The translator selects branch explicitly (see "Server translation" below).

### Combinator-vs-field disambiguation within one node

`.strict()` on the combinator branches forbids extra keys. A node `{ AND: [...], foo: {eq:'x'} }` will not match any predicate branch; users must wrap as `{ AND: [{ AND: [...] }, { foo: {eq:'x'} }] }`. This is a deliberate constraint that keeps translation a clean dispatch.

### Reserved-word risk

Metadata keys are user-supplied. A user metadata key literally named `AND`, `OR`, or `NOT` collides with combinator keywords in the predicate branch. This is the documented cost of the concise Prisma-style shape; alternatives (explicit `{op, field, value}` leaves, or `_AND` / `_OR` prefixes) were considered and traded off for concision. Callers with such keys can still use the legacy flat shape, where every key is unambiguously a field.

### Path safety

`FilterFieldPath` rejects strings containing `$`, leading or trailing `.`, or empty segments. Combined with the auto-prefix `metadata.` at translation time, this prevents Mongo operator injection through user-supplied paths.

## Public helpers (in `optio-contracts`)

Exported alongside the schema. Pure builders, no Zod runtime cost. Live in `packages/optio-contracts/src/process-filter-helpers.ts`.

```ts
import type { ProcessMetadataPredicate, FilterScalar } from './schemas/process.js';

export const and = (...preds: ProcessMetadataPredicate[]): ProcessMetadataPredicate => ({ AND: preds });
export const or  = (...preds: ProcessMetadataPredicate[]): ProcessMetadataPredicate => ({ OR:  preds });
export const not = (pred: ProcessMetadataPredicate): ProcessMetadataPredicate     => ({ NOT: pred });

export const eq     = (field: string, v: FilterScalar)     => ({ [field]: { eq: v } });
export const ne     = (field: string, v: FilterScalar)     => ({ [field]: { ne: v } });
export const isIn   = (field: string, v: FilterScalar[])   => ({ [field]: { in: v } });
export const notIn  = (field: string, v: FilterScalar[])   => ({ [field]: { nin: v } });
export const exists = (field: string, v = true)            => ({ [field]: { exists: v } });
export const gt     = (field: string, v: FilterScalar)     => ({ [field]: { gt: v } });
export const gte    = (field: string, v: FilterScalar)     => ({ [field]: { gte: v } });
export const lt     = (field: string, v: FilterScalar)     => ({ [field]: { lt: v } });
export const lte    = (field: string, v: FilterScalar)     => ({ [field]: { lte: v } });
```

`isIn` / `notIn` instead of `in` / `nin` (JS reserved-word collision on `in`). Combinator `not` and leaf negation collapse into one symbol — to negate a single leaf, use `not(eq(...))`.

Example, reproducing `(tag=demo AND owner=kris) OR (tag=prod AND region in [us,eu])`:

```ts
import { and, or, eq, isIn } from 'optio-contracts';

const filter = or(
  and(eq('tag', 'demo'), eq('owner', 'kris')),
  and(eq('tag', 'prod'), isIn('region', ['us', 'eu'])),
);
```

## Server translation (in `optio-api`)

Extend `packages/optio-api/src/metadata-filter-query.ts`. The public function `metadataFilterToMongo` becomes a dispatcher:

```ts
export function metadataFilterToMongo(
  filter: ProcessMetadataFilter | undefined,
): Record<string, unknown> {
  if (!filter) return {};
  if (isLegacyFlatFilter(filter)) return legacyToMongo(filter);
  return predicateToMongo(filter);
}

function isLegacyFlatFilter(f: ProcessMetadataFilter): boolean {
  for (const [k, v] of Object.entries(f)) {
    if (k === 'AND' || k === 'OR' || k === 'NOT') return false;
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) return false;
  }
  return true;
}

function legacyToMongo(filter: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(filter)) out[`metadata.${k}`] = v;
  return out;
}

function predicateToMongo(p: ProcessMetadataPredicate): Record<string, unknown> {
  if ('AND' in p) return { $and: p.AND.map(predicateToMongo) };
  if ('OR'  in p) return { $or:  p.OR .map(predicateToMongo) };
  if ('NOT' in p) return { $nor: [predicateToMongo(p.NOT)] };
  // record-of-leaves
  const entries = Object.entries(p as Record<string, FilterLeafOps>);
  if (entries.length === 1) {
    const [field, ops] = entries[0]!;
    return { [`metadata.${field}`]: leafOpsToMongo(ops) };
  }
  return {
    $and: entries.map(([field, ops]) => ({ [`metadata.${field}`]: leafOpsToMongo(ops) })),
  };
}

function leafOpsToMongo(ops: FilterLeafOps): Record<string, unknown> {
  const m: Record<string, unknown> = {};
  if (ops.eq     !== undefined) m.$eq     = ops.eq;
  if (ops.ne     !== undefined) m.$ne     = ops.ne;
  if (ops.in     !== undefined) m.$in     = ops.in;
  if (ops.nin    !== undefined) m.$nin    = ops.nin;
  if (ops.exists !== undefined) m.$exists = ops.exists;
  if (ops.gt     !== undefined) m.$gt     = ops.gt;
  if (ops.gte    !== undefined) m.$gte    = ops.gte;
  if (ops.lt     !== undefined) m.$lt     = ops.lt;
  if (ops.lte    !== undefined) m.$lte    = ops.lte;
  return m;
}
```

Notes:

- Negation uses `$nor` over a singleton array rather than `$not`, because Mongo's `$not` cannot wrap a top-level field expression containing combinators. `$nor: [<predicate>]` is exactly the negation of one predicate.
- Single-leaf-per-node nodes emit a flat `{ "metadata.field": {...} }`; multi-leaf nodes split into `$and` of single-key objects. Both forms are semantically equivalent in Mongo; splitting keeps the translator output regular and easy to test.
- `parseMetadataFilterQuery` and `MetadataFilterQueryParamSchema` (the JSON-in-querystring wrapper) gain no new code — they already pipe through `ProcessMetadataFilterSchema`, which widens automatically.
- `detectLegacyMetadataParams` and `formatLegacyMetadataMessage` (handlers for the removed pre-`metadataFilter` `metadata.*` query params) are untouched.

## Hook + consumer updates (in `optio-ui`)

`useProcessListStream` signature is unchanged. `ProcessMetadataFilter` widens to a union of legacy and predicate, so all existing callers continue to compile and run.

`filterKey` (currently `JSON.stringify(options.metadataFilter)` in `useProcessListStream.tsx`) remains the cache key for the EventSource connection. Object key order in JS is insertion-order-stable; helpers emit consistent shapes; this is sufficient for the cache-key purpose. If empirically observed cache misses on semantically equal filters become a problem, a canonicalization step (sorted keys) can be added later — not required for v1.

No new exports from `optio-ui`. Consumers import predicate types and helpers from `optio-contracts`.

## Affected endpoints

All sites consuming `ProcessMetadataFilterSchema`:

REST (`packages/optio-contracts/src/api-to-frontend.ts`):
- `GET /processes` — query param `metadataFilter` (via `MetadataFilterQueryParamSchema`)
- `POST /processes/resync` — body field

SSE (`packages/optio-api/src/adapters/nextjs-app.ts`, not in ts-rest contract):
- `GET /api/processes/stream` — query param `metadataFilter`

Engine RPC (`packages/optio-contracts/src/optio-engine-to-api.ts`):
- `groupCancel` — params `metadataFilter`
- `groupCancelAndWait` — params `metadataFilter`
- `blockLaunches` — params `launchFilter` (different name, same schema)
- `unblockLaunches` — params `launchFilter`
- `resync` (notification) — params `metadataFilter`

All eight sites share one schema; widening `ProcessMetadataFilterSchema` upgrades them all simultaneously. No per-endpoint code change is required.

## Backwards compatibility

- Wire-level: the new schema is a Zod union including the legacy flat shape, so existing payloads validate unchanged.
- Translator: dispatches by structural inspection; legacy payloads go through `legacyToMongo`, which is byte-identical to today's `metadataFilterToMongo` output.
- Hook callers: any existing `metadataFilter: { foo: 'bar' }` continues to compile and run.
- `packages/optio-demo/interop/run.ts:187` (`metadataFilter: { tag: 'demo' }`): unchanged behavior.
- Excavator (`~/private/Excavator`, scheduled for follow-up per `project_excavator_patch.md`): legacy shape still accepted; migration optional, not forced.

No deprecation in v1. Legacy stays first-class. If telemetry later shows no legacy traffic in production, removal can be revisited.

## Testing

### `optio-contracts`

Extend `src/__tests__/process-schema.test.ts`:

- Legacy flat shape: existing tests stay green.
- Predicate shape: parse `or(and(eq, eq), and(eq, isIn))`, assert structural round-trip.
- Reject:
  - `AND: []` (min(1))
  - `NOT: [...]` (array; NOT takes a single predicate)
  - leaf with zero operator keys
  - leaf with unknown operator key
  - field path containing `$`
  - field path with leading or trailing `.`
  - field path with empty segment (e.g. `"a..b"`)
  - object mixing combinator key and field key (e.g. `{ AND: [...], foo: {eq:'x'} }`)
- Helpers smoke: `and / or / not / eq / ne / isIn / notIn / exists / gt / gte / lt / lte` produce schema-valid output.

### `optio-api`

Extend `src/__tests__/metadata-filter-query.test.ts`:

- Legacy translator: existing tests stay green.
- Predicate translator:
  - single leaf, single op → `{ "metadata.foo": { $eq: "x" } }`
  - single leaf, multiple ops → `{ "metadata.foo": { $eq, $in } }`
  - multi-leaf node → `$and` of single-key objects
  - `AND` / `OR` / `NOT` → `$and` / `$or` / `$nor`
  - nested `or(and(...), and(...))` → correct shape
  - operator coverage: one test per operator (`eq` / `ne` / `in` / `nin` / `exists` / `gt` / `gte` / `lt` / `lte`)
  - dotted path: `"foo.bar"` → `"metadata.foo.bar"`
- `isLegacyFlatFilter`: legacy vs predicate selection
- `parseMetadataFilterQuery`: JSON-string-encoded predicate parses and round-trips

Integration smoke (`optio-api`): one end-to-end test against `mongodb-memory-server` (per `feedback_mongodb_docker.md`) inserting representative process docs, querying with a predicate tree, asserting matched docs.

### `optio-ui`

Extend `src/__tests__/useProcessListStream.test.tsx`:

- Existing flat-filter tests stay green.
- One predicate-tree test:
  - `filterKey` differs from a flat-shape filter
  - `metadataFilter` query param arrives URL-encoded on the EventSource URL
  - SSE update propagates back to the hook output

### `optio-demo`

No test change. Optionally add one example using the new shape to exercise it in CI end-to-end (per `project_optio_demo_consumes_opencode.md`).

## Out of scope (deliberate)

- String matching operators (`contains`, `startsWith`, regex). Defer until a concrete UI ask drives the RE2-vs-PCRE-vs-Mongo-regex decision.
- Top-level (non-`metadata.*`) field access. Filter remains metadata-only; the schema and translator hard-code the `metadata.` prefix.
- Array-leaf semantics, `$elemMatch`, projection. Mongo's default array-equality behavior applies (a leaf `eq:"x"` on an array-typed metadata field matches docs whose array contains `x`); call sites that need stricter array semantics can wait for a follow-up spec.
- DSL parser / string syntax. The shape is the API; no `$filter` strings.
- Deprecation or removal of the legacy flat shape.
- Excavator migration to the new shape (separately tracked).

## Migration path for downstream callers

For new code: import helpers from `optio-contracts` and build predicate trees. For existing code: no action required; flat filters continue to work. The recommended progression is opportunistic — when a call site needs disjunction or richer leaf operators, rewrite it; otherwise leave it alone.
