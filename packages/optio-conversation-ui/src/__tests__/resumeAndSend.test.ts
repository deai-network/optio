import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import {
  startResumeSend,
  useResumeSend,
  dismissResumeSend,
  resetResumeSends,
  type ResumeSendTarget,
} from '../claudecode/resumeAndSend.js';

// "Send to resume": resume the suspended process, wait until it is live, then
// post the message to its conversation listener.

vi.mock('optio-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('optio-ui')>();
  return { ...actual, getSessionId: () => 'sess-1' };
});

const TARGET: ResumeSendTarget = {
  processId: 'p1',
  apiBaseUrl: 'https://app.test',
  database: 'db',
  prefix: 'gm',
  widgetProxyUrl: '/api/widget/db/gm/p1/',
};
const FAST = { pollMs: 0, timeoutMs: 2000, graceMs: 50 };

const LAUNCH = 'https://app.test/api/processes/p1/launch?database=db&prefix=gm';
const GET = 'https://app.test/api/processes/p1?database=db&prefix=gm';
const SEND = '/api/widget/db/gm/p1/send';

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const proc = (state: string) => json(200, { _id: 'p1', status: { state } });

// Answers by URL: `launch` once, then the scripted process states and send
// answers in order (the last one repeats).
function script(opts: { launch?: Response; states: string[]; sends?: Array<Response | Error> }) {
  const states = [...opts.states];
  const sends = [...(opts.sends ?? [json(200, { ok: true, id: 'q1', queued: false })])];
  const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
    if (url === LAUNCH) return opts.launch ?? json(200, { _id: 'p1' });
    if (url === GET) return proc(states.length > 1 ? states.shift()! : states[0]);
    if (url === SEND) {
      const next = sends.length > 1 ? sends.shift()! : sends[0];
      if (next instanceof Error) throw next;
      return next.clone();
    }
    throw new Error(`unexpected ${url}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const calls = (m: ReturnType<typeof vi.fn>, url: string) => m.mock.calls.filter((c) => c[0] === url);

async function settle(processId = 'p1') {
  await vi.waitFor(() => {
    const { result } = renderHook(() => useResumeSend(processId));
    expect(result.current?.phase).not.toBe('resuming');
  });
}

beforeEach(() => resetResumeSends());
afterEach(() => vi.unstubAllGlobals());

describe('startResumeSend', () => {
  it('launches in resume mode, waits until live, then posts the message once', async () => {
    const f = script({ states: ['cancelled', 'scheduled', 'running'] });
    const { result } = renderHook(() => useResumeSend('p1'));
    let outcome: unknown;
    await act(async () => {
      outcome = await startResumeSend(TARGET, 'hello again', FAST);
    });
    expect(outcome).toEqual({ ok: true });
    const [, init] = calls(f, LAUNCH)[0];
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toEqual({ resume: true, sessionId: 'sess-1' });
    await vi.waitFor(() => expect(calls(f, SEND)).toHaveLength(1));
    expect(JSON.parse(String(calls(f, SEND)[0][1]?.body))).toEqual({ text: 'hello again' });
    await vi.waitFor(() => expect(result.current).toBeUndefined());
  });

  it('shows resuming with the text while it waits', async () => {
    script({ states: ['scheduled'] });
    const { result } = renderHook(() => useResumeSend('p1'));
    await act(async () => {
      await startResumeSend(TARGET, 'queued text', { ...FAST, pollMs: 20 });
    });
    expect(result.current).toEqual({ phase: 'resuming', text: 'queued text' });
  });

  it('a refused resume returns its message and leaves nothing waiting', async () => {
    const f = script({ launch: json(409, { reason: 'already-running', message: 'Process is already running' }), states: ['running'] });
    const { result } = renderHook(() => useResumeSend('p1'));
    let outcome: unknown;
    await act(async () => {
      outcome = await startResumeSend(TARGET, 'hi', FAST);
    });
    expect(outcome).toEqual({ ok: false, message: 'Process is already running' });
    expect(result.current).toBeUndefined();
    expect(calls(f, GET)).toHaveLength(0);
  });

  it('a resume request that cannot reach the API is refused with a generic message', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('network'); }));
    await expect(startResumeSend(TARGET, 'hi', FAST)).resolves.toEqual({ ok: false, message: 'Could not resume the session.' });
  });

  it('retries the send while the listener is not registered yet (404, 502, 503)', async () => {
    const f = script({
      states: ['running'],
      sends: [json(404, {}), json(502, {}), json(503, {}), json(200, { ok: true })],
    });
    await startResumeSend(TARGET, 'hi', FAST);
    await vi.waitFor(() => expect(calls(f, SEND)).toHaveLength(4));
    await settle();
    expect(renderHook(() => useResumeSend('p1')).result.current).toBeUndefined();
  });

  it('a closed conversation (409) fails with the text kept', async () => {
    script({ states: ['running'], sends: [json(409, { ok: false, reason: 'closed' })] });
    await startResumeSend(TARGET, 'keep me', FAST);
    await settle();
    expect(renderHook(() => useResumeSend('p1')).result.current).toEqual({
      phase: 'failed', text: 'keep me', message: 'The session resumed, but the message could not be sent.',
    });
  });

  it('a send that throws is not retried (it may have been delivered)', async () => {
    const f = script({ states: ['running'], sends: [new TypeError('reset')] });
    await startResumeSend(TARGET, 'once', FAST);
    await settle();
    expect(calls(f, SEND)).toHaveLength(1);
    expect(renderHook(() => useResumeSend('p1')).result.current?.phase).toBe('failed');
  });

  it('the session stopping after it left the suspend fails the send', async () => {
    const f = script({ states: ['scheduled', 'failed'] });
    await startResumeSend(TARGET, 'lost?', FAST);
    await settle();
    expect(calls(f, SEND)).toHaveLength(0);
    expect(renderHook(() => useResumeSend('p1')).result.current).toEqual({
      phase: 'failed', text: 'lost?', message: 'The session stopped before the message could be sent.',
    });
  });

  it('the old suspended state right after the launch is waited out, within the grace', async () => {
    const f = script({ states: ['cancelled', 'cancelled', 'running'] });
    await startResumeSend(TARGET, 'hi', { ...FAST, graceMs: 10_000 });
    await vi.waitFor(() => expect(calls(f, SEND)).toHaveLength(1));
  });

  it('still terminal past the grace: the resume did not happen', async () => {
    script({ states: ['cancelled'] });
    await startResumeSend(TARGET, 'hi', { pollMs: 5, timeoutMs: 2000, graceMs: 20 });
    await settle();
    expect(renderHook(() => useResumeSend('p1')).result.current).toMatchObject({
      phase: 'failed', message: 'The session stopped before the message could be sent.',
    });
  });

  it('gives up after the timeout', async () => {
    script({ states: ['scheduled'] });
    await startResumeSend(TARGET, 'hi', { pollMs: 5, timeoutMs: 30, graceMs: 10 });
    await settle();
    expect(renderHook(() => useResumeSend('p1')).result.current).toMatchObject({
      phase: 'failed', text: 'hi', message: 'The session did not become ready in time.',
    });
  });

  it('a vanished process (404) fails at once', async () => {
    const f = vi.fn(async (url: string) => (url === LAUNCH ? json(200, {}) : json(404, { message: 'Not found' })));
    vi.stubGlobal('fetch', f);
    await startResumeSend(TARGET, 'hi', FAST);
    await settle();
    expect(renderHook(() => useResumeSend('p1')).result.current).toMatchObject({ phase: 'failed', message: 'The process no longer exists.' });
  });

  it('a second send while one waits is refused without a second launch', async () => {
    const f = script({ states: ['scheduled'] });
    await startResumeSend(TARGET, 'first', { ...FAST, pollMs: 20 });
    await expect(startResumeSend(TARGET, 'second', FAST)).resolves.toEqual({
      ok: false, message: 'A message is already waiting for this session to resume.',
    });
    expect(calls(f, LAUNCH)).toHaveLength(1);
  });

  it('omits database from the query when there is none', async () => {
    const f = vi.fn(async (_url: string) => json(409, { message: 'no' }));
    vi.stubGlobal('fetch', f);
    await startResumeSend({ ...TARGET, database: undefined }, 'hi', FAST);
    expect(f.mock.calls[0][0]).toBe('https://app.test/api/processes/p1/launch?prefix=gm');
  });
});

describe('dismissResumeSend', () => {
  it('clears a failed entry but never a waiting one', async () => {
    script({ states: ['scheduled', 'failed'] });
    await startResumeSend(TARGET, 'x', FAST);
    await settle();
    act(() => dismissResumeSend('p1'));
    expect(renderHook(() => useResumeSend('p1')).result.current).toBeUndefined();

    script({ states: ['scheduled'] });
    await startResumeSend(TARGET, 'y', { ...FAST, pollMs: 20 });
    act(() => dismissResumeSend('p1'));
    expect(renderHook(() => useResumeSend('p1')).result.current?.phase).toBe('resuming');
  });
});
