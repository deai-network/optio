# Process Metadata Filter Language Implementation Plan

> **For agentic workers:** This plan is **parallel-shaped**: every file is owned by exactly one task, tasks are file-disjoint, and per-task verification (tsc / pnpm test) is **deferred to the final verification task** (T9). Tasks T1–T8 are intended to be dispatched concurrently to a swarm. Do NOT gate each task on green tests; land the bulk, then verify at the end. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat `{key: value}` AND-only `ProcessMetadataFilter` with a backwards-compatible Zod union accepting either the legacy shape or a new Prisma/Hasura-style nested predicate tree (`AND` / `OR` / `NOT` + per-leaf operator vocabulary), translated to MongoDB queries server-side. All eight API surfaces that share `ProcessMetadataFilterSchema` automatically upgrade.

**Architecture:** Single canonical Zod schema in `optio-contracts` (union of legacy flat record and recursive predicate tree). Pure builder helpers (`and`, `or`, `not`, `eq`, `ne`, `isIn`, `notIn`, `exists`, `gt`, `gte`, `lt`, `lte`) shipped alongside the schema. Server translator in `optio-api` dispatches by structural detection between `legacyToMongo` and `predicateToMongo`. Field-path strings auto-prefixed with `metadata.` at translation time; `$` and empty segments rejected at schema layer.

**Tech Stack:** TypeScript, Zod 3, ts-rest, MongoDB Node driver, Vitest. pnpm workspace monorepo. The Docker MongoDB at `mongodb://localhost:27017` is used for integration tests (per `feedback_mongodb_docker.md`); do NOT use `mongodb-memory-server`.

**Spec:** `docs/2026-05-31-process-filter-language-design.md` (revision `4488a59`).

**Parallel-execution contract (pinned across all tasks):**

The schema, helper signatures, and translator entry points below are the immutable shared contract. Every task must reference these exact names and shapes; do not rename, do not refactor, do not "improve" them mid-flight. Consistency across concurrent agents depends on this.

```
// Schema names (exported from optio-contracts):
FilterScalar, FilterLeafOps, FilterFieldPath,
ProcessMetadataPredicateSchema, ProcessMetadataFilterLegacySchema,
ProcessMetadataFilterSchema (union)

// Types (exported from optio-contracts):
FilterScalar, FilterLeafOps, ProcessMetadataPredicate, ProcessMetadataFilter

// Helper signatures (exported from optio-contracts):
and(...preds: ProcessMetadataPredicate[]): ProcessMetadataPredicate
or(...preds: ProcessMetadataPredicate[]): ProcessMetadataPredicate
not(pred: ProcessMetadataPredicate): ProcessMetadataPredicate
eq(field: string, v: FilterScalar): ProcessMetadataPredicate
ne(field: string, v: FilterScalar): ProcessMetadataPredicate
isIn(field: string, v: FilterScalar[]): ProcessMetadataPredicate
notIn(field: string, v: FilterScalar[]): ProcessMetadataPredicate
exists(field: string, v?: boolean): ProcessMetadataPredicate
gt(field: string, v: FilterScalar): ProcessMetadataPredicate
gte(field: string, v: FilterScalar): ProcessMetadataPredicate
lt(field: string, v: FilterScalar): ProcessMetadataPredicate
lte(field: string, v: FilterScalar): ProcessMetadataPredicate

// Translator entry (optio-api/src/metadata-filter-query.ts), unchanged signature:
metadataFilterToMongo(filter: ProcessMetadataFilter | undefined): Record<string, unknown>
```

---

## File ownership map

| File | Owner |
|------|-------|
| `packages/optio-contracts/src/schemas/process.ts` | T1 |
| `packages/optio-contracts/src/process-filter-helpers.ts` (NEW) | T2 |
| `packages/optio-contracts/src/index.ts` | T3 |
| `packages/optio-api/src/metadata-filter-query.ts` | T4 |
| `packages/optio-contracts/src/__tests__/process-schema.test.ts` | T5 |
| `packages/optio-api/src/__tests__/metadata-filter-query.test.ts` | T6 |
| `packages/optio-ui/src/__tests__/useProcessListStream.test.tsx` | T7 |
| `packages/optio-api/src/__tests__/metadata-filter-mongo-integration.test.ts` (NEW) | T8 |

T9 is the final verification task and touches no source files (runs typecheck + tests across affected packages).

---

### Task 1: Replace `ProcessMetadataFilterSchema` with widened union

**Files:**
- Modify: `packages/optio-contracts/src/schemas/process.ts` (replace lines 82 and the trailing type exports around line 100)

- [ ] **Step 1: Replace the filter schema block**

Find the current block (around lines 82–100):

