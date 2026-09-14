// Real Claude Code stream-json (CLI 2.1.270, claude-sonnet-5), same fixtures
// and trimming as claudecode-steering-real-wire.test.ts, but rendered through
// ConversationView. final-review I1: every `result` clears `busy`, including
// the aborted result an interrupt produces, but the CLI runs the queued
// message as the very next turn with no running/idle event in between (about
// 1.6-1.7 s, s3/s5). Send now must track `busy` -- the same condition that
// makes the busy input bar steerable -- so it disappears in that gap instead
// of offering a click that POSTs /steer with empty text and interrupts the
// turn that is delivering the queue.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { ConversationView } from '../ConversationView.js';
import { initialChatState, reduceEvent } from '../claudecode/events.js';
import type { ChatState } from '../chat.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
function load(name: string): any[] {
  return fs
    .readFileSync(path.join(HERE, 'fixtures', name), 'utf-8')
    .trim()
    .split('\n')
    .map((line) => JSON.parse(line));
}
const NOW = Date.parse('2026-09-13T17:49:00.000Z');
// Replay order (no stream_event), as the listener's persisted buffer holds it.
function replay(events: any[], now = NOW): ChatState {
  return events.reduce((s, ev, i) => (ev.type === 'stream_event' ? s : reduceEvent(s, ev, i + 1, now)), initialChatState);
}

function renderQueuedBubble(state: ChatState): ReturnType<typeof render> {
  return render(
    (
      <ConfigProvider>
        <ConversationView
          state={state}
          busy={state.busy}
          closed={false}
          toolVerbosity="verbose"
          thinkingVerbosity="visible"
          showFileUpload={false}
          maxUploadBytes={10_000_000}
          fileDownload={false}
          onSend={vi.fn(async () => true)}
          onSteer={vi.fn(async () => true)}
          onInterrupt={vi.fn()}
          onPermission={vi.fn()}
          onFileDownload={vi.fn()}
        />
      </ConfigProvider>
    ) as ReactElement,
  );
}

describe('claudecode real wire render: Send now tracks busy (final-review I1)', () => {
  const events = load('claudecode-steer-then-interrupt.jsonl');
  // Right after the operator's steer is queued, mid-turn (the long Bash call
  // is still running; no result yet).
  const queuedAt = events.findIndex((e) => e.type === 'x-optio-queued') + 1;
  // Through the interrupt's own aborted result -- the point Task 3/the
  // real-wire suite already pins as "the steer waits below the interrupt
  // row". The CLI's follow-up turn (the queued message's echo) has not
  // arrived yet.
  const resultAt = events.findIndex((e) => e.type === 'result') + 1;

  it('while the turn runs, the queued bubble is busy and offers Send now', () => {
    const state = replay(events.slice(0, queuedAt));
    expect(state.busy).toBe(true);
    expect(state.items.some((i) => i.kind === 'user' && 'queued' in i && i.queued)).toBe(true);
    renderQueuedBubble(state);
    expect(screen.getByTestId('queued-bubble')).toBeTruthy();
    expect(screen.getByTestId('queued-send-now')).toBeTruthy();
  });

  it("after the interrupt's result, before the queued message's echo, the state is not busy and Send now is hidden", () => {
    const state = replay(events.slice(0, resultAt));
    expect(state.busy).toBe(false);
    expect(state.items.some((i) => i.kind === 'user' && 'queued' in i && i.queued)).toBe(true);
    renderQueuedBubble(state);
    expect(screen.getByTestId('queued-bubble')).toBeTruthy();
    expect(screen.queryByTestId('queued-send-now')).toBeNull();
  });
});
