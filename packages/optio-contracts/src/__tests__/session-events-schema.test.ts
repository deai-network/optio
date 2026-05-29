import { describe, it, expect } from 'vitest';
import { SessionEventSchema, BrowserOpenRequestSchema } from '../schemas/process.js';
import { SessionEventsStreamMessageSchema } from '../schemas/session-events.js';

describe('BrowserOpenRequestSchema', () => {
  it('accepts a requestId + url', () => {
    const parsed = BrowserOpenRequestSchema.parse({ requestId: 'abc', url: 'https://x' });
    expect(parsed.url).toBe('https://x');
  });
});

describe('SessionEventSchema discriminated union', () => {
  it('accepts an attention event', () => {
    const parsed = SessionEventSchema.parse({ requestId: 'r1', type: 'attention', reason: 'need help' });
    expect(parsed.type).toBe('attention');
  });

  it('accepts a domain event with arbitrary data', () => {
    const parsed = SessionEventSchema.parse({ requestId: 'r2', type: 'domain', keyword: 'k', data: { a: [1] } });
    expect(parsed.type).toBe('domain');
    if (parsed.type === 'domain') expect(parsed.data).toEqual({ a: [1] });
  });

  it('rejects an unknown type', () => {
    expect(SessionEventSchema.safeParse({ requestId: 'r', type: 'other' }).success).toBe(false);
  });
});

describe('SessionEventsStreamMessageSchema', () => {
  it('accepts a session-events message', () => {
    const parsed = SessionEventsStreamMessageSchema.parse({
      type: 'session-events',
      processId: '507f1f77bcf86cd799439011',
      events: [{ requestId: 'r1', type: 'attention', reason: 'x' }],
    });
    expect(parsed.events).toHaveLength(1);
  });
});