```ts
export const ProcessMetadataFilterSchema = z.record(z.unknown());

export const MetadataFilterQueryParamSchema = z
  .string()
  .transform((s, ctx) => {
    try {
      return JSON.parse(s);
    } catch {
      ctx.addIssue({ code: 'custom', message: 'metadataFilter must be valid JSON' });
      return z.NEVER;
    }
  })
  .pipe(ProcessMetadataFilterSchema)
  .optional();

export type Process = z.infer<typeof ProcessSchema>;
export type ProcessState = z.infer<typeof ProcessStateSchema>;
export type LogEntry = z.infer<typeof LogEntrySchema>;
export type ProcessMetadataFilter = z.infer<typeof ProcessMetadataFilterSchema>;
export type BrowserOpenRequest = z.infer<typeof BrowserOpenRequestSchema>;
export type SessionEvent = z.infer<typeof SessionEventSchema>;
```

Replace it with:

```ts
// Allowed leaf scalar value types in the filter.
export const FilterScalar = z.union([z.string(), z.number(), z.boolean(), z.null()]);

// Field-path validator: dotted segments under `metadata.*`, each non-empty,
// no `$` anywhere (defense against Mongo operator injection through paths).
export const FilterFieldPath = z
  .string()
  .regex(/^[^.$]+(\.[^.$]+)*$/, 'invalid field path');

// Operator object that lives at each leaf.
export const FilterLeafOps = z
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
  .refine((o) => Object.keys(o).length > 0, 'leaf needs at least one operator');

// Recursive predicate tree. A predicate node is exactly one of:
//   { AND: [...] }   { OR: [...] }   { NOT: ... }   { "field": LeafOps, ... }
// .strict() forbids mixing combinator keys with field keys in one object.
export const ProcessMetadataPredicateSchema: z.ZodType<unknown> = z.lazy(() =>
  z.union([
    z.object({ AND: z.array(ProcessMetadataPredicateSchema).min(1) }).strict(),
    z.object({ OR:  z.array(ProcessMetadataPredicateSchema).min(1) }).strict(),
    z.object({ NOT: ProcessMetadataPredicateSchema }).strict(),
    z.record(FilterFieldPath, FilterLeafOps),
  ]),
);

// Legacy flat shape: keys are field names, values are scalars (implicit AND of equality).
export const ProcessMetadataFilterLegacySchema = z.record(FilterFieldPath, FilterScalar);

// Public union (backwards compatible). Predicate branch first so it wins on
// any input that has combinator keys or operator-object values; flat scalar
// shapes fall through to the legacy branch.
export const ProcessMetadataFilterSchema = z.union([
  ProcessMetadataPredicateSchema,
  ProcessMetadataFilterLegacySchema,
]);

export const MetadataFilterQueryParamSchema = z
  .string()
  .transform((s, ctx) => {
    try {
      return JSON.parse(s);
    } catch {
      ctx.addIssue({ code: 'custom', message: 'metadataFilter must be valid JSON' });
      return z.NEVER;
    }
  })
  .pipe(ProcessMetadataFilterSchema)
  .optional();

export type Process = z.infer<typeof ProcessSchema>;
export type ProcessState = z.infer<typeof ProcessStateSchema>;
export type LogEntry = z.infer<typeof LogEntrySchema>;
export type FilterScalar = z.infer<typeof FilterScalar>;
export type FilterLeafOps = z.infer<typeof FilterLeafOps>;
export type ProcessMetadataPredicate = z.infer<typeof ProcessMetadataPredicateSchema>;
export type ProcessMetadataFilter = z.infer<typeof ProcessMetadataFilterSchema>;
export type BrowserOpenRequest = z.infer<typeof BrowserOpenRequestSchema>;
export type SessionEvent = z.infer<typeof SessionEventSchema>;
```

Leave everything else in this file untouched (imports, ProcessSchema, etc.).

- [ ] **Step 2: Commit**

```bash
git add packages/optio-contracts/src/schemas/process.ts
git commit -m "feat(contracts): widen ProcessMetadataFilter to predicate-tree union"
```

---

### Task 2: Create `process-filter-helpers.ts`

**Files:**
- Create: `packages/optio-contracts/src/process-filter-helpers.ts`

- [ ] **Step 1: Write the helpers module**

Create the file with this exact content:

