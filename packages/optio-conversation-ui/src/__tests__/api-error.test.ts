import { describe, it, expect } from 'vitest';
import { explainApiError } from '../apiError.js';
import { initialChatState } from '../chat.js';
import type { ChatState } from '../chat.js';
import { reduceEvent as reduceClaudecodeEvent } from '../claudecode/events.js';

describe('explainApiError', () => {
  it('explains a content-filter block with a fresh-conversation hint', () => {
    const s = explainApiError('API Error: 400 Output blocked by content filtering policy', 400);
    expect(s).toMatch(/safety filter/i);
    expect(s).toMatch(/fresh conversation/i);
  });

  it('maps rate limit and overloaded by status', () => {
    expect(explainApiError('x', 429)).toMatch(/rate-limited/i);
    expect(explainApiError('x', 529)).toMatch(/overloaded/i);
  });

  it('falls back to the raw text for unknown errors', () => {
    expect(explainApiError('some weird error', null)).toBe('some weird error');
  });
});

describe('claudecode result.is_error -> error item', () => {
  it('renders an explained error item, not a plain assistant bubble', () => {
    const ev = {
      type: 'result',
      is_error: true,
      api_error_status: 400,
      result: 'API Error: 400 Output blocked by content filtering policy',
    };
    const s = reduceClaudecodeEvent(initialChatState, ev, 1);
    const last = s.items[s.items.length - 1];
    expect(last.kind).toBe('error');
    expect(last.kind === 'error' && last.text).toMatch(/fresh conversation/i);
    expect(s.busy).toBe(false);
  });
});

// -- fix 2: a CLI-unreachable API error rendered twice -----------------------
//
// Real wire sequence (Claude Code CLI 2.1.270) when the API is unreachable:
// a few system/api_retry events, then a synthetic `assistant` message whose
// `message.model` is "<synthetic>" and whose event carries `error:
// "server_error"`, then a `result` (subtype success, is_error true,
// terminal_reason api_error) carrying the same text. Both the synthetic
// message and the result used to render the error text (events.ts:665-687
// and 741-744), and the synthetic bubble was left pending forever.

const ERROR_TEXT = "API Error: Can't reach the API server — check your internet or DNS (EAI_AGAIN)";

const user = (text: string) => ({ type: 'user', message: { role: 'user', content: [{ type: 'text', text }] } });
const apiRetry = () => ({ type: 'system', subtype: 'api_retry' });
const syntheticApiError = (text: string) => ({
  type: 'assistant',
  message: { role: 'assistant', model: '<synthetic>', content: [{ type: 'text', text }] },
  error: 'server_error',
});
const apiErrorResult = (text: string) => ({
  type: 'result',
  subtype: 'success',
  is_error: true,
  terminal_reason: 'api_error',
  result: text,
});
const assistantText = (text: string) => ({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text }] } });
const delta = (text: string) => ({ type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'text_delta', text } } });

function run(events: any[]): ChatState {
  return events.reduce((s, ev, i) => reduceClaudecodeEvent(s, ev, i + 1), initialChatState);
}

function hasPending(s: ChatState): boolean {
  return s.items.some((i) => i.kind === 'assistant' && i.pending);
}

describe('claudecode API error renders once (fix 2)', () => {
  it('a synthetic CLI error message plus its is_error result renders exactly one error item and no assistant bubble', () => {
    const s = run([
      user('are you there'),
      apiRetry(),
      apiRetry(),
      syntheticApiError(ERROR_TEXT),
      apiErrorResult(ERROR_TEXT),
    ]);
    const errors = s.items.filter((i) => i.kind === 'error');
    const assistants = s.items.filter((i) => i.kind === 'assistant');
    expect(errors).toHaveLength(1);
    expect(errors[0].kind === 'error' && errors[0].text).toBe(ERROR_TEXT);
    expect(assistants).toHaveLength(0);
    expect(hasPending(s)).toBe(false);
    expect(s.busy).toBe(false);
  });

  it('real assistant text streamed via deltas before the API error stays as a finalized bubble; the error item follows (live)', () => {
    const s = run([
      user('are you there'),
      delta('Work'),
      delta('ing on it'),
      apiRetry(),
      syntheticApiError(ERROR_TEXT),
      apiErrorResult(ERROR_TEXT),
    ]);
    const assistants = s.items.filter((i) => i.kind === 'assistant');
    expect(assistants).toHaveLength(1);
    expect(assistants[0].kind === 'assistant' && assistants[0].text).toBe('Working on it');
    expect(assistants[0].kind === 'assistant' && assistants[0].pending).toBe(false);
    const errors = s.items.filter((i) => i.kind === 'error');
    expect(errors).toHaveLength(1);
    expect(errors[0].kind === 'error' && errors[0].text).toBe(ERROR_TEXT);
    // The bubble comes before the error item.
    expect(s.items.map((i) => i.kind)).toEqual(['user', 'assistant', 'error']);
    expect(hasPending(s)).toBe(false);
  });

  it('the same turn replayed without deltas (final text block only) renders identically', () => {
    const s = run([
      user('are you there'),
      assistantText('Working on it'),
      apiRetry(),
      syntheticApiError(ERROR_TEXT),
      apiErrorResult(ERROR_TEXT),
    ]);
    const assistants = s.items.filter((i) => i.kind === 'assistant');
    expect(assistants).toHaveLength(1);
    expect(assistants[0].kind === 'assistant' && assistants[0].text).toBe('Working on it');
    expect(assistants[0].kind === 'assistant' && assistants[0].pending).toBe(false);
    const errors = s.items.filter((i) => i.kind === 'error');
    expect(errors).toHaveLength(1);
    expect(s.items.map((i) => i.kind)).toEqual(['user', 'assistant', 'error']);
    expect(hasPending(s)).toBe(false);
  });
});
