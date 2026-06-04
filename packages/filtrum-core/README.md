# filtrum-core

Backend-agnostic, extensible filter predicate language. `filtrum-core` knows
nothing about any storage backend; it provides three things:

1. **Schema** — `makeFilterSchema(options?)` builds a set of Zod schemas
   (`LeafOps`, `PredicateSchema`, `LegacySchema`, `FilterSchema`,
   `QueryParamSchema`) for validating filter predicates. The predicate language
   supports the combinators `AND` / `OR` / `NOT` and per-field leaf operators
   (`eq`, `ne`, `in`, `nin`, `exists`, `gt`, `gte`, `lt`, `lte`).

2. **Builders** — small helpers (`and`, `or`, `not`, `leaf`, `eq`, `ne`,
   `isIn`, `notIn`, `exists`, `gt`, `gte`, `lt`, `lte`) that construct predicate
   objects programmatically.

3. **Compile / `Dialect<T>`** — `compile<T>(filter, dialect, ctx?)` walks the
   predicate tree and delegates every node to a `Dialect<T>`. The dialect
   decides what `T` is and how each combinator / operator is emitted, so the
   same predicate can target any backend.

## Extension

- **New operators** — pass `extraLeafOps` to `makeFilterSchema` to extend the
  validated leaf-ops (e.g. `{ search: z.string() }`), and supply a matching
  handler in your dialect's `op` implementation.
- **New backend** — implement `Dialect<T>` for your target.

See [`filtrum-mongo`](../filtrum-mongo) for the reference backend (a MongoDB
dialect plus a pre-wired translator).
