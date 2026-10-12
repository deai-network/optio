import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConversationView, type ConversationViewProps } from '../ConversationView.js';
import { initialChatState, reduceEvent } from '../claudecode/events.js';
import type { ChatItem } from '../chat.js';
import { transcriptKeys } from '../transcriptKeys.js';

// One event can yield several transcript rows: a prompt sent with an upload
// ("System: upload received…" lines, then the text) becomes the muted
// "Attached" row and the prompt's bubble, both carrying the event's seq
// (since 361671b9). Rows were keyed by seq alone, so React warned about two
// children with the same key (owner, 2026-10-12) and could mix them up.

describe('transcriptKeys', () => {
  it('keys a row by its kind and seq', () => {
    const items = [
      { kind: 'activity', text: 'a', seq: 5 },
      { kind: 'user', text: 'b', seq: 5 },
      { kind: 'assistant', text: 'c', pending: false, seq: 6, msgId: 'm' },
    ] as ChatItem[];
    expect(transcriptKeys(items)).toEqual(['activity-5', 'user-5', 'assistant-6']);
  });

  it('numbers the rows of one kind that share a seq', () => {
    const items = [
      { kind: 'thinking', text: 'a', seq: 7 },
      { kind: 'thinking', text: 'b', seq: 7 },
      { kind: 'thinking', text: 'c', seq: 7 },
    ] as ChatItem[];
    expect(transcriptKeys(items)).toEqual(['thinking-7', 'thinking-7-1', 'thinking-7-2']);
  });
});

describe('a prompt sent with an upload', () => {
  afterEach(() => vi.restoreAllMocks());

  it('renders its Attached row and its bubble without a duplicate-key warning', () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    const state = reduceEvent(initialChatState, {
      type: 'user',
      isReplay: true,
      message: { role: 'user', content: [{ type: 'text', text: 'System: upload received, stored in uploads/anything.md\n\ndo you get this file?' }] },
    }, 1541);
    expect(state.items.map((i) => [i.kind, i.seq])).toEqual([['activity', 1541], ['user', 1541]]);

    const props: ConversationViewProps = {
      state, closed: false, busy: false, toolVerbosity: 'verbose', thinkingVerbosity: 'visible',
      showFileUpload: false, maxUploadBytes: 1, fileDownload: false,
      onSend: vi.fn(async () => true), onInterrupt: vi.fn(), onPermission: vi.fn(), onFileDownload: vi.fn(),
    };
    render(<ConversationView {...props} />);
    expect(screen.getByText(/Attached: anything\.md/)).toBeTruthy();
    expect(screen.getByText('do you get this file?')).toBeTruthy();
    const dup = errors.mock.calls.filter((c) => String(c[0]).includes('two children with the same key'));
    expect(dup).toEqual([]);
  });
});
