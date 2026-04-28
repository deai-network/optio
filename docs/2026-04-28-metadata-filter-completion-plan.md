# Metadata Filter Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend typed `ProcessMetadataFilter` end-to-end coverage to the list REST and SSE endpoints and their `optio-ui` hooks, replacing the existing `metadata.*` query-prefix style with a single typed JSON shape that matches the resync write path.

**Architecture:** A single zod schema in `optio-contracts` (URL-encoded JSON → `ProcessMetadataFilter`) is reused both by the ts-rest contract for REST validation and by a shared helper in `optio-api` for SSE adapters. Handler and poller flatten the typed filter to `metadata.<key>` mongo dotted query. Adapter pre-checks reject any legacy `metadata.*` query keys with a 400 plus migration message. UI hooks accept an optional typed filter, JSON-encode it for transport, and include it in their cache/connection key so a change triggers refetch / SSE reconnect.

**Tech Stack:** TypeScript, zod, ts-rest, Express, Fastify, Next.js (App Router + Pages Router), MongoDB, Vitest, ioredis-mock, React.

**Spec:** `docs/2026-04-28-metadata-filter-completion-design.md` (base revision `fe5c2ae`).

---

## File Structure

**Files to create:**

- `packages/optio-api/src/metadata-filter-query.ts` — shared validation/conversion helpers used by adapters and handlers
- `packages/optio-api/src/__tests__/metadata-filter-query.test.ts` — unit tests for the helper

**Files to modify:**

- `packages/optio-contracts/src/schemas/process.ts` — add `MetadataFilterQueryParamSchema`
- `packages/optio-contracts/src/index.ts` — export new schema
- `packages/optio-contracts/src/contract.ts` — list endpoint query schema
- `packages/optio-contracts/src/__tests__/process-schema.test.ts` — extend with new schema tests
- `packages/optio-api/src/index.ts` — re-export helper
- `packages/optio-api/src/handlers.ts` — typed `metadataFilter` in `listProcesses`
- `packages/optio-api/src/stream-poller.ts` — `metadataFilter` in `createListPoller`
- `packages/optio-api/src/adapters/express.ts` — pre-check + SSE filter wiring
- `packages/optio-api/src/adapters/fastify.ts` — same
- `packages/optio-api/src/adapters/nextjs-app.ts` — same
- `packages/optio-api/src/adapters/nextjs-pages.ts` — same
- `packages/optio-api/src/__tests__/handlers.test.ts` — extend
- `packages/optio-api/src/__tests__/stream-poller.test.ts` — extend
- `packages/optio-api/src/adapters/__tests__/express.test.ts` — extend
- `packages/optio-api/src/adapters/__tests__/fastify.test.ts` — extend
- `packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts` — extend
- `packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts` — extend
- `packages/optio-ui/src/hooks/useProcessQueries.ts` — `useProcessList` accepts filter
- `packages/optio-ui/src/hooks/useProcessListStream.tsx` — accepts filter, includes in connection key
- `packages/optio-ui/src/__tests__/useProcessQueries.test.ts` (new)
- `packages/optio-ui/src/__tests__/useProcessListStream.test.tsx` (new)
- `packages/optio-api/README.md` — replace prefix examples with JSON form
- `packages/optio-ui/README.md` — `useProcessList` / `useProcessListStream` signatures
- `AGENTS.md` — list endpoint and UI hook descriptions

---

## Task 1: Add `MetadataFilterQueryParamSchema` to `optio-contracts`

**Files:**
- Modify: `packages/optio-contracts/src/schemas/process.ts`
- Modify: `packages/optio-contracts/src/index.ts`
- Modify: `packages/optio-contracts/src/__tests__/process-schema.test.ts`

- [ ] **Step 1: Write the failing tests**

Append to `packages/optio-contracts/src/__tests__/process-schema.test.ts`:

```typescript
import { MetadataFilterQueryParamSchema } from '../schemas/process.js';

describe('MetadataFilterQueryParamSchema', () => {
  it('parses a valid URL-decoded JSON object', () => {
    const parsed = MetadataFilterQueryParamSchema.parse('{"targetId":"abc"}');
    expect(parsed).toEqual({ targetId: 'abc' });
  });

  it('parses a multi-key object', () => {
    const parsed = MetadataFilterQueryParamSchema.parse('{"a":"x","b":"y"}');
    expect(parsed).toEqual({ a: 'x', b: 'y' });
  });

  it('returns undefined for undefined input', () => {
    const parsed = MetadataFilterQueryParamSchema.parse(undefined);
    expect(parsed).toBeUndefined();
  });

  it('rejects malformed JSON', () => {
    const result = MetadataFilterQueryParamSchema.safeParse('not json');
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0].message).toContain('valid JSON');
    }
  });

  it('rejects a JSON value that is not an object', () => {
    const result = MetadataFilterQueryParamSchema.safeParse('"foo"');
    expect(result.success).toBe(false);
  });

  it('rejects a JSON array', () => {
    const result = MetadataFilterQueryParamSchema.safeParse('[1,2,3]');
    expect(result.success).toBe(false);
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `cd packages/optio-contracts && pnpm test -- process-schema`
Expected: FAIL — `MetadataFilterQueryParamSchema` not exported.

- [ ] **Step 3: Add the schema**

Edit `packages/optio-contracts/src/schemas/process.ts` — append after the existing `ProcessMetadataFilterSchema` (current line 67):

```typescript
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
```

- [ ] **Step 4: Re-export from `packages/optio-contracts/src/index.ts`**

Open `packages/optio-contracts/src/index.ts`, find the existing export line for `ProcessMetadataFilterSchema`, and add `MetadataFilterQueryParamSchema` to the same export (or a new line if separate):

```typescript
export {
  ProcessSchema, ProcessStateSchema, LogEntrySchema,
  ProcessMetadataFilterSchema, MetadataFilterQueryParamSchema,
} from './schemas/process.js';
```

- [ ] **Step 5: Run the tests; confirm they pass**

Run: `cd packages/optio-contracts && pnpm test -- process-schema`
Expected: PASS — all six new cases.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-contracts/src/schemas/process.ts packages/optio-contracts/src/index.ts packages/optio-contracts/src/__tests__/process-schema.test.ts
git commit -m "feat(optio-contracts): MetadataFilterQueryParamSchema for URL-encoded JSON"
```

