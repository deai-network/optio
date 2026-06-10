import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  getSessionId,
  resetSession,
  startSessionEvents,
  __resetSessionStateForTest,
} from '../session/sessionEvents.js';

// Minimal EventSource fake.
class FakeES {
  static instances: FakeES[] = [];
  url: string;
  onmessage: ((e: { data: string }) => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    FakeES.instances.push(this);
  }
  close() { this.closed = true; }
  emit(data: unknown) { this.onmessage?.({ data: JSON.stringify(data) }); }
}

beforeEach(() => {
  __resetSessionStateForTest();
  FakeES.instances = [];
  sessionStorage.clear();
  (globalThis as any).EventSource = FakeES as any;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('sessionId lifecycle', () => {
  it('mints once and persists in sessionStorage', () => {
    const a = getSessionId();
    const b = getSessionId();
    expect(a).toBe(b);
    expect(sessionStorage.getItem('optioSessionId')).toBe(a);
  });

  it('reuses a stored token (survives reload)', () => {
    sessionStorage.setItem('optioSessionId', 'persisted');
    expect(getSessionId()).toBe('persisted');
  });

  it('resetSession rotates the token and reconnects', () => {
    // A subscription must exist (a callback registered) for a stream to be
    // open — the session-events stream is gated on having a handler.
    startSessionEvents('', 'optio', undefined, { onAttention: () => {} });
    const before = getSessionId();
    const esBefore = FakeES.instances.at(-1)!;
    resetSession();
    const after = getSessionId();
    expect(after).not.toBe(before);
    expect(esBefore.closed).toBe(true);
    expect(FakeES.instances.at(-1)!.url).toContain(`sessionId=${after}`);
  });
});

describe('dispatch by type', () => {
  it('routes attention and domain events to the right callbacks, deduped by requestId', () => {
    const onAttention = vi.fn();
    const onClientMessage = vi.fn();
    startSessionEvents('', 'optio', undefined, { onAttention, onClientMessage });
    const es = FakeES.instances.at(-1)!;
    es.emit({
      type: 'session-events', processId: 'pid-1',
      events: [
        { requestId: 'a1', type: 'attention', reason: 'help' },
        { requestId: 'd1', type: 'client', keyword: 'k', data: { n: 1 } },
      ],
    });
    // Re-delivery of the same events (next poll tick) must not re-fire.
    es.emit({
      type: 'session-events', processId: 'pid-1',
      events: [{ requestId: 'a1', type: 'attention', reason: 'help' }],
    });
    expect(onAttention).toHaveBeenCalledTimes(1);
    expect(onAttention).toHaveBeenCalledWith('pid-1', 'help');
    expect(onClientMessage).toHaveBeenCalledTimes(1);
    expect(onClientMessage).toHaveBeenCalledWith('pid-1', 'k', { n: 1 });
  });
});
