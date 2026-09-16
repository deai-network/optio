// Fix 19 (owner rulings 2026-09-15, manual-test finding 6;
// docs/2026-09-15-steering-session-end-design.md): a session that ends
// mid-turn, and messages re-queued on resume, through the claudecode reducer
// and ConversationView. Live = every event the SSE tail delivers; replay =
// what the persisted buffer holds (no stream_event, never buffered; no
// x-optio-closed, which export_buffer drops).
import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { ConversationView } from '../ConversationView.js';
import { initialChatState, reduceEvent } from '../claudecode/events.js';
import { INTERRUPTED_SESSION_ENDED } from '../chat.js';
import type { ChatItem, ChatState } from '../chat.js';

const NOW = Date.parse('2026-09-15T10:00:00.000Z');
const START = Date.parse('2026-09-15T09:59:30.000Z');
const running = { type: 'system', subtype: 'session_state_changed', state: 'running' };
const idle = { type: 'system', subtype: 'session_state_changed', state: 'idle' };
const user = (text: string) => ({ type: 'user', message: { role: 'user', content: [{ type: 'text', text }] } });
const echoU = (uuid: string, text: string, timestamp: string) => ({
  type: 'user', message: { role: 'user', content: [{ type: 'text', text }] }, uuid, isReplay: true, timestamp,
});
const messageStart = (id: string) => ({ type: 'x-optio-message-start', id, ts: START });
const streamStart = (id: string) => ({ type: 'stream_event', event: { type: 'message_start', message: { id, content: [] } } });
const delta = (text: string) => ({ type: 'stream_event', event: { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text } } });
const thinkingDelta = (thinking: string) => ({ type: 'stream_event', event: { type: 'content_block_delta', index: 0, delta: { type: 'thinking_delta', thinking } } });
const assistantText = (text: string, msgId: string) => ({ type: 'assistant', message: { role: 'assistant', id: msgId, content: [{ type: 'text', text }] } });
const hiddenThinking = (msgId: string) => ({ type: 'assistant', message: { role: 'assistant', id: msgId, content: [{ type: 'thinking', thinking: '', signature: 's' }] } });
const toolCall = (id: string, name: string, input: unknown) => ({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'tool_use', id, name, input }] } });
const toolResult = (id: string, content: unknown, isError = false) => ({ type: 'user', message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: id, content, is_error: isError }] } });
const result = (text: string) => ({ type: 'result', subtype: 'success', result: text });
const aborted = (reason = 'aborted_streaming') => ({ type: 'result', subtype: 'error_during_execution', is_error: true, terminal_reason: reason });
const partial = (id: string, text: string) => ({ type: 'x-optio-partial', id, text });
const sessionEnd = { type: 'x-optio-interrupt', by: 'session' };
const userInterrupt = { type: 'x-optio-interrupt', by: 'user' };
const closed = { type: 'x-optio-closed', reason: 'process ended' };
const queued = (id: string, text: string) => ({ type: 'x-optio-queued', id, text });
const lifecycle = (id: string, state: string) => ({ type: 'command_lifecycle', command_uuid: id, state });
const requeued = (id: string, newId: string) => ({ type: 'x-optio-requeued', id, new_id: newId });
const resumed = (ids?: string[]) => (ids ? { type: 'x-optio-resumed', requeued: ids } : { type: 'x-optio-resumed' });
const notice = user('System: you have been resumed');
const interruptedEcho = user('[Request interrupted by user]');

function run(events: any[]): ChatState {
  return events.reduce((s, ev, i) => reduceEvent(s, ev, i + 1, NOW), initialChatState);
}
const live = (events: any[]) => run(events);
const replay = (events: any[]) => run(events.filter((e) => e.type !== 'stream_event' && e.type !== 'x-optio-closed'));
// The closed row exists only live (x-optio-closed is never persisted).
const comparable = (s: ChatState) => s.items.filter((i) => i.kind !== 'closed').map(({ seq: _seq, ...rest }) => rest);
function ofKind<K extends ChatItem['kind']>(s: ChatState, kind: K): Extract<ChatItem, { kind: K }>[] {
  return s.items.filter((i) => i.kind === kind) as Extract<ChatItem, { kind: K }>[];
}
const rows = (s: ChatState) => ofKind(s, 'activity').map((a) => a.text);
const notDelivered = (s: ChatState) => rows(s).filter((t) => t.startsWith('Not delivered'));