---

## Task 2: Switch list endpoint contract to typed `metadataFilter`, drop `.passthrough()`

**Files:**
- Modify: `packages/optio-contracts/src/contract.ts:21-26`

- [ ] **Step 1: Update the schema**

Replace lines 21-26 in `packages/optio-contracts/src/contract.ts`:

```typescript
    query: PaginationQuerySchema.extend({
      rootId: ObjectIdSchema.optional(),
      state: ProcessStateSchema.optional(),
      database: z.string().optional(),
      prefix: z.string().optional(),
      metadataFilter: MetadataFilterQueryParamSchema,
    }),
```

(Note the dropped `.passthrough()` at the end.)

- [ ] **Step 2: Update import in same file**

Adjust the import on line 4 (or add new line) so `MetadataFilterQueryParamSchema` is available:

```typescript
import {
  ProcessSchema, ProcessStateSchema, LogEntrySchema,
  ProcessMetadataFilterSchema, MetadataFilterQueryParamSchema,
} from './schemas/process.js';
```

- [ ] **Step 3: Build the contracts package**

Run: `cd packages/optio-contracts && pnpm build`
Expected: success (TypeScript clean).

- [ ] **Step 4: Commit**

```bash
git add packages/optio-contracts/src/contract.ts
git commit -m "feat(optio-contracts): list endpoint accepts typed metadataFilter, drop passthrough"
```

---

## Task 3: Shared helper module in `optio-api`

**Files:**
- Create: `packages/optio-api/src/metadata-filter-query.ts`
- Create: `packages/optio-api/src/__tests__/metadata-filter-query.test.ts`
- Modify: `packages/optio-api/src/index.ts`

- [ ] **Step 1: Write the failing test file**

Create `packages/optio-api/src/__tests__/metadata-filter-query.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import {
  parseMetadataFilterQuery,
  metadataFilterToMongo,
  detectLegacyMetadataParams,
} from '../metadata-filter-query.js';

describe('parseMetadataFilterQuery', () => {
  it('returns undefined value for undefined input', () => {
    expect(parseMetadataFilterQuery(undefined)).toEqual({ ok: true, value: undefined });
  });

  it('returns undefined value for null input', () => {
    expect(parseMetadataFilterQuery(null)).toEqual({ ok: true, value: undefined });
  });

  it('returns undefined value for empty string', () => {
    expect(parseMetadataFilterQuery('')).toEqual({ ok: true, value: undefined });
  });

  it('rejects non-string raw input', () => {
    const r = parseMetadataFilterQuery(123 as unknown);
    expect(r.ok).toBe(false);
  });

  it('rejects malformed JSON', () => {
    const r = parseMetadataFilterQuery('not json');
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain('JSON');
  });

  it('rejects JSON array', () => {
    const r = parseMetadataFilterQuery('[1,2,3]');
    expect(r.ok).toBe(false);
  });

  it('rejects JSON string scalar', () => {
    const r = parseMetadataFilterQuery('"foo"');
    expect(r.ok).toBe(false);
  });

  it('parses valid object', () => {
    const r = parseMetadataFilterQuery('{"targetId":"abc","kind":"x"}');
    expect(r).toEqual({ ok: true, value: { targetId: 'abc', kind: 'x' } });
  });
});

describe('metadataFilterToMongo', () => {
  it('returns empty object for undefined', () => {
    expect(metadataFilterToMongo(undefined)).toEqual({});
  });

  it('returns empty object for empty filter', () => {
    expect(metadataFilterToMongo({})).toEqual({});
  });

  it('prefixes single key with metadata.', () => {
    expect(metadataFilterToMongo({ targetId: 'abc' })).toEqual({
      'metadata.targetId': 'abc',
    });
  });

  it('prefixes multiple keys with metadata.', () => {
    expect(metadataFilterToMongo({ a: 1, b: 'x' })).toEqual({
      'metadata.a': 1,
      'metadata.b': 'x',
    });
  });
});

describe('detectLegacyMetadataParams', () => {
  it('returns empty array when none present', () => {
    expect(detectLegacyMetadataParams({ rootId: 'abc' })).toEqual([]);
  });

  it('returns matching legacy keys sorted', () => {
    expect(detectLegacyMetadataParams({
      'metadata.zeta': 'z',
      rootId: 'r',
      'metadata.alpha': 'a',
    })).toEqual(['metadata.alpha', 'metadata.zeta']);
  });
});
```

- [ ] **Step 2: Run; confirm failure**

Run: `cd packages/optio-api && pnpm test -- metadata-filter-query`
Expected: FAIL — module not found.

- [ ] **Step 3: Create the helper module**

Create `packages/optio-api/src/metadata-filter-query.ts`:

```typescript
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
```

- [ ] **Step 4: Re-export from package entry**

Open `packages/optio-api/src/index.ts` and add:

```typescript
export {
  parseMetadataFilterQuery,
  metadataFilterToMongo,
  detectLegacyMetadataParams,
} from './metadata-filter-query.js';
```

- [ ] **Step 5: Run the tests; confirm pass**

