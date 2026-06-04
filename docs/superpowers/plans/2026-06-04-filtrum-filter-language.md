# filtrum Filter Language — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract optio's metadata-filter language into two new optio-monorepo packages — `filtrum-core` (backend-agnostic language: Zod schema + builders + `Dialect<T>` compiler + extension points) and `filtrum-mongo` (Mongo dialect + pre-wired translator) — and migrate optio onto them with zero behavior change.

**Architecture:** `filtrum-core` knows nothing about any backend; `compile<T>(filter, dialect, ctx)` walks the predicate tree and delegates to a `Dialect<T>`. `filtrum-mongo` supplies the Mongo dialect and `createMongoFilterTranslator`. optio re-exports filtrum-configured schemas/builders and rewires its translator to `createMongoFilterTranslator({ fieldPrefix: 'metadata.' })`. The `search` text op and excavator wiring are P5, **not here**.

**Tech Stack:** TypeScript ESM, Zod, MongoDB (types only), vitest, tsc, pnpm workspace.

**Spec:** `docs/superpowers/specs/2026-06-04-filtrum-filter-language-design.md`

---

## Shared API contract (all tasks conform to this — it is what makes the parallel tasks agree)

`filtrum-core` exports:
- `FilterScalar` (zod), type `FilterScalar`.
- `defaultFieldPath` (zod string validator).
- `BaseLeafOps` (zod object: `eq, ne, in, nin, exists, gt, gte, lt, lte`).
- `makeFilterSchema(options?) => { LeafOps, PredicateSchema, LegacySchema, FilterSchema, QueryParamSchema }`, where `options = { extraLeafOps?: Record<string, z.ZodTypeAny>; allowLegacyFlat?: boolean; fieldPath?: z.ZodTypeAny; jsonErrorMessage?: string }`.
- Builders: `and, or, not, leaf, eq, ne, isIn, notIn, exists, gt, gte, lt, lte`; type `Predicate = Record<string, unknown>`.
- `interface CompileCtx { fieldPrefix: string }`, `interface Dialect<T> { and(parts:T[]):T; or(parts:T[]):T; not(p:T):T; matchAll():T; op(name:string, field:string, value:unknown, ctx:CompileCtx):T }`, `compile<T>(filter:unknown, dialect:Dialect<T>, ctx?:Partial<CompileCtx>):T`.

`filtrum-mongo` exports:
- `type MongoOpHandler = (field:string, value:unknown, ctx:CompileCtx) => Filter<Document>`.
- `builtinMongoOps: Record<string, MongoOpHandler>`.
- `makeMongoDialect(ops?) => Dialect<Filter<Document>>`.
- `createMongoFilterTranslator(options?) => (filter:unknown) => Filter<Document>`, `options = { fieldPrefix?: string; ops?: Record<string, MongoOpHandler> }`.

---

## File structure

- `packages/filtrum-core/` — `package.json`, `tsconfig.json`, `AGENTS.md`, `README.md`, `src/{schema,builders,compile,index}.ts`, `src/{schema,builders,compile}.test.ts`.
- `packages/filtrum-mongo/` — `package.json`, `tsconfig.json`, `AGENTS.md`, `README.md`, `src/{dialect,translator,index}.ts`, `src/{dialect,translator}.test.ts`.
- optio migration — `packages/optio-contracts/src/schemas/process.ts`, `packages/optio-contracts/src/process-filter-helpers.ts`, `packages/optio-contracts/package.json`, `packages/optio-api/src/metadata-filter-query.ts`, `packages/optio-api/package.json`, root `AGENTS.md` (package table).

## Execution shape (parallel)

Tasks 1, 2, 3 are **file-disjoint and run in parallel.** They deliberately break dependency barriers: Task 2 imports `filtrum-core`, Task 3 imports both new packages — none of which are built until the final phase. That is expected; **do not** run builds/tests/git inside Tasks 1–3. **Task 4** is the single serial verify/commit phase: `pnpm install`, build+test everything, lint, fix, commit.

---

## Task 1: `filtrum-core` package

**Files (all under `packages/filtrum-core/`):** create everything below.

- [ ] **Step 1: `package.json`**

