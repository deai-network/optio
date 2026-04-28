# Metadata Filter Completion — Design

**Base revision:** `fe5c2ae168d4ba0dbe7c6b56ec60b70ecc0f153d` on branch `main` (as of 2026-04-28T10:30:05Z)

## Goal

Extend typed `ProcessMetadataFilter` end-to-end coverage to the two list read paths — REST `GET /api/processes` and SSE `GET /api/processes/stream` — and their corresponding UI hooks. Standardize on a single filter shape across the stack so list reads match the resync write path already shipped.

Tree-shaped endpoints (`getProcessTree`, `getProcessTreeLog`, `createTreePoller`) are out of scope: a tree is rooted at one process and process metadata inherits from parent at spawn (`executor.execute_child`, `store.create_child_process`), so subtree metadata is structurally uniform and filtering inside a subtree adds no value today.

## Background

Existing coverage of metadata filtering:

- **optio-core**: `list_processes(metadata=...)`, `Optio.resync(metadata_filter=...)`, `_handle_resync` scoped clean+sync, `Scheduler.sync_schedules`, `Executor.register_tasks`.
- **optio-api**: `resyncProcesses` + `publishResync` (typed `ProcessMetadataFilter` body); list REST handler accepts `metadata.*` prefixed query params via `.passthrough()`.
- **optio-ui**: `useProcessActions.resync/resyncClean(metadataFilter)`.

Gaps:

- **List REST handler** uses an untyped `metadata.*` prefix convention — different shape from resync, no end-to-end typing.
- **List SSE poller** (`createListPoller`) accepts no filter; streams every process.
- **Stream adapter handlers** in all four adapters (`express`, `fastify`, `nextjs-app`, `nextjs-pages`) have no filter parsing.
- **`useProcessList`** hook accepts no filter argument.
- **`useProcessListStream`** hook accepts no filter argument.

## Decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | Standardize on **typed JSON** filter shape (resync's style), not the prefix style. | Preserves `ProcessMetadataFilter` typing through whole stack. |
| 2 | **Hard switch** + helpful 400 on legacy `metadata.*` prefix. | Loud failure for any forgotten consumer; no dual-mode complexity. |
| 3 | **Tree endpoints unchanged** — out of scope. | Subtree metadata is structurally uniform via spawn-inheritance; no use case. |
| 4 | **Reconnect SSE on filter change** (cache key flip). | SSE re-open is cheap; avoids stateful protocol. |
| 5 | **Shared validation helper** for stream (no contract). REST relies on ts-rest schema. Both reference same zod schema. | One source of validation truth; no duplicated parsing logic across four adapters. |

## Architecture

```
                            optio-contracts
                          ┌──────────────────────┐
                          │ ProcessMetadataFilter│
                          │   Schema (zod)       │   ← single source of truth
                          │ MetadataFilterQuery  │
                          │   ParamSchema        │
                          └──────────┬───────────┘
                                     │
   ┌─────────────────────────────────┼─────────────────────────────────┐
   ▼                                 ▼                                 ▼
optio-api                         optio-api                         optio-ui
listProcesses (REST)              stream adapters + poller          useProcessList,
  contract validates JSON           parseMetadataFilterQuery()        useProcessListStream
  → typed filter                    + detectLegacyMetadataParams()      pass typed filter,
  → metadataFilterToMongo()         → typed filter                      include in cache key
                                    → poller find()                     URL-encode JSON
   │                                  │                                  │
   └──────────► same shape: { metadata.<k>: v, ... } mongo filter ──────┘
```

Client-side flow: caller passes a typed `ProcessMetadataFilter` object → UI hook URL-encodes JSON → contract or shared helper validates and re-types → handler/poller flatten to `metadata.<key>` dotted mongo filter. Resync stays as-is; this work matches its style for parity.

## Component changes

### 1. optio-contracts

`packages/optio-contracts/src/schemas/process.ts` — add (alongside existing `ProcessMetadataFilterSchema`):

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

Export both schemas from `packages/optio-contracts/src/index.ts`.

`packages/optio-contracts/src/contract.ts` — update `list` endpoint:

```typescript
list: {
  method: 'GET',
  path: '/processes',
  query: PaginationQuerySchema.extend({
    rootId: ObjectIdSchema.optional(),
    state: ProcessStateSchema.optional(),
    database: z.string().optional(),
    prefix: z.string().optional(),
    metadataFilter: MetadataFilterQueryParamSchema,
  }),                                              // .passthrough() removed
  ...
},
```

Dropping `.passthrough()` reverts the schema to zod's default `strip` mode: unknown keys are silently dropped during validation. This is fine — legacy-key **detection and rejection** does not live in the schema. It lives in the adapter pre-check (`detectLegacyMetadataParams`, see component 5), which runs *before* ts-rest hands the query to the schema. The schema simply no longer leaks unknown keys downstream.

### 2. optio-api — shared helper

New file `packages/optio-api/src/metadata-filter-query.ts`:

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
    return { ok: false, error: result.error.issues[0]?.message ?? 'Invalid metadataFilter' };
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

Export from `packages/optio-api/src/index.ts`.

### 3. optio-api — REST list handler

`packages/optio-api/src/handlers.ts`:

```typescript
import { metadataFilterToMongo } from './metadata-filter-query.js';
import type { ProcessMetadataFilter } from './types.js';

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

Removed: index signature on `ListQuery`, in-handler `metadata.*` extraction loop.

### 4. optio-api — list stream poller

`packages/optio-api/src/stream-poller.ts`:

```typescript
import type { ProcessMetadataFilter } from 'optio-contracts';
import { metadataFilterToMongo } from './metadata-filter-query.js';

export interface StreamPollerOptions {
  db: Db;
  prefix: string;
  sendEvent: (data: unknown) => void;
  onError: () => void;
  metadataFilter?: ProcessMetadataFilter;
}

export function createListPoller(opts: StreamPollerOptions): ListPollerHandle {
  const { db, prefix, sendEvent, onError, metadataFilter } = opts;
  const col = db.collection(`${prefix}_processes`);
  const filter = metadataFilterToMongo(metadataFilter);
  // captured at construction; reconnect on filter change creates a new poller

  let interval: ReturnType<typeof setInterval> | null = null;
  let lastSnapshot = '';

  async function poll() {
    try {
      const allProcs = await col.find(filter)
        .sort({ depth: 1, order: 1, _id: 1 })
        .toArray();
      // ...rest unchanged
    }
    ...
  }
  ...
}
```

`createTreePoller` unchanged.

### 5. optio-api — adapter SSE list handlers

Each of `express.ts`, `fastify.ts`, `nextjs-app.ts`, `nextjs-pages.ts` updates the existing list-stream route to:

1. Pre-check raw query with `detectLegacyMetadataParams` → 400 with explicit migration message if any legacy keys are present.
2. Parse `metadataFilter` query via `parseMetadataFilterQuery` → 400 with parse error on failure.
3. Construct `createListPoller` with the typed filter.

Pattern (Express shown; others mirror):

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
  // ...existing SSE headers...
  const poller = createListPoller({
    db, prefix, sendEvent, onError,
    metadataFilter: parsed.value,
  });
  poller.start();
  ...
});
```

REST list adapter wrappers also call `detectLegacyMetadataParams` before delegating to the ts-rest handler so the explicit message is returned (otherwise users get a generic schema-validation 400).

### 6. optio-ui — `useProcessList`

`packages/optio-ui/src/hooks/useProcessQueries.ts`:

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

Cache key includes serialized filter so changing filter triggers a fresh fetch / separate cache entry.

### 7. optio-ui — `useProcessListStream`

`packages/optio-ui/src/hooks/useProcessListStream.tsx`:

```typescript
import type { ProcessMetadataFilter } from 'optio-contracts';

export function useProcessListStream(
  options?: { metadataFilter?: ProcessMetadataFilter },
): ProcessListStreamState {
  const filterKey = options?.metadataFilter
    ? JSON.stringify(options.metadataFilter)
    : '';
  // filterKey is included in the SSE-open useEffect's dependency list so a
  // change closes the current EventSource and opens a new one.

  const url = `${baseUrl}/api/processes/stream${
    filterKey ? `?metadataFilter=${encodeURIComponent(filterKey)}` : ''
  }`;
  // ...
}
```