Run: `cd packages/optio-api && pnpm test -- metadata-filter-query`
Expected: PASS — all cases.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-api/src/metadata-filter-query.ts packages/optio-api/src/__tests__/metadata-filter-query.test.ts packages/optio-api/src/index.ts
git commit -m "feat(optio-api): shared metadataFilter helpers (parse, mongo, legacy detect)"
```

---

## Task 4: Switch REST `listProcesses` to typed `metadataFilter`

**Files:**
- Modify: `packages/optio-api/src/handlers.ts:27-63`
- Modify: `packages/optio-api/src/__tests__/handlers.test.ts`

- [ ] **Step 1: Add failing tests**

Append to `packages/optio-api/src/__tests__/handlers.test.ts`:

```typescript
describe('listProcesses metadataFilter', () => {
  beforeEach(async () => {
    await db.collection(`${PREFIX}_processes`).deleteMany({});
  });

  async function insert(metadata: Record<string, unknown>) {
    const oid = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: oid,
      processId: 'p',
      name: 'P',
      rootId: oid,
      parentId: null,
      depth: 0,
      order: 0,
      status: { state: 'idle' },
      progress: { percent: null },
      cancellable: true,
      log: [],
      metadata,
    });
    return oid.toString();
  }

  it('returns all processes when no filter', async () => {
    await insert({ project: 'x' });
    await insert({ project: 'y' });
    const r = await listProcesses(db, PREFIX, { limit: 50 });
    expect(r.items.length).toBe(2);
    expect(r.totalCount).toBe(2);
  });

  it('filters processes by metadata key', async () => {
    await insert({ project: 'x' });
    await insert({ project: 'y' });
    const r = await listProcesses(db, PREFIX, { limit: 50, metadataFilter: { project: 'x' } });
    expect(r.items.length).toBe(1);
    expect(r.items[0].metadata.project).toBe('x');
    expect(r.totalCount).toBe(1);
  });

  it('AND-matches multiple keys', async () => {
    await insert({ project: 'x', kind: 'a' });
    await insert({ project: 'x', kind: 'b' });
    await insert({ project: 'y', kind: 'a' });
    const r = await listProcesses(db, PREFIX, {
      limit: 50,
      metadataFilter: { project: 'x', kind: 'a' },
    });
    expect(r.items.length).toBe(1);
    expect(r.items[0].metadata).toEqual({ project: 'x', kind: 'a' });
  });

  it('returns empty when filter matches nothing', async () => {
    await insert({ project: 'x' });
    const r = await listProcesses(db, PREFIX, { limit: 50, metadataFilter: { project: 'nope' } });
    expect(r.items.length).toBe(0);
    expect(r.totalCount).toBe(0);
  });
});
```

- [ ] **Step 2: Run; confirm failure (compile or assertion)**

Run: `cd packages/optio-api && pnpm test -- handlers`
Expected: FAIL — `metadataFilter` is not a valid `ListQuery` field.

- [ ] **Step 3: Replace `ListQuery` and `listProcesses`**

Edit `packages/optio-api/src/handlers.ts`. Replace lines 27-63 with:

```typescript
// --- Query handlers ---

import { metadataFilterToMongo } from './metadata-filter-query.js';

export interface ListQuery {
  cursor?: string;
  limit: number;
  rootId?: string;
  state?: string;
  metadataFilter?: ProcessMetadataFilter;
}

export async function listProcesses(db: Db, prefix: string, query: ListQuery) {
  const { cursor, limit, rootId, state, metadataFilter } = query;
  const filter: Record<string, unknown> = {
    ...metadataFilterToMongo(metadataFilter),
  };

  if (rootId) filter.rootId = new ObjectId(rootId);
  if (state) filter['status.state'] = state;
  if (cursor) filter._id = { $gt: new ObjectId(cursor) };

  const [items, totalCount] = await Promise.all([
    col(db, prefix).find(filter).sort({ _id: 1 }).limit(limit + 1).toArray(),
    col(db, prefix).countDocuments(filter),
  ]);

  const hasNext = items.length > limit;
  if (hasNext) items.pop();

  return {
    items: items.map(toResponse),
    nextCursor: hasNext ? items[items.length - 1]._id.toString() : null,
    totalCount,
  };
}
```

The `import` line goes to the top of the file with the other imports. The existing `import type { ProcessMetadataFilter }` on line 4 already covers the type used in `ListQuery`.

- [ ] **Step 4: Run; confirm pass**

Run: `cd packages/optio-api && pnpm test -- handlers`
Expected: PASS — including the four new metadataFilter cases.

- [ ] **Step 5: Build, confirm clean**

Run: `cd packages/optio-api && pnpm build`
Expected: success.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-api/src/handlers.ts packages/optio-api/src/__tests__/handlers.test.ts
git commit -m "feat(optio-api): listProcesses accepts typed metadataFilter; drop metadata.* prefix loop"
```

---

## Task 5: Wire `metadataFilter` into `createListPoller`

**Files:**
- Modify: `packages/optio-api/src/stream-poller.ts:3-13, 15-73`
- Modify: `packages/optio-api/src/__tests__/stream-poller.test.ts`

- [ ] **Step 1: Add failing tests**

Append to `packages/optio-api/src/__tests__/stream-poller.test.ts`:

```typescript
import { createListPoller } from '../stream-poller.js';

describe('createListPoller metadataFilter', () => {
  beforeEach(async () => {
    await db.collection(`${PREFIX}_processes`).deleteMany({});
  });

  async function insertProc(metadata: Record<string, unknown>) {
    const oid = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: oid,
      processId: 'p',
      name: 'P',
      rootId: oid,
      parentId: null,
      depth: 0,
      order: 0,
      status: { state: 'idle' },
      progress: { percent: null },
      cancellable: true,
      log: [],
      metadata,
    });
    return oid;
  }

  it('emits all processes when no filter is set', async () => {
    await insertProc({ project: 'x' });
    await insertProc({ project: 'y' });

    const events: any[] = [];
    const poller = createListPoller({
      db, prefix: PREFIX,
      sendEvent: (e) => events.push(e),
      onError: () => {},
    });
    poller.start();
    await new Promise((r) => setTimeout(r, 1100));
    poller.stop();

    const update = events.find((e) => e.type === 'update');
    expect(update).toBeDefined();
    expect(update.processes.length).toBe(2);
  });

  it('emits only matching processes when filter is set', async () => {
    await insertProc({ project: 'x' });
    await insertProc({ project: 'y' });

    const events: any[] = [];
    const poller = createListPoller({
      db, prefix: PREFIX,
      sendEvent: (e) => events.push(e),
      onError: () => {},
      metadataFilter: { project: 'x' },
    });
    poller.start();
    await new Promise((r) => setTimeout(r, 1100));
    poller.stop();

    const update = events.find((e) => e.type === 'update');
    expect(update).toBeDefined();
    expect(update.processes.length).toBe(1);
    expect(update.processes[0].metadata.project).toBe('x');
  });

  it('AND-matches multiple keys', async () => {
    await insertProc({ project: 'x', kind: 'a' });
    await insertProc({ project: 'x', kind: 'b' });
    await insertProc({ project: 'y', kind: 'a' });

    const events: any[] = [];
    const poller = createListPoller({
      db, prefix: PREFIX,
      sendEvent: (e) => events.push(e),
      onError: () => {},
      metadataFilter: { project: 'x', kind: 'a' },
    });
    poller.start();
    await new Promise((r) => setTimeout(r, 1100));
    poller.stop();

    const update = events.find((e) => e.type === 'update');
    expect(update).toBeDefined();
    expect(update.processes.length).toBe(1);
  });
});
```

