import { useSyncExternalStore } from 'react';
import { getSessionId, isTerminal, isWidgetLive } from 'optio-ui';

// "Send to resume" for a suspended claudecode session: the suspended replay's
// composer sends a message by resuming the session first. startResumeSend
// launches the process in resume mode, then a task that does not depend on any
// mounted component waits until the session is live and posts the message to
// its conversation listener, the same /send the live composer uses.
//
// Everything runs in the browser on purpose: the resume request goes to the
// engine over the Redis RPC, and the message must not ride along with it (no
// conversation content in Redis). The text lives only in this module's memory
// until it is sent; a page reload before that loses it.

export type ResumeSendState =
  // The resume was accepted; the message waits for the session to go live.
  | { phase: 'resuming'; text: string }
  // The message could not be sent after the resume was accepted.
  | { phase: 'failed'; text: string; message: string };

export interface ResumeSendTarget {
  processId: string;
  // WidgetProps' apiBaseUrl / database / prefix / widgetProxyUrl.
  apiBaseUrl: string;
  database?: string;
  prefix: string;
  widgetProxyUrl: string;
}

export interface ResumeSendTiming {
  // How often the process document is read while waiting.
  pollMs: number;
  // Give up when the session is not live this long after the resume.
  timeoutMs: number;
  // A terminal state right after the launch may still be the old suspend
  // (the engine has not marked the launch yet); only past this long does it
  // count as a failed resume.
  graceMs: number;
}

export const DEFAULT_RESUME_SEND_TIMING: ResumeSendTiming = {
  pollMs: 1000,
  timeoutMs: 10 * 60_000,
  graceMs: 30_000,
};

const COULD_NOT_RESUME = 'Could not resume the session.';
const ALREADY_WAITING = 'A message is already waiting for this session to resume.';
const NOT_SENT = 'The session resumed, but the message could not be sent.';
const STOPPED = 'The session stopped before the message could be sent.';
const TIMED_OUT = 'The session did not become ready in time.';
const GONE = 'The process no longer exists.';

// The widget proxy's answers while the resumed listener is not registered or
// not reachable yet: try again on the next poll.
const NOT_READY = new Set([404, 502, 503, 504]);

const states = new Map<string, ResumeSendState>();
const listeners = new Set<() => void>();

function put(processId: string, state: ResumeSendState | undefined): void {
  if (state) states.set(processId, state);
  else states.delete(processId);
  for (const l of listeners) l();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** The pending or failed resume-send of a process, if any. */
export function useResumeSend(processId: string | undefined): ResumeSendState | undefined {
  const read = () => (processId ? states.get(processId) : undefined);
  return useSyncExternalStore(subscribe, read, read);
}

/** Forget a failed resume-send (its notice was closed, or a new send began). */
export function dismissResumeSend(processId: string): void {
  if (states.get(processId)?.phase === 'failed') put(processId, undefined);
}

function instanceQuery(t: ResumeSendTarget): string {
  const q = new URLSearchParams();
  if (t.database) q.set('database', t.database);
  q.set('prefix', t.prefix);
  return q.toString();
}

async function messageOf(resp: Response, fallback: string): Promise<string> {
  try {
    const body: unknown = await resp.json();
    const message = (body as { message?: unknown } | null)?.message;
    if (typeof message === 'string' && message) return message;
  } catch {
    // not JSON
  }
  return fallback;
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Resume the process and send `text` once it is live. Resolves after the
 * resume request: `{ok: false, message}` when it was refused (nothing waits
 * then), `{ok: true}` when it was accepted and the message waits in the
 * background (useResumeSend shows `resuming`, then nothing once sent, or
 * `failed` with the text kept).
 */
export async function startResumeSend(
  target: ResumeSendTarget,
  text: string,
  timing: ResumeSendTiming = DEFAULT_RESUME_SEND_TIMING,
): Promise<{ ok: true } | { ok: false; message: string }> {
  const id = target.processId;
  if (states.get(id)?.phase === 'resuming') return { ok: false, message: ALREADY_WAITING };
  const entry: ResumeSendState = { phase: 'resuming', text };
  put(id, entry);
  let resp: Response;
  try {
    resp = await fetch(
      `${target.apiBaseUrl}/api/processes/${encodeURIComponent(id)}/launch?${instanceQuery(target)}`,
      {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ resume: true, sessionId: getSessionId() }),
      },
    );
  } catch {
    put(id, undefined);
    return { ok: false, message: COULD_NOT_RESUME };
  }
  if (!resp.ok) {
    const message = await messageOf(resp, COULD_NOT_RESUME);
    put(id, undefined);
    return { ok: false, message };
  }
  void deliverWhenLive(target, entry, text, timing);
  return { ok: true };
}

async function deliverWhenLive(
  t: ResumeSendTarget, entry: ResumeSendState, text: string, timing: ResumeSendTiming,
): Promise<void> {
  const id = t.processId;
  // The wait ends quietly once its entry is no longer the current one
  // (resetResumeSends).
  const current = () => states.get(id) === entry;
  const fail = (message: string) => {
    if (current()) put(id, { phase: 'failed', text, message });
  };
  const started = Date.now();
  // Set once the process has been seen out of its old terminal state.
  let launched = false;
  while (Date.now() - started < timing.timeoutMs) {
    await sleep(timing.pollMs);
    if (!current()) return;
    let proc: unknown;
    try {
      const r = await fetch(`${t.apiBaseUrl}/api/processes/${encodeURIComponent(id)}?${instanceQuery(t)}`);
      if (r.status === 404) return fail(GONE);
      if (!r.ok) continue;
      proc = await r.json();
    } catch {
      continue;
    }
    if (isWidgetLive(proc as any)) {
      launched = true;
      let sent: Response;
      try {
        sent = await fetch(`${t.widgetProxyUrl}send`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ text }),
        });
      } catch {
        // The request may have reached the listener: sending it again could
        // deliver it twice, so report it instead.
        return fail(NOT_SENT);
      }
      if (sent.ok) {
        if (current()) put(id, undefined);
        return;
      }
      if (NOT_READY.has(sent.status)) continue;
      // 409: the conversation closed; 400/401/403: refused.
      return fail(await messageOf(sent, NOT_SENT));
    }
    if (isTerminal(proc as any)) {
      if (launched || Date.now() - started > timing.graceMs) return fail(STOPPED);
      continue;
    }
    launched = true;
  }
  fail(TIMED_OUT);
}

// Tests only: drop every pending and failed entry, ending their waits.
export function resetResumeSends(): void {
  states.clear();
  for (const l of listeners) l();
}