```ts
import type {
  ProcessMetadataPredicate,
  FilterScalar,
} from './schemas/process.js';

// Combinators.
export const and = (...preds: ProcessMetadataPredicate[]): ProcessMetadataPredicate =>
  ({ AND: preds } as ProcessMetadataPredicate);

export const or = (...preds: ProcessMetadataPredicate[]): ProcessMetadataPredicate =>
  ({ OR: preds } as ProcessMetadataPredicate);

export const not = (pred: ProcessMetadataPredicate): ProcessMetadataPredicate =>
  ({ NOT: pred } as ProcessMetadataPredicate);

// Leaf builders. `field` is a dotted path under `metadata.*` (auto-prefixed
// at translation time). `isIn` / `notIn` avoid the `in` JS reserved-word
// collision; `not` is the combinator and does not double as a leaf negation
// (use `not(eq(...))`).
export const eq = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  ({ [field]: { eq: v } } as ProcessMetadataPredicate);

export const ne = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  ({ [field]: { ne: v } } as ProcessMetadataPredicate);

export const isIn = (field: string, v: FilterScalar[]): ProcessMetadataPredicate =>
  ({ [field]: { in: v } } as ProcessMetadataPredicate);

export const notIn = (field: string, v: FilterScalar[]): ProcessMetadataPredicate =>
  ({ [field]: { nin: v } } as ProcessMetadataPredicate);

export const exists = (field: string, v: boolean = true): ProcessMetadataPredicate =>
  ({ [field]: { exists: v } } as ProcessMetadataPredicate);

export const gt = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  ({ [field]: { gt: v } } as ProcessMetadataPredicate);

export const gte = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  ({ [field]: { gte: v } } as ProcessMetadataPredicate);

export const lt = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  ({ [field]: { lt: v } } as ProcessMetadataPredicate);

export const lte = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  ({ [field]: { lte: v } } as ProcessMetadataPredicate);
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-contracts/src/process-filter-helpers.ts
git commit -m "feat(contracts): add process filter builder helpers"
```

---

### Task 3: Re-export new schema and helpers from `optio-contracts/index.ts`

**Files:**
- Modify: `packages/optio-contracts/src/index.ts`

- [ ] **Step 1: Add re-exports**

Open the file. The existing schema export block looks like:

```ts
export { ProcessSchema, ProcessStateSchema, LogEntrySchema,
         ProcessMetadataFilterSchema, MetadataFilterQueryParamSchema,
         BrowserOpenRequestSchema, SessionEventSchema } from './schemas/process.js';
```

Replace it with (extends the list to include the new schemas):

```ts
export { ProcessSchema, ProcessStateSchema, LogEntrySchema,
         ProcessMetadataFilterSchema, MetadataFilterQueryParamSchema,
         ProcessMetadataPredicateSchema, ProcessMetadataFilterLegacySchema,
         FilterScalar, FilterLeafOps, FilterFieldPath,
         BrowserOpenRequestSchema, SessionEventSchema } from './schemas/process.js';
```

The existing type export block looks like:

```ts
export type { Process, ProcessState, LogEntry, ProcessMetadataFilter,
              BrowserOpenRequest, SessionEvent } from './schemas/process.js';
```

Replace it with (adds new types):

```ts
export type { Process, ProcessState, LogEntry, ProcessMetadataFilter,
              ProcessMetadataPredicate, FilterScalar, FilterLeafOps,
              BrowserOpenRequest, SessionEvent } from './schemas/process.js';
```

Then append a new export line for the helpers, anywhere after the existing type-exports section:

```ts
// Filter builder helpers
export { and, or, not, eq, ne, isIn, notIn, exists, gt, gte, lt, lte } from './process-filter-helpers.js';
```

Note: the existing exports re-export both schema and type names (`FilterScalar` and `FilterLeafOps` are Zod schemas at the value level and types at the type level — both are intentionally re-exported in both blocks above, which is consistent with how the existing file handles similar dual exports).

- [ ] **Step 2: Commit**

```bash
git add packages/optio-contracts/src/index.ts
git commit -m "feat(contracts): re-export new filter schemas, types, and helpers"
```

---

### Task 4: Extend translator with predicate-tree dispatch

**Files:**
- Modify: `packages/optio-api/src/metadata-filter-query.ts` (extend the file; replace `metadataFilterToMongo` and add helpers)

- [ ] **Step 1: Replace the file contents**

Open `packages/optio-api/src/metadata-filter-query.ts`. The current file is reproduced here in full; replace it with the version below.

Current file (verbatim):

```ts
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

export function formatLegacyMetadataMessage(legacyKeys: string[]): string {
  return `Legacy 'metadata.*' query params are no longer supported. ` +
    `Use ?metadataFilter=<URL-encoded JSON>. Offending keys: ${legacyKeys.join(', ')}`;
}
```

Replace with:

```ts
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
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-api/src/metadata-filter-query.ts
git commit -m "feat(api): dispatch predicate-tree filters in metadataFilterToMongo"
```

---

### Task 5: Extend contract schema tests

**Files:**
- Modify: `packages/optio-contracts/src/__tests__/process-schema.test.ts`

- [ ] **Step 1: Append new describe blocks**

Open the file. At the top, the imports currently look like:

```ts
import { describe, it, expect } from 'vitest';
import { ProcessSchema } from '../schemas/process.js';
import { MetadataFilterQueryParamSchema } from '../schemas/process.js';
```

Replace the imports block with:

