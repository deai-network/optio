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