// No graceful interrupt (it timed out, or the CLI died): the listener sends
// the partial, then the marker, right before x-optio-closed.
const cutOffStreaming = [
  running, user('Write a poem'), messageStart('m1'), streamStart('m1'),
  delta('Because I'), delta(' could not stop'), partial('m1', 'Because I could not stop'), sessionEnd, closed,
];
// The graceful path: the marker, then the CLI's own interrupt artefacts.
const graceful = [
  running, user('Write a poem'), messageStart('m1'), streamStart('m1'), delta('Because I'),
  sessionEnd, delta(' could'), assistantText('Because I could', 'm1'), interruptedEcho, aborted(), idle, closed,
];
// The graceful interrupt got no result in time: the partial follows the marker.
const timedOut = [
  running, user('Write a poem'), messageStart('m1'), streamStart('m1'), delta('Because I'),
  sessionEnd, delta(' could'), partial('m1', 'Because I could'), closed,
];

describe('claudecode session end: the session-ended marker and the partial', () => {
  it('the caption is verbatim', () => {
    expect(INTERRUPTED_SESSION_ENDED).toBe('⏹ Interrupted: session ended');
  });

  it('a session that ends while the answer streams: the partial is a cut-off bubble, then the row', () => {
    const s = live(cutOffStreaming);
    expect(s.items.map((i) => i.kind)).toEqual(['user', 'assistant', 'activity', 'closed']);
    expect(s.items[1]).toMatchObject({ text: 'Because I could not stop', pending: false, interrupted: true, msgId: 'm1', timestamp: START });
    expect('openPart' in s.items[1]).toBe(false);
    expect(s.items[2]).toMatchObject({ kind: 'activity', text: '⏹ Interrupted: session ended', muted: true });
    expect(s.interrupt).toBeUndefined();
    expect(s.busy).toBe(false);
  });

  it.each([
    ['no graceful interrupt', cutOffStreaming],
    ['graceful interrupt', graceful],
    ['graceful interrupt timed out', timedOut],
  ])('live and replay agree (%s)', (_name, events) => {
    expect(comparable(live(events))).toEqual(comparable(replay(events)));
  });

  it('the graceful path: the CLI artefacts render as this interruption, no echo bubble, no error', () => {
    const s = live(graceful);
    expect(s.items.map((i) => i.kind)).toEqual(['user', 'assistant', 'activity', 'closed']);
    expect(s.items[1]).toMatchObject({ text: 'Because I could', pending: false, interrupted: true });
    expect(rows(s)).toEqual([INTERRUPTED_SESSION_ENDED]);
    expect(ofKind(s, 'user').map((u) => u.text)).toEqual(['Write a poem']);
  });

  it('the graceful interrupt timed out: the partial completes the cut-off bubble in front of the row', () => {
    const s = live(timedOut);
    expect(s.items[1]).toMatchObject({ text: 'Because I could', interrupted: true, msgId: 'm1' });
    expect(rows(s)).toEqual([INTERRUPTED_SESSION_ENDED]);
  });

  it('hidden thinking only: no bubble, only the row', () => {
    const events = [
      running, user('Think hard'), messageStart('m1'), streamStart('m1'), thinkingDelta(''), hiddenThinking('m1'),
      sessionEnd, interruptedEcho, aborted(), idle, closed,
    ];
    const s = live(events);
    expect(ofKind(s, 'assistant')).toHaveLength(0);
    expect(rows(s)).toEqual([INTERRUPTED_SESSION_ENDED]);
    expect(ofKind(s, 'user').map((u) => u.text)).toEqual(['Think hard']);
    expect(comparable(s)).toEqual(comparable(replay(events)));
  });

  it('a running tool row is stopped, with or without the graceful interrupt', () => {
    const gracefulTool = [
      running, user('Run it'), toolCall('t1', 'Bash', { command: 'sleep 60' }), sessionEnd,
      toolResult('t1', "The user doesn't want to proceed with this tool use.", true),
      user('[Request interrupted by user for tool use]'), aborted('aborted_tools'), idle, closed,
    ];
    for (const events of [gracefulTool, [running, user('Run it'), toolCall('t1', 'Bash', { command: 'sleep 60' }), sessionEnd, closed]]) {
      const s = live(events);
      const tool = ofKind(s, 'tool')[0];
      expect(tool.status).toBe('stopped');
      expect(tool.result).toBeUndefined();
      expect(rows(s)).toEqual([INTERRUPTED_SESSION_ENDED]);
      expect(ofKind(s, 'error')).toHaveLength(0);
      expect(comparable(s)).toEqual(comparable(replay(events)));
    }
  });

  it('no too-late undo: a normal result after the marker keeps the rendering, with no second copy of the answer', () => {
    const events = [
      running, user('q'), messageStart('m1'), streamStart('m1'), delta('Hel'), sessionEnd,
      assistantText('Hello.', 'm1'), result('Hello.'), idle, closed,
    ];
    const s = live(events);
    expect(ofKind(s, 'assistant')).toHaveLength(1);
    expect(ofKind(s, 'assistant')[0]).toMatchObject({ text: 'Hello.', interrupted: true, pending: false });
    expect(rows(s)).toEqual([INTERRUPTED_SESSION_ENDED]);
    expect(ofKind(s, 'error')).toHaveLength(0);
    expect(comparable(s)).toEqual(comparable(replay(events)));
  });

  it('the resumed run leaves the cut-off answer and its row as they are', () => {
    const s = replay([
      running, user('q'), messageStart('m1'), partial('m1', 'Half a'), sessionEnd,
      resumed(), notice, assistantText('Resumed.', 'm2'), result('Resumed.'), idle,
    ]);
    const answers = ofKind(s, 'assistant');
    expect(answers[0]).toMatchObject({ text: 'Half a', interrupted: true, pending: false });
    expect(answers[1]).toMatchObject({ text: 'Resumed.', pending: false });
    expect('interrupted' in answers[1]).toBe(false);
    expect(rows(s)).toEqual([INTERRUPTED_SESSION_ENDED, 'System: you have been resumed']);
  });

  it('an operator interrupt already in flight keeps "Interrupted by you"', () => {
    const events = [
      running, user('q'), messageStart('m1'), streamStart('m1'), delta('Hal'), userInterrupt,
      partial('m1', 'Half'), sessionEnd, closed,
    ];
    const s = live(events);
    expect(rows(s)).toEqual(['⏹ Interrupted by you']);
    expect(ofKind(s, 'assistant')[0]).toMatchObject({ text: 'Half', interrupted: true });
    expect(comparable(s)).toEqual(comparable(replay(events)));
  });
});