```ts
import { describe, it, expect } from 'vitest';
import {
  ProcessSchema,
  MetadataFilterQueryParamSchema,
  ProcessMetadataFilterSchema,
  ProcessMetadataPredicateSchema,
  FilterFieldPath,
  FilterLeafOps,
} from '../schemas/process.js';
import {
  and, or, not, eq, ne, isIn, notIn, exists, gt, gte, lt, lte,
} from '../process-filter-helpers.js';
```

At the very bottom of the file (after the existing `describe('MetadataFilterQueryParamSchema', ...)` block), append:

```ts
describe('ProcessMetadataFilterSchema (legacy flat shape, backwards-compatible)', () => {
  it('accepts an empty object', () => {
    expect(ProcessMetadataFilterSchema.parse({})).toEqual({});
  });

  it('accepts a flat scalar record (legacy)', () => {
    const v = { targetId: 'abc', kind: 'x', n: 5, b: true, nl: null };
    expect(ProcessMetadataFilterSchema.parse(v)).toEqual(v);
  });

  it('accepts dotted field path in legacy shape', () => {
    expect(ProcessMetadataFilterSchema.parse({ 'foo.bar': 'x' })).toEqual({ 'foo.bar': 'x' });
  });
});

describe('ProcessMetadataPredicateSchema (new predicate-tree shape)', () => {
  it('accepts a single-leaf single-op predicate', () => {
    const p = { foo: { eq: 'x' } };
    expect(ProcessMetadataPredicateSchema.parse(p)).toEqual(p);
  });

  it('accepts a single-leaf multi-op predicate', () => {
    const p = { foo: { gt: 1, lte: 10 } };
    expect(ProcessMetadataPredicateSchema.parse(p)).toEqual(p);
  });

  it('accepts a multi-leaf predicate (implicit AND across keys)', () => {
    const p = { foo: { eq: 'x' }, bar: { in: [1, 2] } };
    expect(ProcessMetadataPredicateSchema.parse(p)).toEqual(p);
  });

  it('accepts AND of leaves', () => {
    const p = { AND: [{ a: { eq: 1 } }, { b: { eq: 2 } }] };
    expect(ProcessMetadataPredicateSchema.parse(p)).toEqual(p);
  });

  it('accepts OR of leaves', () => {
    const p = { OR: [{ a: { eq: 1 } }, { b: { eq: 2 } }] };
    expect(ProcessMetadataPredicateSchema.parse(p)).toEqual(p);
  });

  it('accepts NOT of a single predicate', () => {
    const p = { NOT: { a: { eq: 1 } } };
    expect(ProcessMetadataPredicateSchema.parse(p)).toEqual(p);
  });

  it('accepts nested combinators: (A AND B) OR (C AND D)', () => {
    const p = {
      OR: [
        { AND: [{ tag: { eq: 'demo' } }, { owner: { eq: 'kris' } }] },
        { AND: [{ tag: { eq: 'prod' } }, { region: { in: ['us', 'eu'] } }] },
      ],
    };
    expect(ProcessMetadataPredicateSchema.parse(p)).toEqual(p);
  });

  it('accepts dotted field paths in predicate leaves', () => {
    const p = { 'foo.bar.baz': { eq: 'x' } };
    expect(ProcessMetadataPredicateSchema.parse(p)).toEqual(p);
  });

  it('rejects empty AND array', () => {
    const r = ProcessMetadataPredicateSchema.safeParse({ AND: [] });
    expect(r.success).toBe(false);
  });

  it('rejects empty OR array', () => {
    const r = ProcessMetadataPredicateSchema.safeParse({ OR: [] });
    expect(r.success).toBe(false);
  });

  it('rejects NOT carrying an array (NOT takes a single predicate)', () => {
    const r = ProcessMetadataPredicateSchema.safeParse({ NOT: [{ a: { eq: 1 } }] });
    expect(r.success).toBe(false);
  });

  it('rejects an empty leaf operator object', () => {
    const r = ProcessMetadataPredicateSchema.safeParse({ foo: {} });
    expect(r.success).toBe(false);
  });

  it('rejects an unknown operator in a leaf', () => {
    const r = ProcessMetadataPredicateSchema.safeParse({ foo: { eq: 'x', regex: '^a' } });
    expect(r.success).toBe(false);
  });

  it('rejects mixing combinator and field keys in the same object', () => {
    const r = ProcessMetadataPredicateSchema.safeParse({
      AND: [{ a: { eq: 1 } }],
      foo: { eq: 'x' },
    });
    expect(r.success).toBe(false);
  });
});

describe('FilterFieldPath', () => {
  it('accepts simple alphanumeric path', () => {
    expect(FilterFieldPath.parse('foo')).toBe('foo');
  });

  it('accepts dotted multi-segment path', () => {
    expect(FilterFieldPath.parse('foo.bar.baz')).toBe('foo.bar.baz');
  });

  it('rejects path containing $', () => {
    expect(FilterFieldPath.safeParse('$where').success).toBe(false);
  });

  it('rejects path containing $ in any segment', () => {
    expect(FilterFieldPath.safeParse('foo.$bar').success).toBe(false);
  });

  it('rejects leading dot', () => {
    expect(FilterFieldPath.safeParse('.foo').success).toBe(false);
  });

  it('rejects trailing dot', () => {
    expect(FilterFieldPath.safeParse('foo.').success).toBe(false);
  });

  it('rejects empty segment (consecutive dots)', () => {
    expect(FilterFieldPath.safeParse('a..b').success).toBe(false);
  });

  it('rejects empty string', () => {
    expect(FilterFieldPath.safeParse('').success).toBe(false);
  });
});

describe('FilterLeafOps strict mode', () => {
  it('accepts a single valid op', () => {
    expect(FilterLeafOps.parse({ eq: 'x' })).toEqual({ eq: 'x' });
  });

  it('rejects unknown keys (strict)', () => {
    expect(FilterLeafOps.safeParse({ eq: 'x', startsWith: 'a' }).success).toBe(false);
  });
});

describe('process-filter-helpers', () => {
  it('and produces AND wrapper', () => {
    const p = and(eq('a', 1), eq('b', 2));
    expect(p).toEqual({ AND: [{ a: { eq: 1 } }, { b: { eq: 2 } }] });
    expect(ProcessMetadataPredicateSchema.safeParse(p).success).toBe(true);
  });

  it('or produces OR wrapper', () => {
    const p = or(eq('a', 1), eq('b', 2));
    expect(p).toEqual({ OR: [{ a: { eq: 1 } }, { b: { eq: 2 } }] });
  });

  it('not produces NOT wrapper', () => {
    expect(not(eq('a', 1))).toEqual({ NOT: { a: { eq: 1 } } });
  });

  it('leaf builders cover all operators', () => {
    expect(eq('f', 1)).toEqual({ f: { eq: 1 } });
    expect(ne('f', 1)).toEqual({ f: { ne: 1 } });
    expect(isIn('f', [1, 2])).toEqual({ f: { in: [1, 2] } });
    expect(notIn('f', [1, 2])).toEqual({ f: { nin: [1, 2] } });
    expect(exists('f')).toEqual({ f: { exists: true } });
    expect(exists('f', false)).toEqual({ f: { exists: false } });
    expect(gt('f', 1)).toEqual({ f: { gt: 1 } });
    expect(gte('f', 1)).toEqual({ f: { gte: 1 } });
    expect(lt('f', 1)).toEqual({ f: { lt: 1 } });
    expect(lte('f', 1)).toEqual({ f: { lte: 1 } });
  });

  it('builders compose into the spec example: (A AND B) OR (C AND D)', () => {
    const p = or(
      and(eq('tag', 'demo'), eq('owner', 'kris')),
      and(eq('tag', 'prod'), isIn('region', ['us', 'eu'])),
    );
    expect(ProcessMetadataPredicateSchema.safeParse(p).success).toBe(true);
    expect(p).toEqual({
      OR: [
        { AND: [{ tag: { eq: 'demo' } }, { owner: { eq: 'kris' } }] },
        { AND: [{ tag: { eq: 'prod' } }, { region: { in: ['us', 'eu'] } }] },
      ],
    });
  });
});
```

