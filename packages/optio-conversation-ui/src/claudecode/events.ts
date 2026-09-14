// Pure event reducer: raw Claude Code stream-json events -> ChatState.
//
// All Claude-specific interpretation lives here (testable without DOM):
// the listener and the widget transport pass raw events through untouched.
// Wire shapes per the Phase I conversation-gate design (system / user /
// assistant / result / control_request / x-optio-* synthetic events;
// partials arrive as {type:"stream_event", event:{...content_block_delta}}).

import type { ChatItem, ChatState } from '../chat.js';
import {
  INTERRUPTED_BY_YOU, addQueued, appendItems, dropUndelivered, foldControlUpdate, isQueued, takeQueuedAt, takeQueuedIds,
} from '../chat.js';
import { explainApiError } from '../apiError.js';
import { parseUploadNotice, uploadNoticeActivityText } from '../uploads.js';
export { initialChatState } from '../chat.js';

const HARNESS_PREFIX = 'System: ';

// Parts of one assistant message (narration, then the answer) render in one
// bubble, separated by a blank line.
const PART_SEPARATOR = '\n\n';

// Claude Code's own cancel artefacts (CLI 2.1.270): the user text it adds to
// an interrupted turn, and the terminal reasons of that turn's error result.
const INTERRUPT_ECHO = /^\[Request interrupted by user( for tool use)?\]$/;
const ABORTED = new Set<string>(['aborted_streaming', 'aborted_tools']);

type AssistantItem = Extract<ChatItem, { kind: 'assistant' }>;
type UserItem = Extract<ChatItem, { kind: 'user' }>;
type ActivityItem = Extract<ChatItem, { kind: 'activity' }>;

// Text a content block contributes to the reply bubble: a text block's text,
// or the narration a text-bearing thinking block carries. Since CLI 2.1.267
// the model writes its between-tool narration as such "thinking updates"; the
// real reasoning arrives as a separate thinking block with empty text.
function blockText(block: any): string | null {
  if (block?.type === 'text' && typeof block.text === 'string') return block.text;
  if (block?.type === 'thinking' && typeof block.thinking === 'string' && block.thinking.trim() !== '') {
    return block.thinking;
  }
  return null;
}

function replaceAt(items: ChatItem[], idx: number, item: ChatItem): ChatItem[] {
  return [...items.slice(0, idx), item, ...items.slice(idx + 1)];
}

type ToolItem = Extract<ChatItem, { kind: 'tool' }>;

const RESULT_MAX = 2000;

// Epoch ms of an event's ISO `timestamp`, or null. On the wire (CLI 2.1.269)
// only user and assistant events carry one; system and result events don't.
function wireTime(ev: any): number | null {
  const t = typeof ev?.timestamp === 'string' ? Date.parse(ev.timestamp) : NaN;
  return Number.isFinite(t) ? t : null;
}

// Epoch ms of an event: its own timestamp, else `fallback`.
function eventTime(ev: any, fallback: number): number {
  return wireTime(ev) ?? fallback;
}

// The time base for events without a timestamp (system, result, optio's
// synthetic events): the latest user/assistant wire time, so a replay hours
// later shows the live durations. The reducer clock only until a wire time has
// been seen.
function lastWireTime(state: ChatState, now: number): number {
  return state.lastEventAt ?? now;
}

function trimResult(s: string): string {
  const t = s.trim();
  return t.length > RESULT_MAX ? t.slice(0, RESULT_MAX - 1) + '…' : t;
}

// tool_result content is a string or a list of blocks; keep the text blocks.
function toolResultText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .filter((b: any) => b?.type === 'text' && typeof b.text === 'string')
    .map((b: any) => b.text)
    .join('\n');
}

function toolRow(block: any, seq: number, at: number): ChatItem {
  // An explicit status: without one the view sniffs the input, and an input
  // with a `result` key would read as a finished call.
  const row: ToolItem = { kind: 'tool', name: String(block.name ?? ''), input: block.input, seq, status: 'running', startedAt: at };
  if (typeof block.id === 'string') row.callId = block.id;
  return row;
}

// Apply tool_result blocks to their rows (matched by tool_use_id). A background
// row ignores its immediate result: the task notification finishes it. While
// an operator interrupt is in flight, an error result is the CLI rejecting the
// call it cancelled: the row stops, and the boilerplate is not its result.
// `stoppedSeqs`, when given, collects the seq of every row this call stopped
// for that reason, so the caller can record it against the interrupt that
// caused it (see noteInterrupted).
function applyToolResults(
  items: ChatItem[], content: unknown, at: number, interrupted = false, stoppedSeqs?: number[],
): ChatItem[] {
  if (!Array.isArray(content)) return items;
  let out = items;
  for (const b of content) {
    if (b?.type !== 'tool_result' || typeof b.tool_use_id !== 'string') continue;
    const idx = out.findIndex((i) => i.kind === 'tool' && i.callId === b.tool_use_id);
    if (idx === -1) continue;
    const row = out[idx] as ToolItem;
    if (row.background) continue;
    if (interrupted && b.is_error) {
      out = replaceAt(out, idx, { ...row, status: 'stopped', endedAt: at });
      stoppedSeqs?.push(row.seq);
      continue;
    }
    out = replaceAt(out, idx, {
      ...row,
      status: b.is_error ? 'failed' : 'done',
      result: trimResult(toolResultText(b.content)),
      endedAt: at,
    });
  }
  return out;
}

