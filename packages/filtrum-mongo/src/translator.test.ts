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
