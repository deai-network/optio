import { describe, it, expect } from 'vitest';
import {
  allow, deny, denyWithReason, getVerdict, getReason, andDecisions,
} from '../decision.js';

describe('decision', () => {
  it('allow/deny/denyWithReason build correct shapes', () => {
    expect(allow('ok')).toEqual({ verdict: true, reason: 'ok' });
    expect(deny('nope')).toEqual({ verdict: false, reason: 'nope' });
    expect(denyWithReason('blocked')).toEqual({ verdict: false, reason: 'blocked' });
  });

  it('getVerdict handles boolean, object, and undefined', () => {
    expect(getVerdict(true, false)).toBe(true);
    expect(getVerdict(false, true)).toBe(false);
    expect(getVerdict({ verdict: true }, false)).toBe(true);
    expect(getVerdict({ verdict: false, reason: 'x' }, true)).toBe(false);
    expect(getVerdict(undefined, true)).toBe(true);
    expect(getVerdict(undefined, false)).toBe(false);
  });

  it('getReason handles boolean, object, and undefined', () => {
    expect(getReason(true)).toBeUndefined();
    expect(getReason(false)).toBeUndefined();
    expect(getReason({ verdict: false, reason: 'because' })).toBe('because');
    expect(getReason({ verdict: true })).toBeUndefined();
    expect(getReason(undefined)).toBeUndefined();
  });

  it('andDecisions ANDs verdicts and concatenates reasons', () => {
    expect(andDecisions(true, true)).toEqual({ verdict: true, reason: undefined });
    expect(andDecisions(allow('a'), allow('b'))).toEqual({ verdict: true, reason: 'a; b' });
    expect(andDecisions(deny('x'), allow('y'))).toEqual({ verdict: false, reason: 'x' });
    expect(andDecisions(allow('y'), deny('x'))).toEqual({ verdict: false, reason: 'x' });
  });
});