## Error handling

| Condition | Response |
|---|---|
| REST list with legacy `?metadata.foo=bar` | 400 with explicit migration message (adapter pre-check, runs before ts-rest) |
| REST list with malformed `?metadataFilter=...` | 400 from ts-rest schema validation (zod transform reports parse error) |
| SSE list with legacy `?metadata.foo=bar` | 400 with explicit migration message |
| SSE list with malformed `?metadataFilter=...` | 400 with parse-error message |
| List with valid empty filter `{}` | Treated as no filter (consistent with `metadataFilterToMongo`) |
| Filter value type other than string | Allowed (schema is `z.record(z.unknown())`); mongo equality match still works |

## Testing

### Unit — `optio-api/src/__tests__/metadata-filter-query.test.ts` (new)

- `parseMetadataFilterQuery`: undefined / null / empty / non-string / invalid-JSON / non-object-JSON / valid object.
- `metadataFilterToMongo`: undefined / `{}` / single key / multi-key (verify dotted output).
- `detectLegacyMetadataParams`: no legacy keys / one / multiple (sorted).

### Unit — `optio-api/src/__tests__/stream-poller.test.ts` (extend)

- `createListPoller` no filter → emits all processes.
- With single-key filter → emits matching only.
- With multi-key filter → AND-match.

### Unit — `optio-api/src/__tests__/handlers.test.ts` (extend)

- `listProcesses` with typed `metadataFilter` → mongo filter contains `metadata.*` dotted keys.
- `listProcesses` without filter → no `metadata.*` keys in mongo filter.

### Adapter — `optio-api/src/adapters/__tests__/{express,fastify,nextjs-app,nextjs-pages}.test.ts` (extend)

For each adapter:

- `GET /api/processes/stream` with bad-JSON `metadataFilter` → 400 with parse error.
- `GET /api/processes/stream` with valid filter → poller scoped, events scoped.
- `GET /api/processes?metadata.foo=bar` → 400 with explicit legacy message.
- `GET /api/processes?metadataFilter=<valid JSON>` → returns scoped list.

### UI — `optio-ui/src/hooks/__tests__/useProcessListStream.test.tsx` (new or extend)

- Initial render no filter → SSE URL has no `metadataFilter` query.
- Initial render with filter → SSE URL has correctly URL-encoded `metadataFilter`.
- Filter prop change → previous EventSource closed, new one opened with new query.

### UI — `optio-ui/src/hooks/__tests__/useProcessQueries.test.tsx` (new or extend)

- `useProcessList({ metadataFilter: {x:1} })` → query string includes `metadataFilter`.
- queryKey includes filter → switching filter triggers refetch / separate cache entry.

## Breaking changes

| Layer | Before | After |
|---|---|---|
| REST list | `GET /api/processes?metadata.targetId=abc` | `GET /api/processes?metadataFilter=%7B%22targetId%22%3A%22abc%22%7D` |
| REST list | unknown query keys silently passed through | unknown keys → 400; `metadata.*` keys → explicit 400 with migration message |
| SSE list | no filter accepted | optional `metadataFilter` query (URL-encoded JSON) |
| `useProcessList` | no `metadataFilter` arg | optional `metadataFilter: ProcessMetadataFilter` |
| `useProcessListStream` | no args | optional `{ metadataFilter }` argument |

Documentation updates:

- `packages/optio-api/README.md`: replace `metadata.*` examples with `metadataFilter` JSON form; note the breaking change.
- `packages/optio-ui/README.md`: update `useProcessList` and `useProcessListStream` signatures.
- `AGENTS.md`: update list endpoint and UI hook descriptions.

## Out of scope

- Tree REST and SSE endpoints (`getProcessTree`, `getProcessTreeLog`, `createTreePoller`).
- Operators beyond exact match (`$in`, `$exists`, `$regex`, etc.).
- A `metadata=` argument on `ProcessContext.run_child` (children currently inherit parent metadata; no API to override).
- Patching `~/private/guy-montag` to use the new filter on `useProcessListStream`/`useProcessList`. Tracked separately in the existing patch-list memory; the patch will simplify after this work lands by removing client-side `p?.metadata?.targetId === sourceId` filtering.