```json
{
  "name": "filtrum-core",
  "version": "0.1.0",
  "license": "Apache-2.0",
  "description": "Backend-agnostic, extensible filter predicate language: schema, builders, and a pluggable-dialect compiler.",
  "repository": { "type": "git", "url": "git+https://github.com/deai-network/optio.git", "directory": "packages/filtrum-core" },
  "author": "Kristof Csillag <kristof.csillag@deai-labs.com>",
  "type": "module",
  "files": ["dist", "README.md", "LICENSE"],
  "main": "dist/index.js",
  "types": "dist/index.d.ts",
  "exports": { ".": { "import": "./dist/index.js", "types": "./dist/index.d.ts" } },
  "scripts": { "build": "tsc", "dev": "tsc --watch", "test": "vitest run", "test:watch": "vitest" },
  "dependencies": { "zod": "^3.24.0" },
  "devDependencies": { "typescript": "^5.7.0", "vitest": "^3.0.0" }
}
```

- [ ] **Step 2: `tsconfig.json`**

```json
{
  "extends": "../../tsconfig.base.json",
  "compilerOptions": { "outDir": "dist", "rootDir": "src" },
  "include": ["src"],
  "exclude": ["src/**/*.test.ts", "src/**/__tests__/**"]
}
```

- [ ] **Step 3: `src/schema.ts`**

```ts
import { z } from 'zod'

export const FilterScalar = z.union([z.string(), z.number(), z.boolean(), z.null()])
export type FilterScalar = z.infer<typeof FilterScalar>

// Generic dotted-path validator: one or more non-empty, non-whitespace segments
// joined by '.'. No backend/namespace assumptions.
export const defaultFieldPath = z
  .string()
  .regex(/^[^.\s]+(\.[^.\s]+)*$/, 'invalid field path')

export const BaseLeafOps = z
  .object({
    eq: FilterScalar.optional(),
    ne: FilterScalar.optional(),
    in: z.array(FilterScalar).optional(),
    nin: z.array(FilterScalar).optional(),
    exists: z.boolean().optional(),
    gt: FilterScalar.optional(),
    gte: FilterScalar.optional(),
    lt: FilterScalar.optional(),
    lte: FilterScalar.optional(),
  })
  .strict()

export interface MakeFilterSchemaOptions {
  extraLeafOps?: Record<string, z.ZodTypeAny>
  allowLegacyFlat?: boolean
  fieldPath?: z.ZodTypeAny
  jsonErrorMessage?: string
}

export function makeFilterSchema(options: MakeFilterSchemaOptions = {}) {
  const {
    extraLeafOps = {},
    allowLegacyFlat = false,
    fieldPath = defaultFieldPath,
    jsonErrorMessage = 'filter must be valid JSON',
  } = options

  const extraShape = Object.fromEntries(
    Object.entries(extraLeafOps).map(([k, v]) => [k, v.optional()]),
  )
  const LeafOps = BaseLeafOps.extend(extraShape)

  const PredicateSchema: z.ZodType<unknown> = z.lazy(() =>
    z.union([
      z.object({ AND: z.array(PredicateSchema).min(1) }).strict(),
      z.object({ OR: z.array(PredicateSchema).min(1) }).strict(),
      z.object({ NOT: PredicateSchema }).strict(),
      z.record(fieldPath, LeafOps),
    ]),
  )

  const LegacySchema = z.record(fieldPath, FilterScalar)

  const FilterSchema: z.ZodType<unknown> = allowLegacyFlat
    ? z.union([PredicateSchema, LegacySchema])
    : PredicateSchema

  const QueryParamSchema = z
    .string()
    .transform((s, ctx) => {
      try {
        return JSON.parse(s) as unknown
      } catch {
        ctx.addIssue({ code: 'custom', message: jsonErrorMessage })
        return z.NEVER
      }
    })
    .pipe(FilterSchema)

  return { LeafOps, PredicateSchema, LegacySchema, FilterSchema, QueryParamSchema }
}
```

- [ ] **Step 4: `src/builders.ts`**

