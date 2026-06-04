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