- [ ] **Step 2: Run; confirm failure**

Run: `cd packages/optio-api && pnpm test -- stream-poller`
Expected: FAIL — `metadataFilter` not in `StreamPollerOptions`.

- [ ] **Step 3: Update `stream-poller.ts`**

Add imports near the top:

```typescript
import type { ProcessMetadataFilter } from 'optio-contracts';
import { metadataFilterToMongo } from './metadata-filter-query.js';
```

Replace the existing `StreamPollerOptions` interface (lines 3-8) with:

```typescript
export interface StreamPollerOptions {
  db: Db;
  prefix: string;
  sendEvent: (data: unknown) => void;
  onError: () => void;
  metadataFilter?: ProcessMetadataFilter;
}
```

Replace the body of `createListPoller` (lines 15-73) so the find call uses the filter. Concretely, change:

```typescript
export function createListPoller(opts: StreamPollerOptions): ListPollerHandle {
  const { db, prefix, sendEvent, onError } = opts;
  const col = db.collection(`${prefix}_processes`);
  let interval: ReturnType<typeof setInterval> | null = null;
  let lastSnapshot = '';

  async function poll() {
    try {
      const allProcs = await col.find({}).sort({ depth: 1, order: 1, _id: 1 }).toArray();
```

to:

```typescript
export function createListPoller(opts: StreamPollerOptions): ListPollerHandle {
  const { db, prefix, sendEvent, onError, metadataFilter } = opts;
  const col = db.collection(`${prefix}_processes`);
  const filter = metadataFilterToMongo(metadataFilter);
  let interval: ReturnType<typeof setInterval> | null = null;
  let lastSnapshot = '';

  async function poll() {
    try {
      const allProcs = await col.find(filter).sort({ depth: 1, order: 1, _id: 1 }).toArray();
```

`createTreePoller` is unchanged.

- [ ] **Step 4: Run; confirm pass**

Run: `cd packages/optio-api && pnpm test -- stream-poller`
Expected: PASS — including new cases. Existing widgetData tree-poller tests still pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-api/src/stream-poller.ts packages/optio-api/src/__tests__/stream-poller.test.ts
git commit -m "feat(optio-api): createListPoller accepts metadataFilter"
```

---

## Task 6: Express adapter — SSE filter wiring + REST legacy pre-check

**Files:**
- Modify: `packages/optio-api/src/adapters/express.ts:142+ (list SSE)`, plus add a middleware for `GET /api/processes`
- Modify: `packages/optio-api/src/adapters/__tests__/express.test.ts`

- [ ] **Step 1: Add failing tests**

Append to `packages/optio-api/src/adapters/__tests__/express.test.ts`:

```typescript
describe('list metadataFilter (express)', () => {
  // Reuse existing test app/db setup. Insert two docs, request with various
  // metadataFilter combinations.

  it('REST list returns all when no filter', async () => {
    // ...insert two docs with metadata { project:'x' } and { project:'y' }
    const res = await request(app).get('/api/processes?database=...&prefix=...&limit=10');
    expect(res.status).toBe(200);
    expect(res.body.items.length).toBe(2);
  });

  it('REST list returns scoped result with valid metadataFilter', async () => {
    const filter = encodeURIComponent(JSON.stringify({ project: 'x' }));
    const res = await request(app).get(
      `/api/processes?database=...&prefix=...&limit=10&metadataFilter=${filter}`,
    );
    expect(res.status).toBe(200);
    expect(res.body.items.length).toBe(1);
    expect(res.body.items[0].metadata.project).toBe('x');
  });

  it('REST list returns 400 with explicit message for legacy metadata.* params', async () => {
    const res = await request(app).get(
      '/api/processes?database=...&prefix=...&limit=10&metadata.project=x',
    );
    expect(res.status).toBe(400);
    expect(res.body.message).toContain("Legacy 'metadata.*'");
    expect(res.body.message).toContain('metadata.project');
  });

  it('REST list returns 400 for malformed metadataFilter JSON', async () => {
    const res = await request(app).get(
      '/api/processes?database=...&prefix=...&limit=10&metadataFilter=not-json',
    );
    expect(res.status).toBe(400);
  });

  it('SSE list returns 400 for legacy metadata.* params', async () => {
    const res = await request(app).get(
      '/api/processes/stream?database=...&prefix=...&metadata.project=x',
    );
    expect(res.status).toBe(400);
    expect(res.body.message).toContain("Legacy 'metadata.*'");
  });

  it('SSE list returns 400 for malformed metadataFilter', async () => {
    const res = await request(app).get(
      '/api/processes/stream?database=...&prefix=...&metadataFilter=not-json',
    );
    expect(res.status).toBe(400);
  });
});
```

(Adapt `database`/`prefix` and the supertest setup to match existing patterns in the file. Use the existing `request(app)` and `MONGO_URL`/`PREFIX` infrastructure already present at the top.)

- [ ] **Step 2: Run; confirm failure**

Run: `cd packages/optio-api && pnpm test -- adapters/__tests__/express`
Expected: FAIL — handlers don't reject legacy keys; SSE doesn't 400.

- [ ] **Step 3: Add legacy pre-check middleware before ts-rest**

In `packages/optio-api/src/adapters/express.ts`, immediately *before* the `createExpressEndpoints(...)` call (around line 46) add:

```typescript
// Reject legacy metadata.* query params with an explicit migration message.
// Runs before ts-rest validation so users see the helpful error rather than
// a generic schema-validation 400.
import { detectLegacyMetadataParams } from '../metadata-filter-query.js';
// (move this import to the top of the file with the other imports)

