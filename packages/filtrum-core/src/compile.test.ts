import { describe, it, expect } from 'vitest'
import { compile, type Dialect } from './compile'
import { and, or, not, eq, leaf } from './builders'

// A trivial string dialect to prove backend-agnosticism.
const S: Dialect<string> = {
  and: (p) => `(${p.join(' & ')})`,
  or: (p) => `(${p.join(' | ')})`,
  not: (p) => `!${p}`,
  matchAll: () => '*',
  op: (name, field, value, ctx) => `${ctx.fieldPrefix}${field} ${name} ${JSON.stringify(value)}`,
}

describe('compile', () => {
  it('compiles a single leaf', () => {
    expect(compile(eq('a', 1), S)).toBe('a eq 1')
  })
  it('applies fieldPrefix', () => {
    expect(compile(eq('a', 1), S, { fieldPrefix: 'm.' })).toBe('m.a eq 1')
  })
  it('compiles AND/OR/NOT', () => {
    expect(compile(and(eq('a', 1), or(eq('b', 2), not(eq('c', 3)))), S))
      .toBe('(a eq 1 & (b eq 2 | !c eq 3))')
  })
  it('multi-op leaf becomes AND', () => {
    expect(compile({ a: { gt: 1, lt: 9 } }, S)).toBe('(a gt 1 & a lt 9)')
  })
  it('empty / non-object → matchAll', () => {
    expect(compile({}, S)).toBe('*')
    expect(compile(undefined, S)).toBe('*')
  })
  it('empty AND → matchAll', () => {
    expect(compile({ AND: [] }, S)).toBe('*')
  })
  it('legacy flat desugars to eq (single) / AND of eq (multi)', () => {
    expect(compile({ a: 1 }, S)).toBe('a eq 1')
    expect(compile({ a: 1, b: 2 }, S)).toBe('(a eq 1 & b eq 2)')
  })
  it('routes a custom op through the dialect', () => {
    expect(compile(leaf('t', 'search', 'hi'), S)).toBe('t search "hi"')
  })
})
