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