```ts
import type { FilterScalar } from './schema'

export type Predicate = Record<string, unknown>

export const and = (...preds: Predicate[]): Predicate => ({ AND: preds })
export const or = (...preds: Predicate[]): Predicate => ({ OR: preds })
export const not = (pred: Predicate): Predicate => ({ NOT: pred })

// Generic leaf for custom ops the core does not know about.
export const leaf = (field: string, op: string, value: unknown): Predicate => ({ [field]: { [op]: value } })

export const eq = (field: string, v: FilterScalar): Predicate => leaf(field, 'eq', v)
export const ne = (field: string, v: FilterScalar): Predicate => leaf(field, 'ne', v)
export const isIn = (field: string, v: FilterScalar[]): Predicate => leaf(field, 'in', v)
export const notIn = (field: string, v: FilterScalar[]): Predicate => leaf(field, 'nin', v)
export const exists = (field: string, v = true): Predicate => leaf(field, 'exists', v)
export const gt = (field: string, v: FilterScalar): Predicate => leaf(field, 'gt', v)
export const gte = (field: string, v: FilterScalar): Predicate => leaf(field, 'gte', v)
export const lt = (field: string, v: FilterScalar): Predicate => leaf(field, 'lt', v)
export const lte = (field: string, v: FilterScalar): Predicate => leaf(field, 'lte', v)
```

- [ ] **Step 5: `src/compile.ts`**

```ts
export interface CompileCtx { fieldPrefix: string }

export interface Dialect<T> {
  and(parts: T[]): T
  or(parts: T[]): T
  not(part: T): T
  matchAll(): T
  op(name: string, field: string, value: unknown, ctx: CompileCtx): T
}

const COMBINATORS = new Set(['AND', 'OR', 'NOT'])

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

// Legacy-flat: every top-level key is a field (not a combinator) and every value
// is NOT a leaf-ops object (scalar or array) → implicit AND of equality.
function isLegacyFlat(node: Record<string, unknown>): boolean {
  for (const [k, v] of Object.entries(node)) {
    if (COMBINATORS.has(k)) return false
    if (isPlainObject(v)) return false
  }
  return true
}

export function compile<T>(filter: unknown, dialect: Dialect<T>, ctx: Partial<CompileCtx> = {}): T {
  const c: CompileCtx = { fieldPrefix: ctx.fieldPrefix ?? '' }
  if (!isPlainObject(filter) || Object.keys(filter).length === 0) return dialect.matchAll()
  return compileNode(filter, dialect, c)
}

function compileNode<T>(node: Record<string, unknown>, d: Dialect<T>, c: CompileCtx): T {
  if (Array.isArray(node.AND)) {
    const parts = node.AND as unknown[]
    if (parts.length === 0) return d.matchAll()
    return d.and(parts.map((p) => compileNode(p as Record<string, unknown>, d, c)))
  }
  if (Array.isArray(node.OR)) {
    const parts = node.OR as unknown[]
    if (parts.length === 0) return d.matchAll()
    return d.or(parts.map((p) => compileNode(p as Record<string, unknown>, d, c)))
  }
  if ('NOT' in node) {
    return d.not(compileNode(node.NOT as Record<string, unknown>, d, c))
  }
  if (isLegacyFlat(node)) {
    const parts = Object.entries(node).map(([field, v]) => d.op('eq', field, v, c))
    return parts.length === 1 ? (parts[0] as T) : d.and(parts)
  }
  const fragments: T[] = []
  for (const [field, ops] of Object.entries(node)) {
    if (!isPlainObject(ops)) continue
    for (const [op, value] of Object.entries(ops)) {
      fragments.push(d.op(op, field, value, c))
    }
  }
  if (fragments.length === 0) return d.matchAll()
  return fragments.length === 1 ? (fragments[0] as T) : d.and(fragments)
}
```

- [ ] **Step 6: `src/index.ts`**

```ts
export * from './schema'
export * from './builders'
export * from './compile'
```

- [ ] **Step 7: `src/compile.test.ts`**

