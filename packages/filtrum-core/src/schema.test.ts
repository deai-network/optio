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