Do not modify any of the pre-existing `describe` blocks in this file (they verify legacy behavior and must continue to pass).

- [ ] **Step 2: Commit**

```bash
git add packages/optio-contracts/src/__tests__/process-schema.test.ts
git commit -m "test(contracts): cover predicate-tree filter schema, helpers, and field-path safety"
```

---

### Task 6: Extend translator unit tests

**Files:**
- Modify: `packages/optio-api/src/__tests__/metadata-filter-query.test.ts`

- [ ] **Step 1: Update imports and append new describe blocks**

Open the file. The existing import block at the top:

```ts
import { describe, it, expect } from 'vitest';
import {
  parseMetadataFilterQuery,
  metadataFilterToMongo,
  detectLegacyMetadataParams,
  formatLegacyMetadataMessage,
} from '../metadata-filter-query.js';
```

Extend it with `isLegacyFlatFilter` and the helpers from `optio-contracts`:

```ts
import { describe, it, expect } from 'vitest';
import {
  parseMetadataFilterQuery,
  metadataFilterToMongo,
  isLegacyFlatFilter,
  detectLegacyMetadataParams,
  formatLegacyMetadataMessage,
} from '../metadata-filter-query.js';
import {
  and, or, not, eq, ne, isIn, notIn, exists, gt, gte, lt, lte,
} from 'optio-contracts';
```

At the very bottom of the file, after the existing `describe('formatLegacyMetadataMessage', ...)` block, append:

