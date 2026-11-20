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

const { useProcessStream, DEFAULT_MAX_LOG_ENTRIES } = await import('../hooks/useProcessStream.js');

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

  it('a hex _id asked for in upper case names its record (optio-api resolves it in any case)', async () => {
    const root = node({ _id: '64b0c0ffee00000000000000', processId: 'task', parentId: null, depth: 0 });
    const { result } = renderHook(() => useProcessStream('64B0C0FFEE00000000000000'));
    await settle();
    const [es] = MockEventSource.live();
    act(() => es.emit({ type: 'update', processes: [root] }));
    expect(result.current.processNotFound).toBe(false);
    expect(es.closed).toBe(false);
    expect(result.current.tree?._id).toBe('64b0c0ffee00000000000000');
    act(() => es.emit({ type: 'update', processes: [] }));
    expect(result.current.processNotFound).toBe(true);
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

describe('useProcessStream: the log buffer keeps the most recent entries', () => {
  const logEntry = (n: number) => ({
    timestamp: new Date(n * 1000).toISOString(), level: 'info', message: `line ${n}`,
    processId: 'fac-oid', processLabel: 'fac',
  });
  const entries = (from: number, count: number) =>
    Array.from({ length: count }, (_, i) => logEntry(from + i));
  const messages = (logs: { message: string }[]) => logs.map((e) => e.message);

  async function openStream(options?: { maxLogEntries?: number }) {
    const hook = renderHook(() => useProcessStream('fac-oid', 10, options));
    await settle();
    const [es] = MockEventSource.live();
    return { ...hook, es };
  }

  it('the default is 2,000 entries', () => {
    expect(DEFAULT_MAX_LOG_ENTRIES).toBe(2000);
  });

  it('under the cap: every entry, in order, none dropped', async () => {
    const { result, es } = await openStream({ maxLogEntries: 5 });
    act(() => es.emit({ type: 'log', entries: entries(0, 2) }));
    act(() => es.emit({ type: 'log', entries: entries(2, 3) }));
    expect(messages(result.current.logs)).toEqual(['line 0', 'line 1', 'line 2', 'line 3', 'line 4']);
    expect(result.current.droppedLogCount).toBe(0);
  });

  it('more than the cap in several batches: exactly the last N, in order; the dropped count is right', async () => {
    const { result, es } = await openStream({ maxLogEntries: 5 });
    act(() => es.emit({ type: 'log', entries: entries(0, 3) }));
    act(() => es.emit({ type: 'log', entries: entries(3, 4) }));
    expect(messages(result.current.logs)).toEqual(['line 2', 'line 3', 'line 4', 'line 5', 'line 6']);
    expect(result.current.droppedLogCount).toBe(2);
    act(() => es.emit({ type: 'log', entries: entries(7, 2) }));
    expect(messages(result.current.logs)).toEqual(['line 4', 'line 5', 'line 6', 'line 7', 'line 8']);
    expect(result.current.droppedLogCount).toBe(4);
  });

  it('one batch bigger than the cap keeps its own last N', async () => {
    const { result, es } = await openStream({ maxLogEntries: 5 });
    act(() => es.emit({ type: 'log', entries: entries(0, 2) }));
    act(() => es.emit({ type: 'log', entries: entries(2, 12) }));
    expect(messages(result.current.logs)).toEqual(['line 9', 'line 10', 'line 11', 'line 12', 'line 13']);
    expect(result.current.droppedLogCount).toBe(9);
  });

  it('without the option: the default cap (a backlog of 66,908 entries keeps the last 2,000)', async () => {
    const { result, es } = await openStream();
    act(() => es.emit({ type: 'log', entries: entries(0, 66_900) }));
    act(() => es.emit({ type: 'log', entries: entries(66_900, 8) }));
    expect(result.current.logs).toHaveLength(DEFAULT_MAX_LOG_ENTRIES);
    expect(result.current.logs[0].message).toBe('line 64908');
    expect(result.current.logs[DEFAULT_MAX_LOG_ENTRIES - 1].message).toBe('line 66907');
    expect(result.current.droppedLogCount).toBe(64_908);
  });

  it('a log-clear empties the buffer and resets the dropped count', async () => {
    const { result, es } = await openStream({ maxLogEntries: 3 });
    act(() => es.emit({ type: 'log', entries: entries(0, 5) }));
    expect(result.current.droppedLogCount).toBe(2);
    act(() => es.emit({ type: 'log-clear' }));
    expect(result.current.logs).toEqual([]);
    expect(result.current.droppedLogCount).toBe(0);
    act(() => es.emit({ type: 'log', entries: entries(5, 1) }));
    expect(messages(result.current.logs)).toEqual(['line 5']);
    expect(result.current.droppedLogCount).toBe(0);
  });
});
