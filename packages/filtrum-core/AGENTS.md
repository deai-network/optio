# filtrum-core — agent notes

Backend-agnostic filter predicate language: Zod schema + builders + a
pluggable-dialect compiler. **This package must stay backend-free** — it must
not import or reference any storage backend (MongoDB, SQL, etc.). Backend logic
lives in dialect packages such as `filtrum-mongo`.

## Exported symbols

From `schema.ts`:

- `FilterScalar` (zod union), type `FilterScalar`.
- `defaultFieldPath` — zod string validator for dotted field paths.
- `BaseLeafOps` — zod object: `eq, ne, in, nin, exists, gt, gte, lt, lte` (strict).
- `MakeFilterSchemaOptions` — `{ extraLeafOps?: Record<string, z.ZodTypeAny>; allowLegacyFlat?: boolean; fieldPath?: z.ZodTypeAny; jsonErrorMessage?: string }`.
- `makeFilterSchema(options?)` → `{ LeafOps, PredicateSchema, LegacySchema, FilterSchema, QueryParamSchema }`.

From `builders.ts`:

- `Predicate` = `Record<string, unknown>`.
- `and, or, not, leaf, eq, ne, isIn, notIn, exists, gt, gte, lt, lte`.

From `compile.ts`:

- `interface CompileCtx { fieldPrefix: string }`.
- `interface Dialect<T> { and(parts:T[]):T; or(parts:T[]):T; not(p:T):T; matchAll():T; op(name:string, field:string, value:unknown, ctx:CompileCtx):T }`.
- `compile<T>(filter:unknown, dialect:Dialect<T>, ctx?:Partial<CompileCtx>):T`.

## Extension seam: `Dialect<T>`

`compile` walks the predicate tree (`AND`/`OR`/`NOT`, legacy-flat desugaring,
per-field leaf ops) and delegates each node to the supplied `Dialect<T>`. The
dialect chooses the output type `T` and emits the actual fragments. Custom
operators are added by (a) declaring them via `extraLeafOps` in the schema and
(b) handling them in the dialect's `op` method.
