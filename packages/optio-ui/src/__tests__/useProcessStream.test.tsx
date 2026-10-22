import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

// The per-process stream (no MultiProcessStreamProvider): its probe, then its EventSource.
vi.mock('../context/useOptioContext.js', () => ({
  useOptioBaseUrl: () => 'http://host',
  useOptioPrefix: () => 'optio',
  useOptioDatabase: () => 'mydb',
}));

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onopen: ((e: any) => void) | null = null;
  onmessage: ((e: any) => void) | null = null;
  onerror: ((e: any) => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }
  close() { this.closed = true; }
  emit(data: any) { this.onmessage?.({ data: JSON.stringify(data) } as any); }
  static live(): MockEventSource[] {
    return this.instances.filter((es) => !es.closed);
  }
}

const { useProcessStream } = await import('../hooks/useProcessStream.js');

const node = (fields: Record<string, unknown>) => ({
  name: String(fields._id), status: { state: 'done' }, progress: { percent: null },
  cancellable: false, order: 0, metadata: {}, ...fields,
});

// The stream of a record is its root's tree: a launch's is its factory's.
const FACTORY = node({ _id: 'fac-oid', processId: 'fac', parentId: null, depth: 0, taskFactory: true });
const LAUNCH = node({
  _id: 'launch-oid', processId: 'fac_0a1b2c3d', parentId: 'fac-oid', depth: 1,
  metadata: { taskFactory: 'fac' },
});
const OTHER_LAUNCH = node({
  _id: 'other-oid', processId: 'fac_99887766', parentId: 'fac-oid', depth: 1,
  metadata: { taskFactory: 'fac' },
});

let fetchMock: ReturnType<typeof vi.fn>;

/** Lets the probe's promise chain run. */
async function settle() {
  await act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
  });
}

beforeEach(() => {
  MockEventSource.instances = [];
  (globalThis as any).EventSource = MockEventSource;
  fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
  (globalThis as any).fetch = fetchMock;
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useProcessStream: the process gone from its tree', () => {
  it.each([
    ['its _id', 'launch-oid'],
    ['its processId', 'fac_0a1b2c3d'],
  ])('an update whose tree no longer holds the process (asked by %s) reports it not found, and stops', async (_how, id) => {
    const { result } = renderHook(() => useProcessStream(id));
    await settle();
    const [es] = MockEventSource.live();
    expect(es.url).toContain(`/api/processes/${id}/tree/stream`);

    act(() => es.emit({ type: 'update', processes: [FACTORY, LAUNCH, OTHER_LAUNCH] }));
    expect(result.current.processNotFound).toBe(false);
    expect(result.current.tree?._id).toBe('fac-oid');

    // Deleted while the stream is open: the factory's tree goes on without it.
    act(() => es.emit({ type: 'update', processes: [FACTORY, OTHER_LAUNCH] }));
    expect(result.current.processNotFound).toBe(true);
    expect(result.current.connected).toBe(false);
    expect(es.closed).toBe(true);

    // Terminal, as a probe's 404: nothing is probed or opened again.
    const probes = fetchMock.mock.calls.length;
    await act(async () => {
      vi.runAllTimers();
      await Promise.resolve();
    });
    await settle();
    expect(fetchMock.mock.calls.length).toBe(probes);
    expect(MockEventSource.live()).toHaveLength(0);
  });

  it('a whole tree gone (an empty update) reports the process not found', async () => {
    const { result } = renderHook(() => useProcessStream('fac-oid'));
    await settle();
    const [es] = MockEventSource.live();
    act(() => es.emit({ type: 'update', processes: [FACTORY] }));
    expect(result.current.processNotFound).toBe(false);
    act(() => es.emit({ type: 'update', processes: [] }));
    expect(result.current.processNotFound).toBe(true);
    expect(es.closed).toBe(true);
  });
});