app.get('/api/processes', (req: any, res: any, next: any) => {
  const legacyKeys = detectLegacyMetadataParams(req.query ?? {});
  if (legacyKeys.length > 0) {
    res.status(400).json({
      message: `Legacy 'metadata.*' query params are no longer supported. ` +
        `Use ?metadataFilter=<URL-encoded JSON>. Offending keys: ${legacyKeys.join(', ')}`,
    });
    return;
  }
  next();
});
```

Note: place the import at the top of the file with the other imports, not inline as shown.

- [ ] **Step 4: Update SSE list handler**

Edit `packages/optio-api/src/adapters/express.ts:142+` (the `app.get('/api/processes/stream', ...)` block). Add the import for `parseMetadataFilterQuery` at the top, then update the handler so it pre-checks legacy keys, parses metadataFilter, and passes to `createListPoller`. Replace the existing handler body with:

```typescript
app.get('/api/processes/stream', async (req: any, res: any) => {
  const legacyKeys = detectLegacyMetadataParams(req.query ?? {});
  if (legacyKeys.length > 0) {
    res.status(400).json({
      message: `Legacy 'metadata.*' query params are no longer supported. ` +
        `Use ?metadataFilter=<URL-encoded JSON>. Offending keys: ${legacyKeys.join(', ')}`,
    });
    return;
  }
  const parsed = parseMetadataFilterQuery(req.query?.metadataFilter);
  if (!parsed.ok) {
    res.status(400).json({ message: parsed.error });
    return;
  }

  // existing SSE setup (headers, db resolve, sendEvent, etc.) goes here, preserved verbatim
  // ...
  const poller = createListPoller({
    db, prefix,
    sendEvent: (data) => { /* existing */ },
    onError: () => { /* existing */ },
    metadataFilter: parsed.value,
  });
  poller.start();
  // existing close/cleanup wiring
});
```

(Preserve every line of the existing SSE setup — only add the two pre-check blocks at the top and the `metadataFilter` field in the `createListPoller` call.)

Add `parseMetadataFilterQuery` to the import list at the top:

```typescript
import { detectLegacyMetadataParams, parseMetadataFilterQuery } from '../metadata-filter-query.js';
```

- [ ] **Step 5: Run; confirm pass**

Run: `cd packages/optio-api && pnpm test -- adapters/__tests__/express`
Expected: PASS — including all six new cases.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-api/src/adapters/express.ts packages/optio-api/src/adapters/__tests__/express.test.ts
git commit -m "feat(optio-api/express): metadataFilter on REST+SSE list, 400 on legacy metadata.*"
```

---

## Task 7: Fastify adapter — same wiring

**Files:**
- Modify: `packages/optio-api/src/adapters/fastify.ts:373+, 467+`
- Modify: `packages/optio-api/src/adapters/__tests__/fastify.test.ts`

- [ ] **Step 1: Add failing tests**

Append to `packages/optio-api/src/adapters/__tests__/fastify.test.ts` the same six cases as in Task 6 step 1, adapting to the Fastify test setup (look at existing tests in the file for the inject/build pattern; replace `request(app).get(...)` with the equivalent fastify `inject` call).

- [ ] **Step 2: Run; confirm failure**

Run: `cd packages/optio-api && pnpm test -- adapters/__tests__/fastify`
Expected: FAIL.

- [ ] **Step 3: Add legacy pre-check + SSE filter wiring**

In `packages/optio-api/src/adapters/fastify.ts`:

1. Add to imports:

```typescript
import { detectLegacyMetadataParams, parseMetadataFilterQuery } from '../metadata-filter-query.js';
```

2. Before the `createFastifyEndpoints(apiContract.processes, ...)` call (or wherever the contract handlers are wired in this file — find the analogue to the express call), register a `preHandler` hook on the list route that runs before ts-rest:

```typescript
app.addHook('preHandler', async (request: any, reply: any) => {
  if (request.method === 'GET' && request.url?.startsWith('/api/processes')
      && !request.url.includes('/processes/stream')
      && !request.url.match(/\/processes\/[^/]+/)) {
    const legacyKeys = detectLegacyMetadataParams(request.query ?? {});
    if (legacyKeys.length > 0) {
      reply.code(400).send({
        message: `Legacy 'metadata.*' query params are no longer supported. ` +
          `Use ?metadataFilter=<URL-encoded JSON>. Offending keys: ${legacyKeys.join(', ')}`,
      });
    }
  }
});
```

(Adapt the URL match to whatever the Fastify list path looks like — see line 467 for stream and 373 for list. The intent is: only the bare list endpoint, not its sub-paths.)

3. Update the SSE list handler at line 467+. Replace the existing route body so it does the legacy check and parse before constructing the poller:

```typescript
app.get('/api/processes/stream', async (request: any, reply: any) => {
  const legacyKeys = detectLegacyMetadataParams(request.query ?? {});
  if (legacyKeys.length > 0) {
    reply.code(400).send({
      message: `Legacy 'metadata.*' query params are no longer supported. ` +
        `Use ?metadataFilter=<URL-encoded JSON>. Offending keys: ${legacyKeys.join(', ')}`,
    });
    return;
  }
  const parsed = parseMetadataFilterQuery((request.query as any)?.metadataFilter);
  if (!parsed.ok) {
    reply.code(400).send({ message: parsed.error });
    return;
  }
  // existing SSE setup preserved
  // ...
  const poller = createListPoller({
    db, prefix,
    sendEvent: (data) => { /* existing */ },
    onError: () => { /* existing */ },
    metadataFilter: parsed.value,
  });
  poller.start();
  // existing cleanup
});
```

- [ ] **Step 4: Run; confirm pass**