```ts
import { describe, it, expect } from 'vitest'
import { compile, type Dialect } from './compile'
import { and, or, not, eq, leaf } from './builders'

// A trivial string dialect to prove backend-agnosticism.
const S: Dialect<string> = {
  and: (p) => `(${p.join(' & ')})`,
  or: (p) => `(${p.join(' | ')})`,
  not: (p) => `!${p}`,
  matchAll: () => '*',
  op: (name, field, value, ctx) => `${ctx.fieldPrefix}${field} ${name} ${JSON.stringify(value)}`,
}

describe('compile', () => {
  it('compiles a single leaf', () => {
    expect(compile(eq('a', 1), S)).toBe('a eq 1')
  })
  it('applies fieldPrefix', () => {
    expect(compile(eq('a', 1), S, { fieldPrefix: 'm.' })).toBe('m.a eq 1')
  })
  it('compiles AND/OR/NOT', () => {
    expect(compile(and(eq('a', 1), or(eq('b', 2), not(eq('c', 3)))), S))
      .toBe('(a eq 1 & (b eq 2 | !c eq 3))')
  })
  it('multi-op leaf becomes AND', () => {
    expect(compile({ a: { gt: 1, lt: 9 } }, S)).toBe('(a gt 1 & a lt 9)')
  })
  it('empty / non-object → matchAll', () => {
    expect(compile({}, S)).toBe('*')
    expect(compile(undefined, S)).toBe('*')
  })
  it('empty AND → matchAll', () => {
    expect(compile({ AND: [] }, S)).toBe('*')
  })
  it('legacy flat desugars to eq (single) / AND of eq (multi)', () => {
    expect(compile({ a: 1 }, S)).toBe('a eq 1')
    expect(compile({ a: 1, b: 2 }, S)).toBe('(a eq 1 & b eq 2)')
  })
  it('routes a custom op through the dialect', () => {
    expect(compile(leaf('t', 'search', 'hi'), S)).toBe('t search "hi"')
  })
})
```

- [ ] **Step 8: `src/builders.test.ts`**

```ts
import { describe, it, expect } from 'vitest'
import { and, or, not, eq, ne, isIn, notIn, exists, gt, leaf } from './builders'

describe('builders', () => {
  it('combinators', () => {
    expect(and(eq('a', 1), eq('b', 2))).toEqual({ AND: [{ a: { eq: 1 } }, { b: { eq: 2 } }] })
    expect(or(eq('a', 1))).toEqual({ OR: [{ a: { eq: 1 } }] })
    expect(not(eq('a', 1))).toEqual({ NOT: { a: { eq: 1 } } })
  })
  it('leaf ops', () => {
    expect(eq('a', 1)).toEqual({ a: { eq: 1 } })
    expect(ne('a', 1)).toEqual({ a: { ne: 1 } })
    expect(isIn('a', [1, 2])).toEqual({ a: { in: [1, 2] } })
    expect(notIn('a', [1])).toEqual({ a: { nin: [1] } })
    expect(exists('a')).toEqual({ a: { exists: true } })
    expect(gt('a', 1)).toEqual({ a: { gt: 1 } })
    expect(leaf('t', 'search', 'x')).toEqual({ t: { search: 'x' } })
  })
})
```

- [ ] **Step 9: `src/schema.test.ts`**

```ts
import { describe, it, expect } from 'vitest'
import { z } from 'zod'
import { makeFilterSchema } from './schema'

describe('makeFilterSchema', () => {
  it('validates base predicate + rejects unknown op', () => {
    const { FilterSchema } = makeFilterSchema()
    expect(FilterSchema.safeParse({ a: { eq: 1 } }).success).toBe(true)
    expect(FilterSchema.safeParse({ AND: [{ a: { gt: 1 } }] }).success).toBe(true)
    expect(FilterSchema.safeParse({ a: { search: 'x' } }).success).toBe(false)
  })
  it('extraLeafOps adds a custom op', () => {
    const { FilterSchema } = makeFilterSchema({ extraLeafOps: { search: z.string() } })
    expect(FilterSchema.safeParse({ a: { search: 'x' } }).success).toBe(true)
    expect(FilterSchema.safeParse({ a: { search: 1 } }).success).toBe(false)
  })
  it('allowLegacyFlat toggles the flat branch', () => {
    expect(makeFilterSchema({ allowLegacyFlat: false }).FilterSchema.safeParse({ a: 1 }).success).toBe(false)
    expect(makeFilterSchema({ allowLegacyFlat: true }).FilterSchema.safeParse({ a: 1 }).success).toBe(true)
  })
  it('QueryParamSchema parses JSON and rejects invalid JSON', () => {
    const { QueryParamSchema } = makeFilterSchema()
    expect(QueryParamSchema.safeParse('{"a":{"eq":1}}').success).toBe(true)
    const bad = QueryParamSchema.safeParse('{not json')
    expect(bad.success).toBe(false)
  })
  it('custom jsonErrorMessage is used', () => {
    const { QueryParamSchema } = makeFilterSchema({ jsonErrorMessage: 'metadataFilter must be valid JSON' })
    const r = QueryParamSchema.safeParse('{bad')
    expect(r.success).toBe(false)
    if (!r.success) expect(r.error.issues[0]?.message).toBe('metadataFilter must be valid JSON')
  })
})
```