// Stop rows still running. At the end of a turn ('turn') only the counter
// stops, and background rows keep counting: their task outlives the turn. When
// the session ends ('session': x-optio-closed, or x-optio-resumed for the run
// that produced the replayed history) nothing will report on them any more:
// every running row, background included, becomes 'stopped', keeping an end
// time it already has.
// 'interrupt' (an operator interrupt): every running row except background
// ones (their task outlives the turn) becomes 'stopped'.
function freezeRunning(items: ChatItem[], at: number, scope: 'turn' | 'interrupt' | 'session'): ChatItem[] {
  let changed = false;
  const out = items.map((i) => {
    if (i.kind !== 'tool' || (i.status !== undefined && i.status !== 'running')) return i;
    if (scope === 'turn') {
      if (i.background || i.startedAt === undefined || i.endedAt !== undefined) return i;
      changed = true;
      return { ...i, endedAt: at };
    }
    if (scope === 'interrupt' && i.background) return i;
    changed = true;
    const next: ToolItem = { ...i, status: 'stopped' };
    if (next.startedAt !== undefined && next.endedAt === undefined) next.endedAt = at;
    return next;
  });
  return changed ? out : items;
}

interface TaskNotice {
  taskId: string;
  toolUseId: string | null;
  status: string;
  summary: string;
}