Run: `cd packages/optio-api && pnpm test -- adapters/__tests__/fastify`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-api/src/adapters/fastify.ts packages/optio-api/src/adapters/__tests__/fastify.test.ts
git commit -m "feat(optio-api/fastify): metadataFilter on REST+SSE list, 400 on legacy metadata.*"
```

---

## Task 8: Next.js App Router adapter — same wiring

**Files:**
- Modify: `packages/optio-api/src/adapters/nextjs-app.ts:46-48, 169+`
- Modify: `packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts`

- [ ] **Step 1: Add failing tests**

Append the equivalent six-case suite to `packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts`. Use the existing fetch-style helper in that file to call the route handler.

- [ ] **Step 2: Run; confirm failure**

Run: `cd packages/optio-api && pnpm test -- adapters/__tests__/nextjs-app`
Expected: FAIL.

- [ ] **Step 3: Add the pre-check before ts-rest at the REST entry point**

In `packages/optio-api/src/adapters/nextjs-app.ts`, find the App-Router HTTP entry function (the one wrapping `createNextHandler` or equivalent). Before delegating to ts-rest, parse `req.url` query, run `detectLegacyMetadataParams`, and short-circuit with a `Response` containing 400 + JSON body if any legacy keys are present (only when the path matches `/api/processes` exactly — not subpaths).

```typescript
import { detectLegacyMetadataParams, parseMetadataFilterQuery } from '../metadata-filter-query.js';

// inside the route handler dispatch, before delegating to ts-rest:
if (pathname === '/api/processes' && method === 'GET') {
  const queryObj = Object.fromEntries(new URL(req.url).searchParams.entries());
  const legacyKeys = detectLegacyMetadataParams(queryObj);
  if (legacyKeys.length > 0) {
    return new Response(
      JSON.stringify({
        message: `Legacy 'metadata.*' query params are no longer supported. ` +
          `Use ?metadataFilter=<URL-encoded JSON>. Offending keys: ${legacyKeys.join(', ')}`,
      }),
      { status: 400, headers: { 'Content-Type': 'application/json' } },
    );
  }
}
```

- [ ] **Step 4: Update SSE list handler at line 169+**

In the same file, find the `if (pathname === '/api/processes/stream')` block (line 170) and update it to:

```typescript
if (pathname === '/api/processes/stream') {
  const queryObj = Object.fromEntries(new URL(req.url).searchParams.entries());
  const legacyKeys = detectLegacyMetadataParams(queryObj);
  if (legacyKeys.length > 0) {
    return new Response(
      JSON.stringify({
        message: `Legacy 'metadata.*' query params are no longer supported. ` +
          `Use ?metadataFilter=<URL-encoded JSON>. Offending keys: ${legacyKeys.join(', ')}`,
      }),
      { status: 400, headers: { 'Content-Type': 'application/json' } },
    );
  }
  const parsed = parseMetadataFilterQuery(queryObj.metadataFilter);
  if (!parsed.ok) {
    return new Response(
      JSON.stringify({ message: parsed.error }),
      { status: 400, headers: { 'Content-Type': 'application/json' } },
    );
  }
  // existing ReadableStream / poller setup preserved verbatim,
  // adding metadataFilter: parsed.value to the createListPoller(...) call
  // ...
}
```

- [ ] **Step 5: Run; confirm pass**

Run: `cd packages/optio-api && pnpm test -- adapters/__tests__/nextjs-app`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-api/src/adapters/nextjs-app.ts packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts
git commit -m "feat(optio-api/nextjs-app): metadataFilter on REST+SSE list, 400 on legacy metadata.*"
```

---

## Task 9: Next.js Pages Router adapter — same wiring

**Files:**
- Modify: `packages/optio-api/src/adapters/nextjs-pages.ts:36-38, 147-148`
- Modify: `packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts`

- [ ] **Step 1: Add failing tests** — equivalent six cases adapted to the existing Pages-router test pattern.

- [ ] **Step 2: Run; confirm failure**

Run: `cd packages/optio-api && pnpm test -- adapters/__tests__/nextjs-pages`
Expected: FAIL.

- [ ] **Step 3: Add legacy pre-check before ts-rest at REST entry**

In `packages/optio-api/src/adapters/nextjs-pages.ts`, find the dispatch function for HTTP requests (similar shape to nextjs-app). Add the same legacy-key short-circuit using `req.query` (Pages router gives an already-parsed query object). Use:

```typescript
import { detectLegacyMetadataParams, parseMetadataFilterQuery } from '../metadata-filter-query.js';

// before delegating list to ts-rest:
if (req.url === '/api/processes' /* exact */ && req.method === 'GET') {
  const legacyKeys = detectLegacyMetadataParams(req.query ?? {});
  if (legacyKeys.length > 0) {
    res.status(400).json({
      message: `Legacy 'metadata.*' query params are no longer supported. ` +
        `Use ?metadataFilter=<URL-encoded JSON>. Offending keys: ${legacyKeys.join(', ')}`,
    });
    return;
  }
}
```

- [ ] **Step 4: Update SSE list block at line 147+**

Mirror the App-Router change: parse legacy + metadataFilter, return 400 on either failure, pass `metadataFilter: parsed.value` into `createListPoller`. Use the Pages-router `res.status(...).json(...)` API.

- [ ] **Step 5: Run; confirm pass**

