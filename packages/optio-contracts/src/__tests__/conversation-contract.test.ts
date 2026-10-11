import { describe, it, expect } from 'vitest';
import {
  processesContract,
  ConversationFailureReason,
  ConversationRecordSchema,
} from '../index.js';

describe('processesContract.getConversation', () => {
  it('is GET /processes/:id/conversation with the instance query', () => {
    const route = processesContract.getConversation;
    expect(route.method).toBe('GET');
    expect(route.path).toBe('/processes/:id/conversation');
    expect(Object.keys(route.responses).map(Number).sort()).toEqual([200, 401, 403, 404, 409, 500, 503]);
    expect((route.query as any).parse({ database: 'd', prefix: 'p' })).toEqual({ database: 'd', prefix: 'p' });
  });

  it('accepts a record of [seq, event] pairs with a nullable capturedAt', () => {
    const record = { events: [[1, { type: 'user' }], [2, { type: 'assistant' }]], capturedAt: null };
    expect(ConversationRecordSchema.parse(record)).toEqual(record);
    expect(ConversationRecordSchema.safeParse({ events: [], capturedAt: '2026-10-12T10:00:00+00:00' }).success).toBe(true);
    expect(ConversationRecordSchema.safeParse({ capturedAt: null }).success).toBe(false);
  });

  it('error bodies carry a typed reason', () => {
    expect(ConversationFailureReason.options).toEqual([
      'forbidden', 'not-found', 'no-conversation-support', 'no-conversation',
      'not-suspended', 'read-failed', 'engine-unavailable',
    ]);
    const body = (processesContract.getConversation.responses as any)[503];
    expect(body.parse({ reason: 'engine-unavailable', message: 'x' })).toEqual({ reason: 'engine-unavailable', message: 'x' });
    expect(body.safeParse({ message: 'x' }).success).toBe(false);
  });
});