```ts
describe('isLegacyFlatFilter', () => {
  it('treats empty object as legacy', () => {
    expect(isLegacyFlatFilter({})).toBe(true);
  });

  it('treats flat scalar record as legacy', () => {
    expect(isLegacyFlatFilter({ a: 1, b: 'x', c: true, d: null })).toBe(true);
  });

  it('treats AND-key object as predicate', () => {
    expect(isLegacyFlatFilter({ AND: [{ a: { eq: 1 } }] } as any)).toBe(false);
  });

  it('treats OR-key object as predicate', () => {
    expect(isLegacyFlatFilter({ OR: [{ a: { eq: 1 } }] } as any)).toBe(false);
  });

  it('treats NOT-key object as predicate', () => {
    expect(isLegacyFlatFilter({ NOT: { a: { eq: 1 } } } as any)).toBe(false);
  });

  it('treats operator-object values as predicate', () => {
    expect(isLegacyFlatFilter({ foo: { eq: 'x' } } as any)).toBe(false);
  });
});

describe('metadataFilterToMongo (predicate tree)', () => {
  it('translates single-leaf single-op', () => {
    expect(metadataFilterToMongo(eq('foo', 'x'))).toEqual({
      'metadata.foo': { $eq: 'x' },
    });
  });

  it('translates single-leaf multi-op (one node, multiple operators)', () => {
    const p = { foo: { gt: 1, lte: 10 } } as any;
    expect(metadataFilterToMongo(p)).toEqual({
      'metadata.foo': { $gt: 1, $lte: 10 },
    });
  });

  it('translates multi-leaf node into $and of single-key objects', () => {
    const p = { foo: { eq: 'x' }, bar: { in: [1, 2] } } as any;
    expect(metadataFilterToMongo(p)).toEqual({
      $and: [
        { 'metadata.foo': { $eq: 'x' } },
        { 'metadata.bar': { $in: [1, 2] } },
      ],
    });
  });

  it('translates AND into $and', () => {
    expect(metadataFilterToMongo(and(eq('a', 1), eq('b', 2)))).toEqual({
      $and: [
        { 'metadata.a': { $eq: 1 } },
        { 'metadata.b': { $eq: 2 } },
      ],
    });
  });

  it('translates OR into $or', () => {
    expect(metadataFilterToMongo(or(eq('a', 1), eq('b', 2)))).toEqual({
      $or: [
        { 'metadata.a': { $eq: 1 } },
        { 'metadata.b': { $eq: 2 } },
      ],
    });
  });

  it('translates NOT into $nor over a singleton array', () => {
    expect(metadataFilterToMongo(not(eq('a', 1)))).toEqual({
      $nor: [{ 'metadata.a': { $eq: 1 } }],
    });
  });

  it('translates nested (A AND B) OR (C AND D)', () => {
    const p = or(
      and(eq('tag', 'demo'), eq('owner', 'kris')),
      and(eq('tag', 'prod'), isIn('region', ['us', 'eu'])),
    );
    expect(metadataFilterToMongo(p)).toEqual({
      $or: [
        {
          $and: [
            { 'metadata.tag': { $eq: 'demo' } },
            { 'metadata.owner': { $eq: 'kris' } },
          ],
        },
        {
          $and: [
            { 'metadata.tag': { $eq: 'prod' } },
            { 'metadata.region': { $in: ['us', 'eu'] } },
          ],
        },
      ],
    });
  });

  it('translates each leaf operator', () => {
    expect(metadataFilterToMongo(eq('f', 1)))     .toEqual({ 'metadata.f': { $eq: 1 } });
    expect(metadataFilterToMongo(ne('f', 1)))     .toEqual({ 'metadata.f': { $ne: 1 } });
    expect(metadataFilterToMongo(isIn('f', [1]))) .toEqual({ 'metadata.f': { $in: [1] } });
    expect(metadataFilterToMongo(notIn('f', [1]))).toEqual({ 'metadata.f': { $nin: [1] } });
    expect(metadataFilterToMongo(exists('f')))    .toEqual({ 'metadata.f': { $exists: true } });
    expect(metadataFilterToMongo(exists('f', false))).toEqual({ 'metadata.f': { $exists: false } });
    expect(metadataFilterToMongo(gt('f', 1)))     .toEqual({ 'metadata.f': { $gt: 1 } });
    expect(metadataFilterToMongo(gte('f', 1)))    .toEqual({ 'metadata.f': { $gte: 1 } });
    expect(metadataFilterToMongo(lt('f', 1)))     .toEqual({ 'metadata.f': { $lt: 1 } });
    expect(metadataFilterToMongo(lte('f', 1)))    .toEqual({ 'metadata.f': { $lte: 1 } });
  });

  it('prefixes dotted paths with metadata.', () => {
    expect(metadataFilterToMongo(eq('foo.bar.baz', 1))).toEqual({
      'metadata.foo.bar.baz': { $eq: 1 },
    });
  });
});

describe('parseMetadataFilterQuery (predicate JSON round-trip)', () => {
  it('parses a JSON-encoded predicate tree', () => {
    const json = '{"OR":[{"a":{"eq":1}},{"b":{"eq":2}}]}';
    const r = parseMetadataFilterQuery(json);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.value).toEqual({ OR: [{ a: { eq: 1 } }, { b: { eq: 2 } }] });
    }
  });

  it('rejects predicate JSON with invalid field path', () => {
    const json = '{"$where":{"eq":"x"}}';
    const r = parseMetadataFilterQuery(json);
    expect(r.ok).toBe(false);
  });
});
```