Run: `cd packages/optio-api && pnpm test -- adapters/__tests__/nextjs-pages`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-api/src/adapters/nextjs-pages.ts packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts
git commit -m "feat(optio-api/nextjs-pages): metadataFilter on REST+SSE list, 400 on legacy metadata.*"
```

---

## Task 10: `useProcessList` hook accepts `metadataFilter`

**Files:**
- Modify: `packages/optio-ui/src/hooks/useProcessQueries.ts:1-17`
- Create: `packages/optio-ui/src/__tests__/useProcessQueries.test.ts`

- [ ] **Step 1: Add failing tests**

Create `packages/optio-ui/src/__tests__/useProcessQueries.test.ts` (model after the existing `OptioProvider.test.tsx` for the React Query/test-context shape; mock fetch and assert request URL):

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useProcessList } from '../hooks/useProcessQueries.js';
// import the test wrapper helper that supplies OptioContext + QueryClient,
// matching the pattern used in other UI tests
import { wrap } from './test-helpers';

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('useProcessList metadataFilter', () => {
  it('omits metadataFilter from the query string when not provided', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ items: [], totalCount: 0, nextCursor: null }), { status: 200 }),
    );

    const { result } = renderHook(() => useProcessList(), { wrapper: wrap });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    const calledUrl = (fetchMock.mock.calls[0][0] as URL).toString();
    expect(calledUrl).not.toContain('metadataFilter');
  });

  it('includes URL-encoded JSON metadataFilter when provided', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ items: [], totalCount: 0, nextCursor: null }), { status: 200 }),
    );

    const { result } = renderHook(
      () => useProcessList({ metadataFilter: { project: 'x' } }),
      { wrapper: wrap },
    );
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    const url = new URL((fetchMock.mock.calls[0][0] as URL).toString());
    expect(url.searchParams.get('metadataFilter')).toBe('{"project":"x"}');
  });

  it('uses a separate cache entry when filter changes (refetches)', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ items: [], totalCount: 0, nextCursor: null }), { status: 200 }),
    );

    const { rerender } = renderHook(
      ({ f }: { f?: Record<string, unknown> }) =>
        useProcessList({ metadataFilter: f as Record<string, string> | undefined }),
      { wrapper: wrap, initialProps: { f: { project: 'x' } } },
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    rerender({ f: { project: 'y' } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });
});
```

If `test-helpers` does not yet exist in `__tests__/`, create a small helper module that wraps `OptioProvider` + a fresh `QueryClient`. Look at the existing `OptioProvider.test.tsx` at `packages/optio-ui/src/__tests__/OptioProvider.test.tsx` for the established pattern, and reuse rather than duplicate.

- [ ] **Step 2: Run; confirm failure**

Run: `cd packages/optio-ui && pnpm test -- useProcessQueries`
Expected: FAIL — hook does not accept `metadataFilter`; URL has no such param.

- [ ] **Step 3: Update `useProcessList`**

Replace the body of `useProcessList` in `packages/optio-ui/src/hooks/useProcessQueries.ts` (lines 3-17) with:

```typescript
import type { ProcessMetadataFilter } from 'optio-contracts';

export function useProcessList(options?: {
  refetchInterval?: number | false;
  metadataFilter?: ProcessMetadataFilter;
}) {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();

  const filterKey = options?.metadataFilter
    ? JSON.stringify(options.metadataFilter)
    : '';

  const { data, isLoading } = api.processes.list.useQuery({
    queryKey: ['processes', database, prefix, filterKey],
    queryData: {
      query: {
        database, prefix, limit: 50,
        ...(filterKey ? { metadataFilter: filterKey } : {}),
      },
    },
    refetchInterval: options?.refetchInterval ?? 5000,
  });
  return {
    processes: data?.status === 200 ? data.body.items : [],
    totalCount: data?.status === 200 ? data.body.totalCount : 0,
    isLoading,
  };
}
```

- [ ] **Step 4: Run; confirm pass**

Run: `cd packages/optio-ui && pnpm test -- useProcessQueries`
Expected: PASS — all three new cases.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-ui/src/hooks/useProcessQueries.ts packages/optio-ui/src/__tests__/useProcessQueries.test.ts
git commit -m "feat(optio-ui): useProcessList accepts metadataFilter; cache key includes filter"
```

---

## Task 11: `useProcessListStream` hook accepts `metadataFilter`

**Files:**
- Modify: `packages/optio-ui/src/hooks/useProcessListStream.tsx:62+`
- Create: `packages/optio-ui/src/__tests__/useProcessListStream.test.tsx`

- [ ] **Step 1: Add failing tests**

Create `packages/optio-ui/src/__tests__/useProcessListStream.test.tsx`. Mock `EventSource` (the existing UI tests already do this; reuse the pattern). Tests:

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useProcessListStream } from '../hooks/useProcessListStream.js';
import { wrap } from './test-helpers';

class MockES {
  url: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(url: string) {
    MockES.last = this;
    this.url = url;
    MockES.instances.push(this);
  }
  close() { this.closed = true; }
  static last: MockES | null = null;
  static instances: MockES[] = [];
  static reset() { MockES.last = null; MockES.instances = []; }
}

beforeEach(() => {
  MockES.reset();
  (globalThis as any).EventSource = MockES;
});

describe('useProcessListStream metadataFilter', () => {
  it('opens SSE without metadataFilter when no filter provided', () => {
    renderHook(() => useProcessListStream(), { wrapper: wrap });
    expect(MockES.last).not.toBeNull();
    expect(MockES.last!.url).not.toContain('metadataFilter');
  });

  it('URL-encodes JSON metadataFilter into the SSE URL', () => {
    renderHook(
      () => useProcessListStream({ metadataFilter: { project: 'x' } }),
      { wrapper: wrap },
    );
    expect(MockES.last).not.toBeNull();
    const url = new URL(MockES.last!.url, 'http://x');
    expect(url.searchParams.get('metadataFilter')).toBe('{"project":"x"}');
  });

  it('reconnects (closes prev, opens new) when filter changes', () => {
    const { rerender } = renderHook(
      ({ f }: { f?: Record<string, string> }) => useProcessListStream({ metadataFilter: f }),
      { wrapper: wrap, initialProps: { f: { project: 'x' } } },
    );
    const first = MockES.last!;
    rerender({ f: { project: 'y' } });
    expect(first.closed).toBe(true);
    expect(MockES.instances.length).toBe(2);
    const second = MockES.instances[1];
    const url = new URL(second.url, 'http://x');
    expect(url.searchParams.get('metadataFilter')).toBe('{"project":"y"}');
  });
});
```

- [ ] **Step 2: Run; confirm failure**

Run: `cd packages/optio-ui && pnpm test -- useProcessListStream`
Expected: FAIL.

- [ ] **Step 3: Update `useProcessListStream`**

Open `packages/optio-ui/src/hooks/useProcessListStream.tsx`. Update the function signature, derive a `filterKey`, append to URL when present, and include `filterKey` in the dependency array of the SSE-opening `useEffect`:

