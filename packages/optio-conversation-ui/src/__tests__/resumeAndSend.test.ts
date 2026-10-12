import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { notification } from 'antd';
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
vi.mock('antd', async (importOriginal) => {
  const actual = await importOriginal<typeof import('antd')>();
  return { ...actual, notification: { ...actual.notification, warning: vi.fn() } };
});
const warn = vi.mocked(notification.warning);

const TARGET: ResumeSendTarget = {
  processId: 'p1',
  apiBaseUrl: 'https://app.test',
  database: 'db',
  prefix: 'gm',
  widgetProxyUrl: '/api/widget/db/gm/p1/',
};
// pollMs 0; timeoutMs and graceMs are product timeouts, kept out of reach
// except where a test is about them.
const FAST = { pollMs: 0, timeoutMs: 600_000, graceMs: 600_000 };
const HANG = { timeout: 60_000 };

const LAUNCH = 'https://app.test/api/processes/p1/launch?database=db&prefix=gm';
const GET = 'https://app.test/api/processes/p1?database=db&prefix=gm';
const SEND = '/api/widget/db/gm/p1/send';

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const proc = (state: string) => json(200, { _id: 'p1', status: { state } });

// Answers by URL: `launch` once (by default the process as the engine left it:
// scheduled), then the scripted process states and send answers in order (the
// last one repeats).
function script(opts: { launch?: Response; states: string[]; sends?: Array<Response | Error> }) {
  const states = [...opts.states];
  const sends = [...(opts.sends ?? [json(200, { ok: true, id: 'q1', queued: false })])];
  const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
    if (url === LAUNCH) return opts.launch ?? proc('scheduled');
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

// A mounted view of the process: failures stay in the store for it to show.
const view = () => renderHook(() => useResumeSend('p1'));

async function settled(v: ReturnType<typeof view>) {
  await vi.waitFor(() => expect(v.result.current?.phase).not.toBe('resuming'), HANG);
  return v.result.current;
}

beforeEach(() => {
  resetResumeSends();
  warn.mockClear();
});
afterEach(() => {
  // End every wait before the real fetch comes back.
  resetResumeSends();
  vi.unstubAllGlobals();
});

describe('startResumeSend', () => {
  it('launches in resume mode, waits until live, then posts the message once', async () => {
    const f = script({ states: ['scheduled', 'running'] });
    const v = view();
    let outcome: unknown;
    await act(async () => {
      outcome = await startResumeSend(TARGET, 'hello again', FAST);
    });
    expect(outcome).toEqual({ ok: true });
    const [, init] = calls(f, LAUNCH)[0];
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toEqual({ resume: true, sessionId: 'sess-1' });
    await vi.waitFor(() => expect(calls(f, SEND)).toHaveLength(1), HANG);
    expect(JSON.parse(String(calls(f, SEND)[0][1]?.body))).toEqual({ text: 'hello again' });
    expect(await settled(v)).toBeUndefined();
    expect(warn).not.toHaveBeenCalled();
  });

  it('shows resuming with the text while it waits', async () => {
    script({ states: ['scheduled'] });
    const v = view();
    await act(async () => {
      await startResumeSend(TARGET, 'queued text', { ...FAST, pollMs: 20 });
    });
    expect(v.result.current).toEqual({ phase: 'resuming', text: 'queued text' });
  });

  it('a refused resume returns its message and leaves nothing waiting', async () => {
    const f = script({ launch: json(409, { reason: 'already-running', message: 'Process is already running' }), states: ['running'] });
    const v = view();
    let outcome: unknown;
    await act(async () => {
      outcome = await startResumeSend(TARGET, 'hi', FAST);
    });
    expect(outcome).toEqual({ ok: false, message: 'Process is already running' });
    expect(v.result.current).toBeUndefined();
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
    const v = view();
    await startResumeSend(TARGET, 'hi', FAST);
    expect(await settled(v)).toBeUndefined();
    expect(calls(f, SEND)).toHaveLength(4);
  });

  it('a 504 is not retried: the request may have reached the listener', async () => {
    const f = script({ states: ['running'], sends: [json(504, {})] });
    const v = view();
    await startResumeSend(TARGET, 'once', FAST);
    expect(await settled(v)).toMatchObject({ phase: 'failed', text: 'once' });
    expect(calls(f, SEND)).toHaveLength(1);
  });

  it('a closed conversation (409) fails with the text kept', async () => {
    script({ states: ['running'], sends: [json(409, { ok: false, reason: 'closed' })] });
    const v = view();
    await startResumeSend(TARGET, 'keep me', FAST);
    expect(await settled(v)).toEqual({
      phase: 'failed', text: 'keep me', message: 'The session resumed, but the message could not be sent.',
    });
  });

  it('a send that throws is not retried and is reported as possibly delivered', async () => {
    const f = script({ states: ['running'], sends: [new TypeError('reset')] });
    const v = view();
    await startResumeSend(TARGET, 'once', FAST);
    expect(await settled(v)).toEqual({
      phase: 'failed',
      text: 'once',
      message: 'The connection broke off while sending it. Check the conversation before sending it again.',
      uncertain: true,
    });
    expect(calls(f, SEND)).toHaveLength(1);
  });

  it('the session stopping after the launch fails the send at once (no grace wait)', async () => {
    // The launch answered `scheduled`: the old suspend is gone, so the first
    // terminal state is the resumed run's own, even with a long grace period.
    const f = script({ states: ['failed'] });
    const v = view();
    await startResumeSend(TARGET, 'lost?', FAST);
    expect(await settled(v)).toEqual({
      phase: 'failed', text: 'lost?', message: 'The session stopped before the message could be sent.',
    });
    expect(calls(f, SEND)).toHaveLength(0);
  });

  it('without an active launch answer, the old suspended state is waited out within the grace', async () => {
    const f = script({ launch: json(200, {}), states: ['cancelled', 'cancelled', 'running'] });
    view();
    await startResumeSend(TARGET, 'hi', FAST);
    await vi.waitFor(() => expect(calls(f, SEND)).toHaveLength(1), HANG);
  });

  it('still terminal past the grace: the resume did not happen', async () => {
    script({ launch: json(200, {}), states: ['cancelled'] });
    const v = view();
    await startResumeSend(TARGET, 'hi', { pollMs: 5, timeoutMs: 600_000, graceMs: 20 });
    expect(await settled(v)).toMatchObject({
      phase: 'failed', message: 'The session stopped before the message could be sent.',
    });
  });

  it('gives up after the timeout', async () => {
    script({ states: ['scheduled'] });
    const v = view();
    await startResumeSend(TARGET, 'hi', { pollMs: 5, timeoutMs: 30, graceMs: 600_000 });
    expect(await settled(v)).toMatchObject({
      phase: 'failed', text: 'hi', message: 'The session did not become ready in time.',
    });
  });

  it('a vanished process (404) fails at once', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => (url === LAUNCH ? proc('scheduled') : json(404, { message: 'Not found' }))));
    const v = view();
    await startResumeSend(TARGET, 'hi', FAST);
    expect(await settled(v)).toMatchObject({ phase: 'failed', message: 'The process no longer exists.' });
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

describe('a failure with no view of the process mounted', () => {
  // The process page renders no widget for a failed process: the failure
  // goes to a notification with the text, and nothing stays in the store.
  it('raises a notification carrying the reason and the text', async () => {
    script({ states: ['failed'] });
    await startResumeSend(TARGET, 'please do X', FAST);
    await vi.waitFor(() => expect(warn).toHaveBeenCalledTimes(1), HANG);
    expect(warn.mock.calls[0][0]).toMatchObject({
      title: 'Your message was not sent: The session stopped before the message could be sent.',
      description: 'Your message: “please do X”',
      duration: 0,
    });
    expect(view().result.current).toBeUndefined();
  });

  it('says "may not have been sent" when the send broke off', async () => {
    script({ states: ['running'], sends: [new TypeError('reset')] });
    await startResumeSend(TARGET, 'once', FAST);
    await vi.waitFor(() => expect(warn).toHaveBeenCalledTimes(1), HANG);
    expect(String(warn.mock.calls[0][0].title)).toMatch(/^Your message may not have been sent: /);
  });

  it('a view mounted again counts: no notification while one shows it', async () => {
    script({ states: ['failed'] });
    const v = view();
    v.unmount();
    const v2 = view();
    await startResumeSend(TARGET, 'x', FAST);
    expect(await settled(v2)).toMatchObject({ phase: 'failed' });
    expect(warn).not.toHaveBeenCalled();
  });
});

describe('dismissResumeSend', () => {
  it('clears a failed entry but never a waiting one', async () => {
    script({ states: ['failed'] });
    const v = view();
    await startResumeSend(TARGET, 'x', FAST);
    await settled(v);
    act(() => dismissResumeSend('p1'));
    expect(v.result.current).toBeUndefined();

    script({ states: ['scheduled'] });
    await act(async () => {
      await startResumeSend(TARGET, 'y', { ...FAST, pollMs: 20 });
    });
    act(() => dismissResumeSend('p1'));
    expect(v.result.current?.phase).toBe('resuming');
  });
});