- [ ] **Step 10: `README.md`** — short package intro (purpose, the three concepts: schema, builders, compile/Dialect, extension via `extraLeafOps`+custom dialect ops). Keep concise; mention `filtrum-mongo` as the reference backend.

- [ ] **Step 11: `AGENTS.md`** — per-package API summary: exported symbols (the Shared API contract above), the `Dialect<T>` extension seam, and the rule that core must stay backend-free.

---

## Task 2: `filtrum-mongo` package

**Files (all under `packages/filtrum-mongo/`):** create everything below.

- [ ] **Step 1: `package.json`**

```json
{
  "name": "filtrum-mongo",
  "version": "0.1.0",
  "license": "Apache-2.0",
  "description": "MongoDB dialect for filtrum: translate filter predicates to Mongo query filters.",
  "repository": { "type": "git", "url": "git+https://github.com/deai-network/optio.git", "directory": "packages/filtrum-mongo" },
  "author": "Kristof Csillag <kristof.csillag@deai-labs.com>",
  "type": "module",
  "files": ["dist", "README.md", "LICENSE"],
  "main": "dist/index.js",
  "types": "dist/index.d.ts",
  "exports": { ".": { "import": "./dist/index.js", "types": "./dist/index.d.ts" } },
  "scripts": { "build": "tsc", "dev": "tsc --watch", "test": "vitest run", "test:watch": "vitest" },
  "dependencies": { "filtrum-core": "workspace:*" },
  "peerDependencies": { "mongodb": ">=5" },
  "peerDependenciesMeta": { "mongodb": { "optional": true } },
  "devDependencies": { "mongodb": "^6.0.0", "typescript": "^5.7.0", "vitest": "^3.0.0" }
}
```

- [ ] **Step 2: `tsconfig.json`** (identical to Task 1 Step 2)

```json
{
  "extends": "../../tsconfig.base.json",
  "compilerOptions": { "outDir": "dist", "rootDir": "src" },
  "include": ["src"],
  "exclude": ["src/**/*.test.ts", "src/**/__tests__/**"]
}
```

- [ ] **Step 3: `src/dialect.ts`**

```ts
import type { Filter, Document } from 'mongodb'
import type { Dialect, CompileCtx } from 'filtrum-core'

export type MongoOpHandler = (field: string, value: unknown, ctx: CompileCtx) => Filter<Document>

const path = (ctx: CompileCtx, field: string) => `${ctx.fieldPrefix}${field}`

export const builtinMongoOps: Record<string, MongoOpHandler> = {
  eq: (f, v, c) => ({ [path(c, f)]: { $eq: v } }),
  ne: (f, v, c) => ({ [path(c, f)]: { $ne: v } }),
  in: (f, v, c) => ({ [path(c, f)]: { $in: v as unknown[] } }),
  nin: (f, v, c) => ({ [path(c, f)]: { $nin: v as unknown[] } }),
  exists: (f, v, c) => ({ [path(c, f)]: { $exists: v as boolean } }),
  gt: (f, v, c) => ({ [path(c, f)]: { $gt: v } }),
  gte: (f, v, c) => ({ [path(c, f)]: { $gte: v } }),
  lt: (f, v, c) => ({ [path(c, f)]: { $lt: v } }),
  lte: (f, v, c) => ({ [path(c, f)]: { $lte: v } }),
}

export function makeMongoDialect(
  ops: Record<string, MongoOpHandler> = {},
): Dialect<Filter<Document>> {
  const table = { ...builtinMongoOps, ...ops }
  return {
    and: (parts) => ({ $and: parts }),
    or: (parts) => ({ $or: parts }),
    not: (part) => ({ $nor: [part] }),
    matchAll: () => ({}),
    op: (name, field, value, ctx) => {
      const h = table[name]
      if (!h) throw new Error(`filtrum-mongo: unknown operator "${name}"`)
      return h(field, value, ctx)
    },
  }
}
```

