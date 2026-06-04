# filtrum — Extensible, Backend-Agnostic Filter Language

This spec was written against the following baseline:

**Base revision:** `5f24a951a7375e3a86792f9721af4e0fe1a5d64c` on branch `main` (as of 2026-06-04T13:17:23Z)

## Status

This is **P1** of the excavator "entity-data live search" effort (umbrella spec:
`~/deai/excavator/docs/superpowers/specs/2026-06-02-entity-data-live-search-architecture.md`).
P1 lives in the **optio monorepo** (the current home of the filter language and a
consumer). Excavator's *use* of filtrum (the `search` op wired to
`buildTextSearchFilter`, the api search path) is **P5**, not here.

## Goal

Extract optio's metadata-filter language into a standalone, reusable filter
language with two layers:

- **`filtrum-core`** — the language: a Zod schema, predicate builders, and a
  backend-agnostic compiler driven by a pluggable `Dialect<T>`, plus the
  extension points for custom operators. No backend, no Mongo.
- **`filtrum-mongo`** — the Mongo binding: a `mongoDialect`, the built-in Mongo
  operator handlers, and a pre-wired `createMongoFilterTranslator` convenience.

Then migrate optio onto filtrum with **full behavioral back-compat**. The design
must support adding custom operators (e.g. a future `search` op) and other
backends (SQL, in-memory) **without forking core**.

## Background (what exists today)

- Schemas — `packages/optio-contracts/src/schemas/process.ts`: `FilterScalar`,
  `FilterFieldPath`, `FilterLeafOps` (`eq/ne/in/nin/exists/gt/gte/lt/lte`),
  `ProcessMetadataPredicateSchema` (`AND/OR/NOT` + `{field: LeafOps}`),
  `ProcessMetadataFilterLegacySchema` (flat `{field: scalar}`),
  `ProcessMetadataFilterSchema` (union), `MetadataFilterQueryParamSchema`
  (JSON-string → parse → filter).
- Translator — `packages/optio-api/src/metadata-filter-query.ts`:
  `metadataFilterToMongo` / `predicateToMongo` / `legacyToMongo`, **hardcoding**
  the `metadata.` field prefix and `eq→$eq` etc.
- Builders — `packages/optio-contracts/src/process-filter-helpers.ts`:
  `and/or/not/eq/ne/isIn/notIn/exists/gt/gte/lt/lte`.

## Architecture

### `filtrum-core` (language + framework, no backend)

**Schema (Zod), backend-agnostic.** The same predicate JSON is valid for any
backend.
- `FilterScalar`, `FilterFieldPath` (generic dotted-path; **no** `metadata`
  baked in), base `FilterLeafOps` (the structured ops).
- `makeFilterSchema(options) => { PredicateSchema, FilterSchema, QueryParamSchema }`
  where:
  ```ts
  interface MakeFilterSchemaOptions {
    extraLeafOps?: Record<string, z.ZodTypeAny> // merged into FilterLeafOps via .extend
    allowLegacyFlat?: boolean                    // include the flat {field:scalar} union branch
    fieldPath?: z.ZodTypeAny                     // override the default field-path validator
  }
  ```
  `QueryParamSchema` is the `string → JSON.parse → FilterSchema` parser (today's
  `MetadataFilterQueryParamSchema` generalized).

**Builders, backend-agnostic** (build the predicate tree only): `and/or/not`,
`eq/ne/isIn/notIn/exists/gt/gte/lt/lte`, plus a generic
`leaf(field, op, value)` so consumers can build custom-op predicates without
core knowing the op.

**Compiler, generic over a result type `T`:**
```ts
interface Dialect<T> {
  and(parts: T[]): T
  or(parts: T[]): T
  not(part: T): T
  matchAll(): T                                  // empty AND/OR collapse
  op(name: string, field: string, value: unknown, ctx: CompileCtx): T
}
interface CompileCtx { fieldPrefix: string }
function compile<T>(filter: unknown, dialect: Dialect<T>, ctx?: Partial<CompileCtx>): T
```
`compile` walks `AND/OR/NOT` + leaves, calling `dialect.op(...)` per `(op,value)`
(multiple ops on a field → `dialect.and([...])`). **Legacy-flat desugars here**
to `and(op('eq', field, scalar))` — backend-agnostic, dialect handles the rest.
Unknown op names are a compile error unless the dialect's `op` handles them.

Core knows nothing about `$and`, `_qt`, SQL, etc.

### `filtrum-mongo` (the Mongo binding)

- `mongoDialect`: `and→{$and}`, `or→{$or}`, `not→{$nor:[...]}`, `matchAll→{}`,
  and a built-in **op-handler table** for the structured ops emitting
  `{ [ctx.fieldPrefix + field]: { $eq|$ne|$in|$nin|$exists|$gt|$gte|$lt|$lte: value } }`.
- Custom ops: a consumer extends the dialect's op table; a custom handler returns
  its **whole** fragment (so `search`, later, can target `_qt.<field>.*` and
  ignore `fieldPrefix`).
- Pre-wired convenience:
  ```ts
  function createMongoFilterTranslator(options?: {
    fieldPrefix?: string
    ops?: Record<string, MongoOpHandler> // merged over built-ins
  }): (filter: unknown) => Filter<Document>
  ```
  Wires `compile` + `mongoDialect` so the common case never touches the
  abstraction. `MongoOpHandler = (field: string, value: unknown, ctx: CompileCtx) => Filter<Document>`.