Leave existing describe blocks untouched.

- [ ] **Step 2: Commit**

```bash
git add packages/optio-api/src/__tests__/metadata-filter-query.test.ts
git commit -m "test(api): cover predicate-tree translator paths and detector"
```

---

### Task 7: Add hook test for predicate-tree filter

**Files:**
- Modify: `packages/optio-ui/src/__tests__/useProcessListStream.test.tsx`

- [ ] **Step 1: Append one new test case**

Open the file. Inside the existing `describe('useProcessListStream metadataFilter', ...)` block, after the last `it(...)` (the `does not reconnect when filter is unchanged` test, around line 137), but **before the closing `});` of the describe**, append this `it` block:

```tsx
  it('URL-encodes a predicate-tree metadataFilter into the SSE URL', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { useProcessListStream } = await import('../hooks/useProcessListStream.js');
    const { or, and, eq } = await import('optio-contracts');
    function wrapper({ children }: { children: ReactNode }) {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      return (
        <QueryClientProvider client={client}>
          <OptioProvider prefix="test" database="test-db" baseUrl="http://localhost:0">
            {children}
          </OptioProvider>
        </QueryClientProvider>
      );
    }
    const filter = or(
      and(eq('tag', 'demo'), eq('owner', 'kris')),
      and(eq('tag', 'prod'), eq('region', 'us')),
    );
    renderHook(
      () => useProcessListStream({ metadataFilter: filter }),
      { wrapper },
    );
    expect(MockEventSource.last).not.toBeNull();
    const url = new URL(MockEventSource.last!.url, 'http://localhost');
    const param = url.searchParams.get('metadataFilter');
    expect(param).not.toBeNull();
    const decoded = JSON.parse(param!);
    expect(decoded).toEqual({
      OR: [
        { AND: [{ tag: { eq: 'demo' } }, { owner: { eq: 'kris' } }] },
        { AND: [{ tag: { eq: 'prod' } }, { region: { eq: 'us' } }] },
      ],
    });
  });
```

Do not change any other test in this file (flat-shape tests must continue to pass).

- [ ] **Step 2: Commit**

```bash
git add packages/optio-ui/src/__tests__/useProcessListStream.test.tsx
git commit -m "test(ui): cover predicate-tree metadataFilter round-trip through SSE URL"
```

---

### Task 8: Add MongoDB integration smoke test

**Files:**
- Create: `packages/optio-api/src/__tests__/metadata-filter-mongo-integration.test.ts`

- [ ] **Step 1: Write the integration test**

Create the file with this exact content:

```ts
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { MongoClient, type Db } from 'mongodb';
import { metadataFilterToMongo } from '../metadata-filter-query.js';
import { and, or, eq, isIn } from 'optio-contracts';

// Connect to the running Docker MongoDB (per feedback_mongodb_docker.md).
// Do NOT use mongodb-memory-server.
const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = `optio-filter-it-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
const COLL = 'processes';

let client: MongoClient;
let db: Db;

beforeAll(async () => {
  client = new MongoClient(MONGO_URL);
  await client.connect();
  db = client.db(DB_NAME);
  await db.collection(COLL).insertMany([
    { _id: 'p1' as any, metadata: { tag: 'demo', owner: 'kris', region: 'us' } },
    { _id: 'p2' as any, metadata: { tag: 'demo', owner: 'sam',  region: 'eu' } },
    { _id: 'p3' as any, metadata: { tag: 'prod', owner: 'kris', region: 'us' } },
    { _id: 'p4' as any, metadata: { tag: 'prod', owner: 'sam',  region: 'jp' } },
    { _id: 'p5' as any, metadata: { tag: 'test', owner: 'kris', region: 'us' } },
  ]);
});

afterAll(async () => {
  await db.dropDatabase();
  await client.close();
});

async function matchedIds(query: Record<string, unknown>): Promise<string[]> {
  const docs = await db.collection(COLL).find(query).project({ _id: 1 }).toArray();
  return docs.map((d) => d._id as unknown as string).sort();
}