- [ ] **Step 4: `src/translator.ts`**

```ts
import type { Filter, Document } from 'mongodb'
import { compile } from 'filtrum-core'
import { makeMongoDialect, type MongoOpHandler } from './dialect'

export interface CreateMongoFilterTranslatorOptions {
  fieldPrefix?: string
  ops?: Record<string, MongoOpHandler>
}

export function createMongoFilterTranslator(
  options: CreateMongoFilterTranslatorOptions = {},
): (filter: unknown) => Filter<Document> {
  const { fieldPrefix = '', ops = {} } = options
  const dialect = makeMongoDialect(ops)
  return (filter: unknown) => compile(filter, dialect, { fieldPrefix })
}
```

- [ ] **Step 5: `src/index.ts`**

```ts
export * from './dialect'
export * from './translator'
```

- [ ] **Step 6: `src/dialect.test.ts`**

```ts
import { describe, it, expect } from 'vitest'
import { makeMongoDialect, type MongoOpHandler } from './dialect'
import { compile } from 'filtrum-core'

describe('mongo dialect', () => {
  const d = makeMongoDialect()
  it('structured ops with prefix', () => {
    expect(compile({ a: { eq: 1 } }, d, { fieldPrefix: 'm.' })).toEqual({ 'm.a': { $eq: 1 } })
    expect(compile({ a: { exists: false } }, d)).toEqual({ a: { $exists: false } })
    expect(compile({ a: { in: [1, 2] } }, d)).toEqual({ a: { $in: [1, 2] } })
  })
  it('combinators', () => {
    expect(compile({ AND: [{ a: { eq: 1 } }, { b: { gt: 2 } }] }, d))
      .toEqual({ $and: [{ a: { $eq: 1 } }, { b: { $gt: 2 } }] })
    expect(compile({ NOT: { a: { eq: 1 } } }, d)).toEqual({ $nor: [{ a: { $eq: 1 } }] })
    expect(compile({}, d)).toEqual({})
  })
  it('unknown op throws', () => {
    expect(() => compile({ a: { search: 'x' } }, d)).toThrow(/unknown operator/)
  })
  it('custom op handler controls its whole fragment and may ignore the prefix', () => {
    const search: MongoOpHandler = (field, value) => ({ [`_qt.${field}.ngrams`]: { $all: [value] } })
    const dc = makeMongoDialect({ search })
    expect(compile({ title: { search: 'hi' } }, dc, { fieldPrefix: 'm.' }))
      .toEqual({ '_qt.title.ngrams': { $all: ['hi'] } })
  })
})
```

- [ ] **Step 7: `src/translator.test.ts`**

```ts
import { describe, it, expect } from 'vitest'
import { createMongoFilterTranslator } from './translator'

describe('createMongoFilterTranslator', () => {
  it('applies fieldPrefix end-to-end', () => {
    const t = createMongoFilterTranslator({ fieldPrefix: 'metadata.' })
    expect(t({ AND: [{ a: { eq: 1 } }, { b: { exists: true } }] }))
      .toEqual({ $and: [{ 'metadata.a': { $eq: 1 } }, { 'metadata.b': { $exists: true } }] })
  })
  it('legacy flat → AND of eq', () => {
    const t = createMongoFilterTranslator({ fieldPrefix: 'metadata.' })
    expect(t({ a: 1, b: 2 })).toEqual({ $and: [{ 'metadata.a': { $eq: 1 } }, { 'metadata.b': { $eq: 2 } }] })
  })
  it('custom op via options.ops', () => {
    const t = createMongoFilterTranslator({ ops: { search: (f, v) => ({ [`_qt.${f}`]: { $regex: v } }) } })
    expect(t({ title: { search: 'hi' } })).toEqual({ '_qt.title': { $regex: 'hi' } })
  })
  it('empty filter → {}', () => {
    expect(createMongoFilterTranslator()(undefined)).toEqual({})
  })
})
```

- [ ] **Step 8: `README.md`** — short intro: `mongoDialect` / `createMongoFilterTranslator`, per-op prefixing, custom ops via `ops`, the type-only `mongodb` peer.

- [ ] **Step 9: `AGENTS.md`** — exported symbols, op-handler contract (returns the whole fragment; custom ops may ignore prefix), no runtime deps.