`filtrum-mongo` imports `mongodb` **types only** (`Filter<Document>`) — it emits
plain objects, so it has **no runtime dependency** on the driver.

## Packaging

Two packages in the optio monorepo (pre-shaped so a future standalone `filtrum`
monorepo is a lift-and-shift — they would become `packages/core` / `packages/mongo`):

- `packages/filtrum-core` — npm name `filtrum-core`. Runtime dep: `zod`. Build
  `tsc`, test `vitest` (mirror `optio-contracts`'s `package.json`/`tsconfig`).
- `packages/filtrum-mongo` — npm name `filtrum-mongo`. Deps: `filtrum-core`
  (`workspace:*`); `mongodb` as an **optional peerDependency** (`>=5`,
  `peerDependenciesMeta.mongodb.optional`) + devDependency for types — mirroring
  `@quaesitor-textus/mongo`. Build `tsc`, test `vitest`.

Each package gets its own `AGENTS.md` (per the monorepo rule). Add both to the
workspace and to the root `AGENTS.md` package table.

**Publishing:** excavator (P5) consumes filtrum from the registry (as it does
other optio packages), so both packages must be published. Publish during P1 (or
gate P5 on it); flagged as a release step, version `0.1.0`.

## optio migration (full back-compat)

Behavior must not change for optio. Concretely:

- **`optio-contracts`**: re-create today's exports by *configuring* filtrum and
  re-exporting, so downstream imports keep working:
  - `ProcessMetadataPredicateSchema` / `ProcessMetadataFilterSchema` /
    `MetadataFilterQueryParamSchema` ← `makeFilterSchema({ allowLegacyFlat: true,
    fieldPath: <metadata-dotted-path validator> })` (preserving the current
    `FilterFieldPath` semantics for `metadata.*`).
  - `FilterScalar`, `FilterLeafOps`, and the `ProcessMetadata*` **types** stay
    exported (re-exported from filtrum-core).
  - The builder helpers in `process-filter-helpers.ts` re-export filtrum-core's
    builders (same names/signatures: `and/or/not/eq/ne/isIn/notIn/exists/gt/...`).
- **`optio-api`**: `metadata-filter-query.ts` becomes a thin wrapper:
  `metadataFilterToMongo = createMongoFilterTranslator({ fieldPrefix: 'metadata.' })`
  composed with the existing legacy-vs-predicate dispatch (now handled inside
  filtrum's `allowLegacyFlat`). `parseMetadataFilterQuery`,
  `isLegacyFlatFilter`, `detectLegacyMetadataParams`, `formatLegacyMetadataMessage`
  keep their signatures (re-implemented over filtrum where they touch it).
- **No wire/behavior change**: the `metadata.` prefix, the legacy flat shape, the
  query-param JSON parsing, and the emitted Mongo all stay identical. The
  existing optio filter tests (`optio-contracts` `process-schema.test.ts`, any
  `optio-api` translator tests) must pass **unchanged**.

## Testing

Per-package `vitest` (build via `tsc`). All pure — **no Mongo/DB needed**
(translation produces plain objects; no change streams here).

- **filtrum-core**:
  - `compile` over a trivial test dialect (e.g. T = string S-expr): `AND/OR/NOT`,
    multi-op leaves → `and`, empty combinator → `matchAll`, legacy-flat desugar.
  - `makeFilterSchema`: base ops validate; `extraLeafOps` accept a custom op;
    `allowLegacyFlat` toggles the flat branch; bad shapes rejected; QueryParam
    parses JSON string and rejects invalid JSON.
  - builders produce the expected predicate objects; `leaf` builds custom ops.
- **filtrum-mongo**:
  - each structured op → correct `{[prefix+field]:{$op}}`; `fieldPrefix` applied;
    `AND/OR/NOT`→`$and/$or/$nor`; empty → `{}`.
  - a **custom op** handler (a stub, e.g. `regex`) is invoked and its whole
    fragment is emitted (proves the extension point + that custom ops can ignore
    the prefix) — this is the mechanism the P5 `search` op will use.
  - `createMongoFilterTranslator` end-to-end parity with a hand-built filter.
- **optio regression**: existing optio filter tests pass unchanged after the
  migration; add a focused test asserting `metadata.` prefixing + legacy flat are
  preserved through the new wrapper.

## Out of scope / deferred

- The `search` text op and its `buildTextSearchFilter` wiring, excavator's api
  search path, reading `_qt_meta` — **P5**.
- Non-Mongo dialects (SQL, in-memory) — future; the `Dialect<T>` seam is the
  only thing P1 ships for them. Do **not** build speculative dialects now.
- Extracting filtrum to its own monorepo — later; the `-core`/`-mongo` split
  pre-stages it.

## Open items (settle during the plan)

- Exact `FilterFieldPath` generalization (drop the `metadata` assumption while
  keeping a sane dotted-path validator optio can reuse with its own constraint).
- Whether `process-filter-helpers` re-exports or is replaced by a
  `filtrum-core`-imported barrel (keep the public import paths stable either way).
- filtrum package `0.1.0` publish mechanics (same release flow as other optio
  packages).
