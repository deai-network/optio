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