---

## Task 3: migrate optio onto filtrum (back-compat)

**Files:** `packages/optio-contracts/src/schemas/process.ts`, `packages/optio-contracts/src/process-filter-helpers.ts`, `packages/optio-contracts/package.json`, `packages/optio-api/src/metadata-filter-query.ts`, `packages/optio-api/package.json`, root `AGENTS.md`.

- [ ] **Step 1: add deps**

In `packages/optio-contracts/package.json` `dependencies`, add: `"filtrum-core": "workspace:*"`.
In `packages/optio-api/package.json` `dependencies`, add: `"filtrum-mongo": "workspace:*"` (and `"filtrum-core": "workspace:*"` if its source imports core types directly — it does not in this plan, so only `filtrum-mongo`).

- [ ] **Step 2: rewrite the filter block in `packages/optio-contracts/src/schemas/process.ts`**

Read the file. **Keep** the existing `FilterScalar` and `FilterFieldPath` definitions verbatim (they are the public contract and the `metadata.*`-oriented path validator). **Replace** the block from the `FilterLeafOps` definition through the filter-type exports (i.e. `FilterLeafOps`, `ProcessMetadataPredicateSchema`, `ProcessMetadataFilterLegacySchema`, `ProcessMetadataFilterSchema`, `MetadataFilterQueryParamSchema`, and their `type` exports) with:

```ts
// Filter language is provided by filtrum-core (extracted, backend-agnostic).
// FilterScalar + FilterFieldPath above remain the optio public contract and are
// fed to filtrum. metadata.* prefixing + Mongo translation live in optio-api.
import { makeFilterSchema } from 'filtrum-core';

const _filter = makeFilterSchema({
  allowLegacyFlat: true,
  fieldPath: FilterFieldPath,
  jsonErrorMessage: 'metadataFilter must be valid JSON',
});

export const FilterLeafOps = _filter.LeafOps;
export const ProcessMetadataPredicateSchema = _filter.PredicateSchema;
export const ProcessMetadataFilterLegacySchema = _filter.LegacySchema;
export const ProcessMetadataFilterSchema = _filter.FilterSchema;
export const MetadataFilterQueryParamSchema = _filter.QueryParamSchema;

export type FilterLeafOps = z.infer<typeof FilterLeafOps>;
export type ProcessMetadataPredicate = z.infer<typeof ProcessMetadataPredicateSchema>;
export type ProcessMetadataFilter = z.infer<typeof ProcessMetadataFilterSchema>;
```

Keep the existing `import { z } from 'zod'` (already present). Do not remove any other exports in the file.

- [ ] **Step 3: rewrite `packages/optio-contracts/src/process-filter-helpers.ts`** as thin re-exports preserving the exact optio signatures (return `ProcessMetadataPredicate`)

```ts
import * as f from 'filtrum-core';
import type { ProcessMetadataPredicate, FilterScalar } from './schemas/process.js';

// Builders are provided by filtrum-core; these wrappers preserve optio's
// ProcessMetadataPredicate return type and the isIn/notIn naming.
export const and = (...preds: ProcessMetadataPredicate[]): ProcessMetadataPredicate =>
  f.and(...(preds as f.Predicate[])) as ProcessMetadataPredicate;
export const or = (...preds: ProcessMetadataPredicate[]): ProcessMetadataPredicate =>
  f.or(...(preds as f.Predicate[])) as ProcessMetadataPredicate;
export const not = (pred: ProcessMetadataPredicate): ProcessMetadataPredicate =>
  f.not(pred as f.Predicate) as ProcessMetadataPredicate;
export const eq = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  f.eq(field, v) as ProcessMetadataPredicate;
export const ne = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  f.ne(field, v) as ProcessMetadataPredicate;
export const isIn = (field: string, v: FilterScalar[]): ProcessMetadataPredicate =>
  f.isIn(field, v) as ProcessMetadataPredicate;
export const notIn = (field: string, v: FilterScalar[]): ProcessMetadataPredicate =>
  f.notIn(field, v) as ProcessMetadataPredicate;
export const exists = (field: string, v: boolean = true): ProcessMetadataPredicate =>
  f.exists(field, v) as ProcessMetadataPredicate;
export const gt = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  f.gt(field, v) as ProcessMetadataPredicate;
export const gte = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  f.gte(field, v) as ProcessMetadataPredicate;
export const lt = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  f.lt(field, v) as ProcessMetadataPredicate;
export const lte = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  f.lte(field, v) as ProcessMetadataPredicate;
```

