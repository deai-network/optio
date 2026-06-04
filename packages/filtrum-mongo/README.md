# filtrum-mongo

MongoDB dialect for [filtrum](../filtrum-core): translate filter predicates into
Mongo query filters.

## Concepts

- **`makeMongoDialect(ops?)`** — builds a `Dialect<Filter<Document>>` for use with
  `filtrum-core`'s `compile`. Built-in operators (`eq`, `ne`, `in`, `nin`,
  `exists`, `gt`, `gte`, `lt`, `lte`) map to their `$`-prefixed Mongo equivalents.
  Combinators map to `$and` / `$or` / `$nor`; an empty filter matches all (`{}`).
- **`createMongoFilterTranslator(options?)`** — the pre-wired entry point. Returns
  a `(filter) => Filter<Document>` function.

## Per-op prefixing

Built-in op handlers prefix each field path with `options.fieldPrefix`, so

```ts
const translate = createMongoFilterTranslator({ fieldPrefix: 'metadata.' })
translate({ a: { eq: 1 } }) // → { 'metadata.a': { $eq: 1 } }
```

## Custom ops

Pass extra operators via `ops`. A handler returns the whole Mongo fragment and may
ignore the prefix entirely:

```ts
createMongoFilterTranslator({
  ops: { search: (f, v) => ({ [`_qt.${f}`]: { $regex: v } }) },
})
```

## mongodb peer

`mongodb` is a **type-only**, optional peer dependency — this package emits Mongo
query objects but has no runtime dependency on the driver.