function tagValue(xml: string, tag: string): string | null {
  const m = xml.match(new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`));
  return m ? m[1].trim() : null;
}

// The <task-notification> element the CLI injects as a user turn when a
// background command ends.
function parseTaskNotification(text: string): TaskNotice | null {
  const taskId = tagValue(text, 'task-id');
  if (!taskId) return null;
  return {
    taskId,
    toolUseId: tagValue(text, 'tool-use-id'),
    status: tagValue(text, 'status') ?? '',
    summary: tagValue(text, 'summary') ?? '',
  };
}

// A background task ended (system/task_notification or an injected
// <task-notification> turn): finish its Bash row, or add a muted activity row
// when no row exists (e.g. a replay without the call). Each task applies once.
// Only 'completed' is a success; 'failed' is a failure; anything else (e.g.
// 'stopped', 'killed') is treated as stopped — not a failure, but not a
// finished-successfully row either.
function applyTaskNotice(state: ChatState, n: TaskNotice, at: number, seq: number): ChatState {
  const seen = state.finishedTaskIds ?? [];
  if (seen.includes(n.taskId)) return state;
  const toolStatus: 'done' | 'failed' | 'stopped' =
    n.status === 'completed' ? 'done' : n.status === 'failed' ? 'failed' : 'stopped';
  const idx = n.toolUseId
    ? state.items.findIndex((i) => i.kind === 'tool' && i.callId === n.toolUseId)
    : -1;
  let items: ChatItem[];
  const found = idx !== -1 ? (state.items[idx] as ToolItem) : null;
  if (found && !found.background && found.taskId === n.taskId) {
    // A foreground call the CLI ran as a task (task_started with
    // is_backgrounded false), reported when an interrupt stops it: it stays a
    // foreground row, and the summary (its command) is not its result.
    items = replaceAt(state.items, idx, { ...found, status: toolStatus, endedAt: found.endedAt ?? at });
  } else if (idx !== -1) {
    const row = state.items[idx] as ToolItem;
    items = replaceAt(state.items, idx, {
      ...row,
      background: true,
      status: toolStatus,
      result: trimResult(n.summary),
      // A background row that already ended keeps its time: task_updated's
      // end_time is exact, the notice's time is not.
      endedAt: row.background && row.endedAt !== undefined ? row.endedAt : at,
    });
  } else {
    // An empty summary names the task instead.
    const what = n.summary.trim() || n.taskId;
    const text =
      toolStatus === 'done'
        ? `✓ Background task finished: ${what}`
        : toolStatus === 'failed'
          ? `✗ Background task failed: ${what}`
          : `⏹ Background task stopped: ${what}`;
    items = appendItems(state.items, [{ kind: 'activity', text, seq }]);
  }
  return { ...state, items, finishedTaskIds: [...seen, n.taskId] };
}

// message.content is either a plain string or an array of content blocks;
// join the text blocks with a newline. Separate blocks are logically distinct
// messages (e.g. several harness "System:" notices claude coalesced into one
// user event) and must not render run-together.
function extractText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .filter((block: any) => block?.type === 'text' && typeof block.text === 'string')
    .map((block: any) => block.text)
    .join('\n');
}

// message.content's text blocks, in order (tool_use/tool_result/thinking
// blocks excluded). A lone user message carries exactly one; a joint echo of
// several messages queued together (CLI 2.1.270: two "Send when ready"
// messages queued while the answer streams come back as ONE isReplay echo,
// one text block per message, each the exact sent text) carries more than
// one — see the multi-block take below.
function textBlocks(content: unknown): string[] {
  if (typeof content === 'string') return content === '' ? [] : [content];
  if (!Array.isArray(content)) return [];
  return content
    .filter((block: any) => block?.type === 'text' && typeof block.text === 'string')
    .map((block: any) => block.text);
}

function pendingIndex(items: ChatItem[]): number {
  return items.findIndex((item) => item.kind === 'assistant' && item.pending);
}

// The pending bubble may keep absorbing the in-flight turn only while it is
// the conversation's tail. Tool rows don't count: they are progress rows, not
// newer conversation content. Anything else after the bubble (activity rows,
// permission cards, user turns) means newer content has been appended — the
// bubble is stale and must not act as an anchor anymore. Queued bubbles
// don't count either: they are pinned below the conversation.
function isTail(items: ChatItem[], idx: number): boolean {
  return items.slice(idx + 1).every((i) => i.kind === 'tool' || isQueued(i));
}

// Finalize the bubble at idx in place (text kept), used when newer content
// has to open a fresh bubble after it.
function finalizeAt(items: ChatItem[], idx: number): ChatItem[] {
  const current = items[idx] as AssistantItem;
  if (!current.pending) return items;
  const next: AssistantItem = { ...current, pending: false };
  delete next.openPart;
  return replaceAt(items, idx, next);
}

// A streamed delta (text_delta or thinking_delta) extends the open part of the
// pending bubble; the first delta of a block opens a new part, separated from
// earlier parts by a blank line. A pending bubble that is no longer the tail is
// finalized where it stands and a fresh bubble opens at the end.
function appendDelta(items: ChatItem[], seq: number, delta: string): ChatItem[] {
  const idx = pendingIndex(items);
  if (idx !== -1 && isTail(items, idx)) {
    const cur = items[idx] as AssistantItem;
    if (cur.openPart !== undefined) return replaceAt(items, idx, { ...cur, text: cur.text + delta });
    const sep = cur.text === '' ? '' : PART_SEPARATOR;
    return replaceAt(items, idx, {
      ...cur,
      text: cur.text + sep + delta,
      openPart: cur.text.length + sep.length,
    });
  }
  if (idx !== -1) items = finalizeAt(items, idx);
  return appendItems(items, [{ kind: 'assistant', text: delta, pending: true, seq, msgId: null, openPart: 0 }]);
}

// A content block's final assistant event (it carries the block's full text).
// Within the same message it replaces the part its deltas streamed, or appends
// a new part when nothing streamed (replays hold no stream_events). A different
// message, or a pending bubble that is no longer the tail, opens a fresh bubble.
// `ts`, when given, is this event's own wire time; the bubble keeps the FIRST
// one it ever sees for its message (a streaming delta carries none — the
// bubble may exist with no timestamp yet, filled in once its assistant event
// arrives; a later event for the same message must not overwrite it).
function applyBlockText(items: ChatItem[], seq: number, text: string, msgId?: string, ts?: number): ChatItem[] {
  const idx = pendingIndex(items);
  if (idx !== -1) {
    const cur = items[idx] as AssistantItem;
    const sameMessage = cur.msgId === null || msgId == null || cur.msgId === msgId;
    if (isTail(items, idx) && sameMessage) {
      const base =
        cur.openPart !== undefined
          ? cur.text.slice(0, cur.openPart)
          : cur.text + (cur.text === '' ? '' : PART_SEPARATOR);
      const next: AssistantItem = { ...cur, text: base + text, msgId: msgId ?? cur.msgId };
      if (next.timestamp === undefined && ts !== undefined) next.timestamp = ts;
      delete next.openPart;
      return replaceAt(items, idx, next);
    }
    items = finalizeAt(items, idx);
  }
  const fresh: AssistantItem = { kind: 'assistant', text, pending: true, seq, msgId: msgId ?? null };
  if (ts !== undefined) fresh.timestamp = ts;
  return appendItems(items, [fresh]);
}

// Finalize the in-flight assistant bubble (pending -> false). The result text
// replaces the bubble's text unless the bubble already ends with it (ignoring
// surrounding whitespace): narration parts earlier in the same message must
// survive the end of the turn. Creates
// a finalized bubble if there is result text but no pending bubble (e.g. a
// replay that skipped partials).
function finalizePending(items: ChatItem[], seq: number, resultText: string | null): ChatItem[] {
  const idx = pendingIndex(items);
  if (idx === -1) {
    if (resultText === null || resultText === '') return items;
    return appendItems(items, [{ kind: 'assistant', text: resultText, pending: false, seq, msgId: null }]);
  }
  const current = items[idx] as AssistantItem;
  const keep = resultText === null || current.text.trimEnd().endsWith(resultText.trim());
  const next: AssistantItem = { ...current, text: keep ? current.text : resultText, pending: false };
  delete next.openPart;
  return replaceAt(items, idx, next);
}

// Insert a user message before the assistant bubble it triggered. With CLI
// 2.1.270 the prompt's echo arrives BEFORE `message_start` in every
// recording, but the reducer keeps this path for older replays where
// `--replay-user-messages` made Claude stream the whole answer FIRST and
// only echo the user message afterward: the streaming assistant bubble
// already exists (and has an earlier seq) when that late user echo arrives.
// Ordering by seq — or appending on arrival — would therefore render the
// answer above the question. Conversation order is what we want, so the
// echoed user turn slots in front of the in-flight assistant bubble — but
// ONLY while that bubble is the conversation's last content (queued bubbles
// aside). A stale pending bubble (e.g. replayed from a buffer captured
// mid-turn, never finalized) must not pull later, unrelated user events
// above newer content.
function insertBeforePending(items: ChatItem[], rows: ChatItem[]): ChatItem[] {
  const idx = pendingIndex(items);
  // A message echoed after a tool row was taken mid-turn (Claude Code takes
  // a message sent mid-turn at the next tool result): it belongs after that
  // row, not above the in-flight answer.
  if (idx === -1 || !items.slice(idx + 1).every(isQueued)) return appendItems(items, rows);
  return [...items.slice(0, idx), ...rows, ...items.slice(idx)];
}

// -- Operator interrupts (x-optio-interrupt) ---------------------------------

// The index of the "Interrupted by you" row an in-flight interrupt added.
function interruptRowIndex(items: ChatItem[], rowSeq: number): number {
  return items.findIndex((i) => i.kind === 'activity' && i.seq === rowSeq);
}

// Rows the interrupted turn still produces go in front of its row.
function insertBeforeRow(items: ChatItem[], rowSeq: number, rows: ChatItem[]): ChatItem[] {
  const r = interruptRowIndex(items, rowSeq);
  if (r === -1) return appendItems(items, rows);
  return [...items.slice(0, r), ...rows, ...items.slice(r)];
}

// The interrupted message's final text (the CLI sends it after the
// interrupt): it completes the interrupted bubble right before the row,
// replacing the part its deltas streamed (live), or becomes that bubble
// (replay holds no deltas). `ts`: see applyBlockText — first-seen wins.
function applyInterruptedText(
  items: ChatItem[], rowSeq: number, seq: number, text: string, msgId?: string, ts?: number,
): ChatItem[] {
  const r = interruptRowIndex(items, rowSeq);
  const prev = r > 0 ? items[r - 1] : undefined;
  if (prev?.kind === 'assistant' && prev.interrupted && (prev.msgId === null || msgId == null || prev.msgId === msgId)) {
    const base =
      prev.openPart !== undefined
        ? prev.text.slice(0, prev.openPart)
        : prev.text + (prev.text === '' ? '' : PART_SEPARATOR);
    const next: AssistantItem = { ...prev, text: base + text, msgId: msgId ?? prev.msgId };
    if (next.timestamp === undefined && ts !== undefined) next.timestamp = ts;
    delete next.openPart;
    return replaceAt(items, r - 1, next);
  }
  const fresh: AssistantItem = { kind: 'assistant', text, pending: false, seq, msgId: msgId ?? null, interrupted: true };
  if (ts !== undefined) fresh.timestamp = ts;
  return insertBeforeRow(items, rowSeq, [fresh]);
}

// Record that the current interrupt (if any) is responsible for the item
// whose own (persistent) `seq` is `itemSeq` -- fix round 2. An item's `seq`
// is set once, at the event that created it, and survives every later
// in-place update (replaceAt always spreads the old item first), so it is a
// stable identity even though the array index and the item's own fields
// change. Recording is deliberately conservative: it is fine to note a seq
// that never actually lands on a matching item (undoInterrupt then simply
// finds nothing to revert for it), but it must never fail to note one that
// does, or a too-late race would corrupt an item nobody tracked.
function noteInterrupted(state: ChatState, itemSeq: number): ChatState {
  if (!state.interrupt || state.interrupt.itemSeqs.includes(itemSeq)) return state;
  return { ...state, interrupt: { ...state.interrupt, itemSeqs: [...state.interrupt.itemSeqs, itemSeq] } };
}

// The interrupted turn is over: drop the flag and the part offset the
// interrupted bubble kept for its final text.
function endInterrupt(state: ChatState): ChatState {
  if (!state.interrupt) return state;
  const items = state.items.map((i) => {
    if (i.kind !== 'assistant' || !i.interrupted || i.openPart === undefined) return i;
    const next: AssistantItem = { ...i };
    delete next.openPart;
    return next;
  });
  const next: ChatState = { ...state, items };
  delete next.interrupt;
  return next;
}

// OWNER RULING (Task 6 fix round 1; scoped in fix round 2): a too-late
// interrupt — x-optio-interrupt followed, in the same turn, by a result that
// is not an abort — races the end of a turn the CLI finished normally.
// Rendering must end up exactly as if optio had never sent it: undo every
// mark THIS interrupt made for this turn. The row is dropped; a bubble this
// firing marked goes back to pending, so the normal end-of-turn path below
// (finalizePending) completes it in place instead of appending a second
// copy; a tool row this firing (or a CLI signal that only ever fires because
// of it, e.g. a rejected tool_result or a foreground task_notification)
// stopped without a result goes back to running, so the normal 'turn' freeze
// handles it exactly as it would have without the interrupt.
//
// Fix round 2: scoped to `interrupt.itemSeqs` — the items THIS firing
// touched (recorded as they happened, via noteInterrupted) — instead of
// pattern-matching `interrupted`/`stopped` across the whole item list. A
// second, unrelated interrupt earlier in the same session (a genuinely
// aborted turn, permanently `interrupted:true`/`stopped` with no result) is
// no longer swept up by a later turn's too-late race. Abort results never
// call this: they keep today's interrupt rendering.
function undoInterrupt(items: ChatItem[], interrupt: { rowSeq: number; itemSeqs: number[] }): ChatItem[] {
  const r = interruptRowIndex(items, interrupt.rowSeq);
  const out = r === -1 ? items : [...items.slice(0, r), ...items.slice(r + 1)];
  const owned = new Set(interrupt.itemSeqs);
  return out.map((i) => {
    if (!owned.has(i.seq)) return i;
    if (i.kind === 'assistant' && i.interrupted) {
      const next: AssistantItem = { ...i, pending: true };
      delete next.interrupted;
      return next;
    }
    if (i.kind === 'tool' && i.status === 'stopped' && i.result === undefined) {
      const next: ToolItem = { ...i, status: 'running' };
      delete next.endedAt;
      return next;
    }
    return i;
  });
}

export function reduceEvent(state: ChatState, ev: any, seq: number, now: number = Date.now()): ChatState {
  // Sniff the runtime model and fold it into the model control. Claude Code
  // reports the model at top level on `system`/`init` (fires immediately at
  // launch) and on each assistant `message.model`. The stream uses the
  // runtime/variant id (e.g. claude-opus-4-8[1m]); strip the [..] suffix so it
  // matches a catalog option. Only fill while the control has no value yet — an
  // operator pick (reflected optimistically) must win, and the in-flight
  // process keeps reporting the OLD model until the restart completes.
  const rawModel = ev?.model ?? ev?.message?.model;
  if (typeof rawModel === 'string' && rawModel) {
    const modelCtrl = state.controls.find((c) => c.id === 'model');
    if (modelCtrl && !modelCtrl.value) {
      state = foldControlUpdate(state, { id: 'model', value: rawModel.replace(/\[[^\]]*\]$/, '') });
    }
  }
  // Track the latest wire time (see lastWireTime).
  if (ev?.type === 'user' || ev?.type === 'assistant') {
    const t = wireTime(ev);
    if (t !== null && (state.lastEventAt === undefined || t > state.lastEventAt)) {
      state = { ...state, lastEventAt: t };
    }
  }
  switch (ev?.type) {
    case 'x-optio-control-update':
      return foldControlUpdate(state, ev);

    case 'user': {
      // An upload prepends `System: upload received…` lines; split them off —
      // `.text` is the real prompt, `.uploads` drives a persistent muted
      // "attached files" row that re-renders on resume (Claude replays the user
      // message under --replay-user-messages).
      // Tool results arrive as user events; finish their rows first. Such an
      // event carries no text, so it adds no bubble below.
      const stoppedByInterrupt: number[] = [];
      const withResults = applyToolResults(
        state.items, ev.message?.content, eventTime(ev, now), state.interrupt !== undefined, stoppedByInterrupt,
      );
      if (withResults !== state.items) state = { ...state, items: withResults };
      for (const s of stoppedByInterrupt) state = noteInterrupted(state, s);
      // A joint echo of several messages, taken together at the start of one
      // follow-up turn (see textBlocks): match each block, in order, to its
      // own local or queued bubble and confirm it (FIFO by text, same as the
      // single-message match below, which matches `local || queued` —
      // final-review M2). A local (not yet queued) bubble is confirmed in
      // place; a queued one moves to the take point, exactly as the
      // single-message path does. Two messages queued mid-tool instead
      // arrive as two separate one-block echoes and take the ordinary path.
      const blocks = textBlocks(ev.message?.content);
      // The one echo event's own wire time: "once the message is taken, the
      // transcript user message shows the echo's wire timestamp" — every
      // block taken by this same event shares it.
      const echoTs = wireTime(ev) ?? undefined;
      if (blocks.length > 1) {
        let items = state.items;
        let matchedAll = true;
        for (const raw of blocks) {
          // final-review M2: parseUploadNotice per block, as the
          // single-message path already does below.
          const { text: t, uploads } = parseUploadNotice(raw);
          const idx = items.findIndex(
            (i) => i.kind === 'user' && (i.local === true || isQueued(i)) && i.text === t,
          );
          if (idx === -1) {
            matchedAll = false;
            break;
          }
          const attach: ChatItem[] =
            uploads.length > 0 ? [{ kind: 'activity', text: uploadNoticeActivityText(uploads), seq }] : [];
          if (isQueued(items[idx])) {
            items = takeQueuedAt(items, idx, attach, echoTs);
          } else {
            const confirmed = { ...items[idx] } as unknown as UserItem;
            delete confirmed.local;
            if (echoTs !== undefined) confirmed.timestamp = echoTs;
            items = [...items.slice(0, idx), ...attach, confirmed, ...items.slice(idx + 1)];
          }
        }
        if (matchedAll) return { ...state, items, busy: true };
      }
      // A background command ended and the CLI injected its notification as a
      // user turn: apply it to the Bash row; never render it as a user bubble.
      // Trust `origin` when the CLI sends it: only an origin explicitly tagged
      // task-notification is treated as one, so a genuine user prompt that
      // happens to start with the literal text renders normally. Fall back to
      // the text-prefix sniff only for older replays that carry no origin.
      const rawText = extractText(ev.message?.content);
      // The CLI's own marker for the turn optio interrupted: its row says so.
      if (state.interrupt && INTERRUPT_ECHO.test(rawText.trim())) return state;
      const isInjectedNotification =
        ev.origin && typeof ev.origin === 'object'
          ? ev.origin.kind === 'task-notification'
          : rawText.trimStart().startsWith('<task-notification>');
      if (isInjectedNotification) {
        const notice = parseTaskNotification(rawText);
        if (!notice) return state;
        // Older CLIs start a model turn right after this notification; mark
        // the agent busy so the working indicator and interrupt/Escape work.
        // (CLI 2.1.269 sends no such user event on stdout: its follow-up turn
        // is bracketed by system/session_state_changed running/idle, which
        // drive busy below.) The system/task_notification route itself never
        // touches busy: the turn, not the notice, makes the agent busy.
        return { ...applyTaskNotice(state, notice, eventTime(ev, lastWireTime(state, now)), seq), busy: true };
      }
      const { text, uploads } = parseUploadNotice(rawText);
      if (text === '' && uploads.length === 0) return state;
      const attach: ChatItem | null =
        uploads.length > 0 ? { kind: 'activity', text: uploadNoticeActivityText(uploads), seq } : null;
      // Harness-injected messages (resume notices, auto-start prompt) render as
      // activity rows, not user bubbles; an upload with no prompt body renders
      // just its attachment row. Either way the agent is working. Fix 9, owner
      // ruling 2026-09-14: the row carries this echo's own wire timestamp
      // (never the reducer's clock) — never invented when the wire carries none.
      if (text === '' || text.startsWith(HARNESS_PREFIX)) {
        let items = attach ? appendItems(state.items, [attach]) : state.items;
        if (text !== '') {
          const activityItem: ActivityItem = { kind: 'activity', text, seq };
          const t = wireTime(ev);
          if (t !== null) activityItem.timestamp = t;
          items = appendItems(items, [activityItem]);
        }
        return { ...state, items, busy: true };
      }
      // Wire echo of a message already on screen: the widget's optimistic
      // local bubble, or a queued one. FIFO by text.
      const localIdx = state.items.findIndex(
        (i) => i.kind === 'user' && (i.local === true || i.queued === true) && i.text === text,
      );
      // A queued message the agent took now (after the tool row it came
      // with, or at the start of the next turn): it moves to the take point,
      // its attachment row in front.
      if (localIdx !== -1 && (state.items[localIdx] as UserItem).queued) {
        return { ...state, items: takeQueuedAt(state.items, localIdx, attach ? [attach] : [], wireTime(ev) ?? undefined), busy: true };
      }
      // A local bubble: confirm it in place instead of inserting a duplicate
      // (the attachment row slots just before it). The echo's own wire time
      // replaces whatever send-time the local (optimistic) bubble carried.
      if (localIdx !== -1) {
        const confirmed = { ...state.items[localIdx] } as Extract<ChatItem, { kind: 'user' }>;
        delete confirmed.local;
        const t = wireTime(ev);
        if (t !== null) confirmed.timestamp = t;
        const items = attach
          ? [...state.items.slice(0, localIdx), attach, confirmed, ...state.items.slice(localIdx + 1)]
          : [...state.items.slice(0, localIdx), confirmed, ...state.items.slice(localIdx + 1)];
        return { ...state, items, busy: true };
      }
      // Replayed / un-echoed prompt: slot the attachment row + user bubble in
      // front of the in-flight assistant bubble (the answer streams first).
      const userItem: UserItem = { kind: 'user', text, seq };
      if (echoTs !== undefined) userItem.timestamp = echoTs;
      const rows: ChatItem[] = attach ? [attach, userItem] : [userItem];
      return { ...state, items: insertBeforePending(state.items, rows), busy: true };
    }

    // Synthetic, widget-emitted: render the operator's own message the moment
    // it is accepted by the listener, instead of waiting for Claude's echo
    // (which only arrives after the answer starts streaming).
    case 'x-optio-local-user': {
      const text = typeof ev.text === 'string' ? ev.text : '';
      if (text === '') return state;
      const id = typeof ev.id === 'string' && ev.id !== '' ? ev.id : undefined;
      // The listener's x-optio-queued (or the message's echo) got here first.
      if (id !== undefined) {
        const idx = state.items.findIndex((i) => i.kind === 'user' && i.queueId === id);
        if (idx !== -1) {
          const existing = state.items[idx] as UserItem;
          // x-optio-queued carries no time of its own, so a bubble it created
          // has none yet. If the item is still queued/local (the wire echo
          // has not confirmed it) and untimed, this is the only send-time we
          // have — use it. An item the wire echo already confirmed (queued
          // and local both cleared) keeps its own wire time; never overwrite
          // that.
          if ((existing.queued === true || existing.local === true) && existing.timestamp === undefined && typeof ev.time === 'number') {
            const timed = { ...existing, timestamp: ev.time };
            return { ...state, items: [...state.items.slice(0, idx), timed, ...state.items.slice(idx + 1)], busy: true };
          }
          return { ...state, busy: true };
        }
      }
      const item: UserItem = { kind: 'user', text, seq, local: true };
      if (id !== undefined) item.queueId = id;
      // The view's own send-time (Date.now() at the moment it dispatched this
      // synthetic event) — the reducer itself never reads the clock. Shown
      // live until the wire echo replaces it with its own timestamp.
      if (typeof ev.time === 'number') item.timestamp = ev.time;
      // Queued (the /send response said so), or sent behind queued messages:
      // it waits, pinned at the bottom with them.
      if (ev.queued === true || state.items.some(isQueued)) item.queued = true;
      return { ...state, items: [...state.items, item], busy: true };
    }

    // Synthetic, listener-emitted (steering): the operator interrupted
    // (Interrupt, Interrupt and send, Send now). The only source of the
    // "Interrupted by you" row. Nothing in flight, or an interrupt already in
    // flight: no-op.
    case 'x-optio-interrupt': {
      if (!state.busy || state.interrupt) return state;
      let items = state.items;
      const idx = pendingIndex(items);
      // The seqs of the items THIS firing marks (fix round 2): a too-late
      // race (undoInterrupt) reverts exactly these, never an unrelated item.
      const itemSeqs: number[] = [];
      if (idx !== -1) {
        // The in-flight answer (only queued bubbles after it) is cut off: it
        // keeps its text (and its open part, which the final text event
        // replaces) and gets the jagged edge. A pending bubble with tool rows
        // after it was already complete: it just stops being pending (and is
        // not tracked — it never becomes `interrupted`, so it can't be
        // mistaken for this interrupt's doing).
        if (items.slice(idx + 1).every(isQueued)) {
          const cur = items[idx] as AssistantItem;
          itemSeqs.push(cur.seq);
          items = replaceAt(items, idx, { ...cur, pending: false, interrupted: true });
        } else {
          items = finalizeAt(items, idx);
        }
      }
      for (const i of items) {
        if (i.kind === 'tool' && (i.status === undefined || i.status === 'running') && !i.background) {
          itemSeqs.push(i.seq);
        }
      }
      items = freezeRunning(items, lastWireTime(state, now), 'interrupt');
      items = appendItems(items, [{ kind: 'activity', text: INTERRUPTED_BY_YOU, seq, muted: true }]);
      return { ...state, items, interrupt: { rowSeq: seq, itemSeqs } };
    }

    // Synthetic, listener-emitted (steering): a Send when ready that arrived
    // while the agent works. Upload notice lines are split off as in a user
    // echo, so the bubble shows (and the echo matches) the prompt text.
    case 'x-optio-queued': {
      const id = typeof ev.id === 'string' ? ev.id : '';
      const { text } = parseUploadNotice(typeof ev.text === 'string' ? ev.text : '');
      if (id === '' || text === '') return state;
      const items = addQueued(state.items, id, text, seq);
      return items === state.items ? state : { ...state, items };
    }

    // Synthetic, listener-emitted (steering): optio delivered the messages it
    // held, as one prompt. Claude Code holds its own queue, so its listener
    // never emits this; it is here for completeness of the shared contract.
    case 'x-optio-taken': {
      const ids = Array.isArray(ev.ids) ? ev.ids.filter((x: unknown): x is string => typeof x === 'string') : [];
      const items = takeQueuedIds(state.items, ids);
      return items === state.items ? state : { ...state, items, busy: true };
    }

    case 'x-optio-local-error': {
      // A client-side upload failure the view surfaces immediately (transient —
      // not replayed on resume, unlike the successful-filename activity rows).
      const text = typeof ev.text === 'string' ? ev.text : '';
      if (text === '') return state;
      const item: Extract<ChatItem, { kind: 'error' }> = { kind: 'error', text, seq };
      if (typeof ev.time === 'number') item.timestamp = ev.time;
      return { ...state, items: appendItems(state.items, [item]) };
    }

    case 'assistant': {
      // The CLI's own synthetic error message (model "<synthetic>", an
      // `error` field, e.g. "API Error: Can't reach the API server — check
      // your internet or DNS (EAI_AGAIN)"): the is_error result below (see
      // 'result') renders this same text as the single, explained error
      // item. This event must not also open or extend an agent bubble with
      // it — Fix 2 of this branch's manual-test wave (bug predates the
      // branch: main 44441fb8, from 21a9846a).
      if (ev.message?.model === '<synthetic>' && typeof ev.error === 'string') return state;
      const blocks = Array.isArray(ev.message?.content) ? ev.message.content : [];
      const msgId = typeof ev.message?.id === 'string' ? ev.message.id : undefined;
      const at = eventTime(ev, now);
      // This event's own wire time, for the message-time shown under the
      // bubble (see chat.ts): the FIRST such timestamp seen for a message
      // wins (applyBlockText/applyInterruptedText enforce that), so a later
      // event for the same message never overrides it.
      const ts = wireTime(ev) ?? undefined;
      let items = state.items;
      for (const block of blocks) {
        const text = blockText(block);
        if (text !== null) {
          // The agent is answering (or narrating) — complete this block's
          // part of the bubble. After an operator interrupt, the interrupted
          // message's text completes the cut-off bubble in front of its row
          // (merging into the bubble the interrupt itself cut off, an item
          // already tracked — or, if none existed yet, a freshly inserted
          // one, tracked here by its own seq, this event's `seq`).
          if (state.interrupt) {
            items = applyInterruptedText(items, state.interrupt.rowSeq, seq, text, msgId, ts);
            state = noteInterrupted(state, seq);
          } else {
            items = applyBlockText(items, seq, text, msgId, ts);
          }
        } else if (block?.type === 'tool_use') {
          // A persistent row per call; its tool_result (a later user event)
          // finishes it. A call the interrupted turn still announces is
          // stopped before it ran — tracked by its own (fresh) seq.
          if (state.interrupt) {
            const stoppedRow = { ...(toolRow(block, seq, at) as ToolItem), status: 'stopped' as const, endedAt: at };
            items = insertBeforeRow(items, state.interrupt.rowSeq, [stoppedRow]);
            state = noteInterrupted(state, stoppedRow.seq);
          } else {
            items = appendItems(items, [toolRow(block, seq, at)]);
          }
        }
      }
      return items === state.items ? state : { ...state, items };
    }

    case 'stream_event': {
      // While an operator interrupt is in flight the interrupted message's
      // final assistant event carries its text; deltas that raced the
      // interrupt are dropped (replay never has them either).
      if (state.interrupt) return state;
      // A new assistant message announces itself before its deltas. Finalize
      // the previous message's bubble here, or its tail would absorb the next
      // message's deltas ("10" + "9" rendering as "109").
      if (ev.event?.type === 'message_start') {
        const idx = pendingIndex(state.items);
        return idx === -1 ? state : { ...state, items: finalizeAt(state.items, idx) };
      }
      // text_delta carries `text`; thinking_delta carries `thinking` (narration;
      // the hidden reasoning block streams no text). signature/input_json deltas
      // carry neither.
      const d = ev.event?.delta;
      const delta = d?.type === 'thinking_delta' ? d.thinking : d?.text;
      if (typeof delta !== 'string' || delta === '') return state;
      return { ...state, items: appendDelta(state.items, seq, delta) };
    }

    case 'result': {
      const at = eventTime(ev, lastWireTime(state, now));
      if (state.interrupt) {
        const interrupt = state.interrupt;
        state = endInterrupt(state);
        // The turn optio interrupted ends with the CLI's abort error: that is
        // the operator's own interrupt, already shown by its row. No error
        // item; rows still running stop.
        if (ev.subtype === 'error_during_execution' && ABORTED.has(ev.terminal_reason)) {
          const items = freezeRunning(finalizePending(state.items, seq, null), at, 'interrupt');
          return { ...state, items, busy: false };
        }
        // Too late: the turn finished normally after all (see undoInterrupt).
        state = { ...state, items: undoInterrupt(state.items, interrupt) };
      }
      const resultText = typeof ev.result === 'string' ? ev.result : null;
      const items = freezeRunning(state.items, at, 'turn');
      // An API/model error arrives as a result with is_error — surface it as a
      // distinct, explained error item instead of a plain agent bubble. Any
      // pending bubble is finalized in place first (keeping whatever real
      // text it already streamed): the CLI's own synthetic error message is
      // filtered out above, but real narration/text from earlier in the same
      // turn must stay, finalized, not left stuck pending — Fix 2.
      if (ev.is_error) {
        const msg = explainApiError(resultText ?? '', ev.api_error_status);
        const pidx = pendingIndex(items);
        const finalized = pidx === -1 ? items : finalizeAt(items, pidx);
        // 'result' carries no wire timestamp of its own. The reducer must
        // never read the clock: use the latest real user/assistant wire time
        // seen so far (state.lastEventAt), and show no time at all when none
        // has been seen yet — never invent one. `at` (which falls back to
        // `now`) stays reserved for freezeRunning above, which always needs a
        // concrete instant to freeze running tool rows against.
        const item: Extract<ChatItem, { kind: 'error' }> = { kind: 'error', text: msg, seq };
        if (state.lastEventAt !== undefined) item.timestamp = state.lastEventAt;
        return { ...state, items: appendItems(finalized, [item]), busy: false };
      }
      return { ...state, items: finalizePending(items, seq, resultText), busy: false };
    }

    case 'control_request': {
      if (ev.request?.subtype !== 'can_use_tool') return state;
      const item: ChatItem = {
        kind: 'permission',
        requestId: String(ev.request_id),
        toolName: String(ev.request.tool_name ?? ''),
        input: ev.request.input,
        answered: null,
        seq,
      };
      // busy stays true — the agent is parked on the gate.
      return { ...state, items: appendItems(state.items, [item]) };
    }

    case 'x-optio-permission-answered': {
      const requestId = String(ev.request_id);
      const behavior: 'allow' | 'deny' = ev.behavior === 'allow' ? 'allow' : 'deny';
      let changed = false;
      const items = state.items.map((item) => {
        if (item.kind !== 'permission' || item.requestId !== requestId || item.answered !== null) {
          return item;
        }
        changed = true;
        return { ...item, answered: behavior };
      });
      return changed ? { ...state, items } : state;
    }

    case 'x-optio-closed': {
      state = endInterrupt(state);
      // Session ended: stop every running row, background rows included.
      const item: ChatItem = { kind: 'closed', reason: String(ev.reason ?? ''), seq };
      const items = dropUndelivered(freezeRunning(state.items, lastWireTime(state, now), 'session'));
      return { ...state, items: [...items, item], busy: false, closed: true };
    }

    case 'x-optio-resumed': {
      state = endInterrupt(state);
      // Synthetic, listener-emitted after a resumed run's restored history
      // (x-optio-closed is not persisted, so nothing else ends that run here).
      // Its process is gone: stop what it left running, background rows
      // included, and drop a busy flag its unfinished turn left behind. The
      // session itself stays open.
      // Messages the old process held died with it.
      const items = dropUndelivered(freezeRunning(state.items, lastWireTime(state, now), 'session'));
      return { ...state, items, busy: false };
    }

    case 'system': {
      // The CLI brackets every turn with session_state_changed running/idle,
      // including the follow-up turn it starts on its own when a background
      // job finishes (CLI 2.1.269), so busy follows it.
      if (ev.subtype === 'session_state_changed') {
        if (ev.state === 'running') return state.busy ? state : { ...state, busy: true };
        if (ev.state === 'idle') {
          const ended = endInterrupt(state);
          return ended.busy ? { ...ended, busy: false } : ended;
        }
        return state;
      }
      // Background shell commands: task_started marks the Bash row (its
      // immediate tool_result then does not finish it); task_updated carries
      // the exact end time; task_notification ends it. Other system subtypes
      // (init, status, thinking_tokens, ...) are ignored; the model fold above
      // already read init's model.
      if (ev.subtype === 'task_started' && ev.is_backgrounded && typeof ev.tool_use_id === 'string') {
        if (state.finishedTaskIds?.includes(String(ev.task_id))) return state;
        const idx = state.items.findIndex((i) => i.kind === 'tool' && i.callId === ev.tool_use_id);
        if (idx === -1) return state;
        const next: ToolItem = { ...(state.items[idx] as ToolItem), background: true, status: 'running' };
        if (typeof ev.task_id === 'string') next.taskId = ev.task_id;
        delete next.endedAt;
        delete next.result;
        return { ...state, items: replaceAt(state.items, idx, next) };
      }
      if (ev.subtype === 'task_started' && ev.is_backgrounded === false
        && typeof ev.tool_use_id === 'string' && typeof ev.task_id === 'string') {
        // A foreground call run as a task: remember its task id, so its
        // notification (sent when an interrupt stops it) keeps it foreground.
        const idx = state.items.findIndex((i) => i.kind === 'tool' && i.callId === ev.tool_use_id);
        if (idx === -1) return state;
        return { ...state, items: replaceAt(state.items, idx, { ...(state.items[idx] as ToolItem), taskId: ev.task_id }) };
      }
      if (ev.subtype === 'task_updated' && typeof ev.task_id === 'string') {
        // patch.end_time (epoch ms) is the only exact end time the CLI reports
        // for a background task: the notification carries no timestamp.
        const end = ev.patch?.end_time;
        if (typeof end !== 'number' || !Number.isFinite(end)) return state;
        const idx = state.items.findIndex((i) => i.kind === 'tool' && i.taskId === ev.task_id);
        if (idx === -1) return state;
        return { ...state, items: replaceAt(state.items, idx, { ...(state.items[idx] as ToolItem), endedAt: end }) };
      }
      if (ev.subtype === 'task_notification' && typeof ev.task_id === 'string') {
        const toolUseId = typeof ev.tool_use_id === 'string' ? ev.tool_use_id : null;
        let next = applyTaskNotice(
          state,
          { taskId: ev.task_id, toolUseId, status: String(ev.status ?? ''), summary: String(ev.summary ?? '') },
          eventTime(ev, lastWireTime(state, now)),
          seq,
        );
        // A foreground row this notification stopped while an interrupt is in
        // flight is this interrupt's doing (only an interrupt produces a
        // stopped foreground notification) — track it, so a too-late race
        // (undoInterrupt) can put it back to running.
        if (next.interrupt && toolUseId) {
          const row = next.items.find((i) => i.kind === 'tool' && i.callId === toolUseId) as ToolItem | undefined;
          if (row && !row.background && row.status === 'stopped' && row.result === undefined) {
            next = noteInterrupted(next, row.seq);
          }
        }
        return next;
      }
      return state;
    }

    default:
      // x-optio-unparseable, unknown control traffic, etc.
      return state;
  }
}