- [ ] **Step 4: rewrite `packages/optio-api/src/metadata-filter-query.ts`** over filtrum, preserving every existing export

```ts
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
```

- [ ] **Step 5: root `AGENTS.md`** — add `filtrum-core` and `filtrum-mongo` rows to the package table (Level/Package/Language/Install). Match the existing table format.

---

## Task 4: verify + commit (serial, after 1–3)

**Files:** none edited — installs, builds, tests, lints, commits.

- [ ] **Step 1: install (links the new workspace packages)**

```bash
cd /home/csillag/deai/optio && pnpm install
```

- [ ] **Step 2: build everything (typecheck catches any cross-package API mismatch)**

```bash
pnpm -r build
```
Expected: all packages compile, including `filtrum-core`, `filtrum-mongo`, `optio-contracts`, `optio-api`.

- [ ] **Step 3: run tests**

```bash
pnpm --filter filtrum-core test
pnpm --filter filtrum-mongo test
pnpm --filter optio-contracts test
pnpm --filter optio-api test
```
Expected: the new filtrum suites PASS, and the **existing** optio filter tests (`optio-contracts` schema tests, any `optio-api` translator tests) PASS **unchanged** — proving back-compat (metadata. prefix, legacy flat, query-param JSON all preserved).

- [ ] **Step 4: lint optio-api**

```bash
pnpm --filter optio-api lint
```
Expected: clean (no direct Mongo writes were added; this change is pure translation).

- [ ] **Step 5: fix any breakage**

Fix and re-run until green. Likely spots: a zod typing nuance in `makeFilterSchema` (the `BaseLeafOps.extend(extraShape)` shape typing), a `z.infer` mismatch on the re-exported optio types, or an `exactOptionalPropertyTypes`-style strictness issue. Do not weaken assertions or change wire behavior. If an existing optio test asserts an exact error string, confirm it still matches (the `jsonErrorMessage` is set to `'metadataFilter must be valid JSON'` for that reason).

- [ ] **Step 6: commit** (NO `Co-Authored-By` trailer — optio AGENTS.md forbids it)

```bash
cd /home/csillag/deai/optio
git add packages/filtrum-core packages/filtrum-mongo \
        packages/optio-contracts/src/schemas/process.ts \
        packages/optio-contracts/src/process-filter-helpers.ts \
        packages/optio-contracts/package.json \
        packages/optio-api/src/metadata-filter-query.ts \
        packages/optio-api/package.json \
        AGENTS.md pnpm-lock.yaml
git commit -m "feat(filtrum): extract backend-agnostic filter language into filtrum-core + filtrum-mongo

New packages: filtrum-core (Zod schema + builders + Dialect<T> compiler with a
custom-op extension point) and filtrum-mongo (mongoDialect + createMongoFilterTranslator).
optio's metadata-filter schemas/builders/translator are migrated onto them with
no wire/behavior change (metadata. prefix, legacy flat, query-param JSON preserved)."
```

---

## Self-review notes

- **Spec coverage:** filtrum-core schema+builders+compiler+extension (Task 1) ✓; filtrum-mongo dialect+translator (Task 2) ✓; two packages, names, deps, type-only mongodb (Task 1/2 package.json) ✓; optio back-compat migration incl. metadata. prefix, legacy flat, query-param message, builder signatures, preserved exports (Task 3) ✓; pure tests, no DB (all test steps) ✓; per-package AGENTS.md + root table (Task 1 S11 / Task 2 S9 / Task 3 S5) ✓; publish 0.1.0 (package.json version) ✓.
- **No placeholders:** full code for every src + test file and every config; migration edits show exact replacement code with retained-definition references.
- **Type consistency:** `Dialect<T>`/`CompileCtx`/`compile`/`MongoOpHandler`/`createMongoFilterTranslator`/`makeFilterSchema` names identical across Tasks 1–3 and the Shared API contract; optio wrappers cast filtrum `Predicate` → `ProcessMetadataPredicate` to preserve optio's public types.