describe('claudecode session end: messages re-queued on resume', () => {
  const full = [
    running, user('q'), queued('q1', 'later'), sessionEnd, closed, resumed(['q1']), notice,
    requeued('q1', 'n1'), lifecycle('n1', 'started'), echoU('n1', 'later\n\n', '2026-09-15T10:01:00.000Z'),
  ];

  it('x-optio-resumed keeps a bubble it lists queued, and x-optio-requeued re-keys it', () => {
    const s = replay([running, user('q'), queued('q1', 'later'), sessionEnd, resumed(['q1']), notice, requeued('q1', 'n1')]);
    expect(ofKind(s, 'user').filter((u) => u.queued)).toEqual([
      expect.objectContaining({ text: 'later', queued: true, queueId: 'n1' }),
    ]);
    expect(notDelivered(s)).toEqual([]);
  });

  it('the re-queued message is delivered once, under its new id, and live agrees with replay', () => {
    const s = live(full);
    const later = ofKind(s, 'user').filter((u) => u.text === 'later');
    expect(later).toHaveLength(1);
    expect(later[0]).toMatchObject({ queueId: 'n1', timestamp: Date.parse('2026-09-15T10:01:00.000Z') });
    expect(later[0].queued).toBeUndefined();
    expect(notDelivered(s)).toEqual([]);
    expect(comparable(s)).toEqual(comparable(replay(full)));
  });

  it("x-optio-closed's Not delivered does not stick, even for a viewer that missed x-optio-resumed", () => {
    const s = live([running, user('q'), queued('q1', 'later'), sessionEnd, closed, notice, requeued('q1', 'n1')]);
    expect(notDelivered(s)).toEqual([]);
    expect(s.items[s.items.length - 1]).toMatchObject({ kind: 'user', text: 'later', queued: true, queueId: 'n1' });
  });

  it('a message the graceful interrupt swept (cancelled, never started) comes back queued and is delivered once', () => {
    const events = [
      running, user('q'), queued('q1', 'later'), sessionEnd, lifecycle('q1', 'cancelled'), aborted(), idle, closed,
      resumed(['q1']), notice, requeued('q1', 'n1'), lifecycle('n1', 'started'),
      echoU('n1', 'later\n\n', '2026-09-15T10:01:00.000Z'),
    ];
    const s = live(events);
    expect(ofKind(s, 'user').map((u) => u.text)).toEqual(['q', 'later']);
    expect(notDelivered(s)).toEqual([]);
    expect(comparable(s)).toEqual(comparable(replay(events)));
  });

  it('a message the graceful interrupt swept keeps its place ahead of ones that stayed queued, live and replay agree', () => {
    // Review round 1, finding 1: q1 is swept by the graceful interrupt
    // (cancelled, never started) and becomes a "Not delivered" note before
    // x-optio-closed/x-optio-resumed even run; q2 and q3 stay plainly
    // queued the whole time. The resumed run re-queues all three: the
    // original send order (q1, q2, q3) must survive in both live and
    // replay, not just live (which never shows the bug: x-optio-closed's
    // blanket drop turns q2/q3 into notes too, so restoring one id at a
    // time from an all-notes list happens to keep the right order).
    const events = [
      running, user('q'), queued('q1', 'one'), queued('q2', 'two'), queued('q3', 'three'),
      lifecycle('q1', 'queued'), sessionEnd, lifecycle('q1', 'cancelled'), aborted(), idle, closed,
      resumed(['q1', 'q2', 'q3']), notice,
    ];
    const s = live(events);
    expect(ofKind(s, 'user').filter((u) => u.queued).map((u) => u.text)).toEqual(['one', 'two', 'three']);
    expect(notDelivered(s)).toEqual([]);
    expect(comparable(s)).toEqual(comparable(replay(events)));
  });

  it('a bubble x-optio-resumed does not list becomes Not delivered; the listed one stays queued below it', () => {
    const s = run([running, user('q'), queued('q1', 'a'), queued('q2', 'b'), resumed(['q2'])]);
    expect(s.items.map((i) => ('text' in i ? i.text : i.kind))).toEqual(['q', 'Not delivered: a', 'b']);
    expect(s.items[1]).toMatchObject({ kind: 'activity', muted: true, requeueId: 'q1' });
    expect(s.items[2]).toMatchObject({ kind: 'user', queued: true, queueId: 'q2' });
  });
});

function renderState(state: ChatState): ReturnType<typeof render> {
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

describe('ConversationView: a session that ended mid-turn', () => {
  it('shows the cut-off answer with its jagged edge and ellipsis, then the muted session-ended row', () => {
    renderState(replay(cutOffStreaming));
    expect(screen.getByTestId('answer-interrupted').textContent).toContain('Because I could not stop…');
    expect(screen.getByTestId('activity-muted').textContent).toBe('⏹ Interrupted: session ended');
  });
});