describe('metadataFilterToMongo integration with MongoDB', () => {
  it('(tag=demo AND owner=kris) OR (tag=prod AND region in [us,eu]) matches p1, p3', async () => {
    const filter = or(
      and(eq('tag', 'demo'), eq('owner', 'kris')),
      and(eq('tag', 'prod'), isIn('region', ['us', 'eu'])),
    );
    const mongo = metadataFilterToMongo(filter);
    expect(await matchedIds(mongo)).toEqual(['p1', 'p3']);
  });

  it('legacy flat shape { tag: "demo" } still matches p1, p2', async () => {
    const mongo = metadataFilterToMongo({ tag: 'demo' });
    expect(await matchedIds(mongo)).toEqual(['p1', 'p2']);
  });

  it('OR with three branches matches union', async () => {
    const filter = or(eq('owner', 'kris'), eq('region', 'jp'));
    const mongo = metadataFilterToMongo(filter);
    expect(await matchedIds(mongo)).toEqual(['p1', 'p3', 'p4', 'p5']);
  });
});
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-api/src/__tests__/metadata-filter-mongo-integration.test.ts
git commit -m "test(api): integration smoke for predicate-tree filter against Mongo"
```

---

### Task 9: End verification (typecheck + tests across affected packages)

This task runs LAST, after T1–T8 have landed. It performs all verification that was deliberately deferred from the parallel work.

**Files:** none (verification only)

- [ ] **Step 1: Build affected packages**

```bash
pnpm --filter optio-contracts build && \
pnpm --filter optio-api build && \
pnpm --filter optio-ui build
```

Expected: all three packages build with zero TypeScript errors. If any task left a type mismatch, fix it in the file owned by the responsible task and re-run.

- [ ] **Step 2: Run `optio-contracts` tests**

```bash
pnpm --filter optio-contracts test -- --run
```

Expected: all existing tests pass plus the new `ProcessMetadataFilterSchema (legacy flat shape, backwards-compatible)`, `ProcessMetadataPredicateSchema`, `FilterFieldPath`, `FilterLeafOps strict mode`, and `process-filter-helpers` describes pass.

- [ ] **Step 3: Run `optio-api` tests**

The integration smoke (T8) requires the Docker MongoDB at `mongodb://localhost:27017`. Verify it's reachable first; if not, start it before running tests.

```bash
docker ps --format '{{.Names}}' | grep -E 'mongo' || echo 'mongo container not running'
pnpm --filter optio-api test -- --run
```

Per `project_optio_api_ws_flake.md`, `pnpm -r` can flake the WS preflight tests under load; running with `--filter optio-api` directly (not `-r`) avoids that. If still flaky, set `OPTIO_SKIP_PREFLIGHT_TESTS=1`:

```bash
OPTIO_SKIP_PREFLIGHT_TESTS=1 pnpm --filter optio-api test -- --run
```

Expected: all existing tests pass, plus the new translator unit tests in `metadata-filter-query.test.ts` and the integration tests in `metadata-filter-mongo-integration.test.ts`.

- [ ] **Step 4: Run `optio-ui` tests**

```bash
pnpm --filter optio-ui test -- --run
```

Expected: all existing tests pass plus the new `URL-encodes a predicate-tree metadataFilter into the SSE URL` test.

- [ ] **Step 5: Type-check all affected packages with the bundled tsc**

Per `feedback_no_npx_tsc.md`, invoke `tsc` from `node_modules/.bin` directly. From each affected package directory:

```bash
(cd packages/optio-contracts && ./node_modules/.bin/tsc --noEmit) && \
(cd packages/optio-api && ./node_modules/.bin/tsc --noEmit) && \
(cd packages/optio-ui && ./node_modules/.bin/tsc --noEmit)
```

Expected: no errors. If a package's local `tsc` is not present, use the root one: `./node_modules/.bin/tsc --noEmit`.

- [ ] **Step 6: Final commit (only if a fixup was needed in this task)**

If steps 1–5 surfaced no issues, no commit is needed in this task. If a fixup was made to a file owned by a previous task, commit it now:

```bash
git add <fixup-files>
git commit -m "fix(filter): verification fixups for predicate-tree filter rollout"
```

---

## Self-review notes

- **Spec coverage:** Section 1 (Summary) → all tasks; Section 2 (Schema) → T1; Section 3 (Helpers) → T2 + T3; Section 4 (Translator) → T4; Section 5 (Hook) → no source change required (T7 is test-only, hook widens via type union); Section 6 (Backwards compatibility) → covered structurally by T1's union, T4's dispatcher; tests in T5/T6/T7 cover legacy backwards-compatibility; Section 7 (Testing) → T5 (contracts), T6 (translator unit), T7 (hook), T8 (integration), T9 (cross-package verification). Out-of-scope items in the spec are not implemented and not tested. No gaps.
- **Placeholders:** none.
- **Type consistency:** schema names, helper signatures, and translator entry-point match across all tasks (pinned in the "Parallel-execution contract" block at the top).
- **File ownership:** seven existing files modified, two new files created, each owned by exactly one task; T9 is verification-only and touches no source files. No two tasks edit the same file.
