# filtrum-mongo — agent notes

MongoDB dialect for filtrum. Translates filter predicates (from `filtrum-core`)
into Mongo query filters.

## Exported symbols

- `type MongoOpHandler = (field: string, value: unknown, ctx: CompileCtx) => Filter<Document>`
- `builtinMongoOps: Record<string, MongoOpHandler>` — `eq, ne, in, nin, exists, gt, gte, lt, lte`.
- `makeMongoDialect(ops?) => Dialect<Filter<Document>>`
- `createMongoFilterTranslator(options?) => (filter: unknown) => Filter<Document>`,
  where `options = { fieldPrefix?: string; ops?: Record<string, MongoOpHandler> }`.

## Op-handler contract

- A handler returns the **whole** Mongo fragment for its field/op, not just the
  operator value. This lets custom ops produce arbitrary shapes.
- Built-in handlers apply `ctx.fieldPrefix` to the field path. **Custom ops may
  ignore the prefix** and build whatever path they need (e.g. a sidecar index).
- An unknown operator throws `filtrum-mongo: unknown operator "<name>"`.

## Constraints

- The only runtime dependency is `filtrum-core`. `mongodb` is a **type-only**,
  optional peer dependency — never import it for runtime values.
