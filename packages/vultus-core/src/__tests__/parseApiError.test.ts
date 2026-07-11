import { describe, it, expect } from 'vitest';
import { parseApiError } from '../parseApiError.js';

describe('parseApiError', () => {
  it('extracts status, reason, message from ts-rest reject shape', () => {
    expect(
      parseApiError({ status: 409, body: { message: 'conflict', reason: 'slug-conflict' } }),
    ).toEqual({ status: 409, reason: 'slug-conflict', message: 'conflict' });
  });

  it('returns status: 0 when input is not an object', () => {
    expect(parseApiError(null)).toEqual({ status: 0 });
    expect(parseApiError('string err')).toEqual({ status: 0 });
    expect(parseApiError(42)).toEqual({ status: 0 });
  });

  it('handles objects with no body field', () => {
    expect(parseApiError({ status: 500 })).toEqual({ status: 500 });
  });

  it('handles objects with body but no reason / message', () => {
    expect(parseApiError({ status: 200, body: {} })).toEqual({ status: 200 });
  });
});
