import { describe, it, expect } from 'vitest';
import { explainApiError } from '../apiError.js';
import { initialChatState } from '../chat.js';
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