```typescript
import type { ProcessMetadataFilter } from 'optio-contracts';

export function useProcessListStream(
  options?: { metadataFilter?: ProcessMetadataFilter },
): ProcessListStreamState {
  // ...existing prefix/database/baseUrl resolution...

  const filterKey = options?.metadataFilter
    ? JSON.stringify(options.metadataFilter)
    : '';

  useEffect(() => {
    const url = `${baseUrl}/api/processes/stream${
      filterKey ? `?metadataFilter=${encodeURIComponent(filterKey)}` : ''
    }`;
    const es = new EventSource(url);
    // ...existing onmessage / onerror / cleanup wiring...
    return () => { es.close(); };
  }, [baseUrl, filterKey /* plus the existing deps such as database, prefix */]);

  // ...rest unchanged
}
```

(Preserve every line of the existing logic — the only changes are: signature, `filterKey` computation, URL construction, and the dependency-array entry.)

- [ ] **Step 4: Run; confirm pass**

Run: `cd packages/optio-ui && pnpm test -- useProcessListStream`
Expected: PASS — all three new cases.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-ui/src/hooks/useProcessListStream.tsx packages/optio-ui/src/__tests__/useProcessListStream.test.tsx
git commit -m "feat(optio-ui): useProcessListStream accepts metadataFilter; reconnect on change"
```

---

## Task 12: Documentation updates

**Files:**
- Modify: `packages/optio-api/README.md`
- Modify: `packages/optio-ui/README.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Update `packages/optio-api/README.md`**

Find the section describing list endpoint query parameters (search the file for `metadata.targetId` or similar). Replace any prefix-style example with the JSON form. Add a "Breaking changes" note (or extend an existing one) listing:

> The `?metadata.<key>=<value>` form is no longer supported. Use `?metadataFilter=<URL-encoded JSON>`. Requests using the legacy form return 400 with an explicit message naming the offending keys.

Also document the SSE list endpoint's optional `metadataFilter` query.

- [ ] **Step 2: Update `packages/optio-ui/README.md`**

Update the `useProcessList` and `useProcessListStream` entries to show:

```typescript
useProcessList({ metadataFilter: { project: 'x' } });
useProcessListStream({ metadataFilter: { project: 'x' } });
```

- [ ] **Step 3: Update `AGENTS.md`**

Find the list-endpoint and UI hook descriptions; switch examples to `metadataFilter` JSON form. Note the breaking change in the changelog/section that tracks recent API surface evolution if such a section exists.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-api/README.md packages/optio-ui/README.md AGENTS.md
git commit -m "docs: list endpoint + UI hooks now use metadataFilter JSON; flag prefix removal"
```

---

## Task 13: Final verification

- [ ] **Step 1: Run full type-check from repo root**

Run: `pnpm -r exec tsc --noEmit` (or the equivalent project-wide build script — see `package.json` / `Makefile` for the canonical command).
Expected: clean.

- [ ] **Step 2: Run full test suite**

Run: `pnpm -r test`
Expected: all packages pass.

- [ ] **Step 3: Manual REST sanity check**

Start the dev/demo server and:

1. `curl 'http://localhost:PORT/api/processes?database=...&prefix=...&limit=10&metadata.foo=bar'` → 400 with explicit message.
2. `curl 'http://localhost:PORT/api/processes?database=...&prefix=...&limit=10&metadataFilter=%7B%22project%22%3A%22x%22%7D'` → 200 with scoped result.
3. `curl 'http://localhost:PORT/api/processes/stream?database=...&prefix=...&metadata.foo=bar'` → 400.

If any of the above fails, return to the relevant task.

- [ ] **Step 4: No commit needed.**

---

## Self-Review

### Spec coverage

| Spec section | Implementing task(s) |
|---|---|
| Goal — typed end-to-end on list REST + SSE | T1–T11 |
| Decisions row 1 (typed JSON) | T1, T2, T4 |
| Decisions row 2 (hard switch + 400) | T2 (drop passthrough), T6–T9 (adapter pre-check) |
| Decisions row 3 (tree out of scope) | nothing changes — `createTreePoller` and `getProcessTree` are not touched in any task |
| Decisions row 4 (reconnect on filter change) | T11 (`filterKey` in `useEffect` deps) |
| Decisions row 5 (shared validation helper) | T3 |
| Component 1 (optio-contracts) | T1, T2 |
| Component 2 (shared helper) | T3 |
| Component 3 (REST handler) | T4 |
| Component 4 (poller) | T5 |
| Component 5 (adapters) | T6, T7, T8, T9 |
| Component 6 (`useProcessList`) | T10 |
| Component 7 (`useProcessListStream`) | T11 |
| Error handling table — all rows | T6–T9 (pre-check + parse), T2 (schema 400), T3 (helper 400 messages) |
| Testing — all sections | T1, T3, T4, T5, T6, T7, T8, T9, T10, T11 |
| Breaking-changes documentation | T12 |

No gaps.

### Placeholder scan

Scanned for "TBD", "TODO", "fill in", "implement later", "similar to", "appropriate", "edge cases" without code: none in code-bearing steps. The doc tasks (T12) intentionally describe README edits in prose because the existing READMEs have variable structure across the repo; the steps name the file, what to change, and the message text — sufficient to act on without ambiguity.

### Type / API consistency

- `ProcessMetadataFilter` import path: `optio-contracts` everywhere new code references it. Existing `optio-api/src/types.ts` already re-exports it; both are used (handler imports from `./types.js`, helper from `optio-contracts`) — both refer to the same type.
- Helper API: `parseMetadataFilterQuery` returns `{ ok: true, value: ProcessMetadataFilter | undefined } | { ok: false; error: string }`. Used identically in T6, T7, T8, T9.
- `metadataFilterToMongo` signature stable across T4 (handler) and T5 (poller).
- `detectLegacyMetadataParams` returns sorted `string[]` (assertion in T3), consumed identically in T6–T9.
- `MetadataFilterQueryParamSchema` exported from `optio-contracts` (T1), imported by helper (T3) and contract (T2).
- UI `metadataFilter` field name consistent across hook signatures (T10, T11) and adapter handlers (T6–T9).
