// Pure event reducer: raw Claude Code stream-json events -> ChatState.
//
// All Claude-specific interpretation lives here (testable without DOM):
// the listener and the widget transport pass raw events through untouched.
// Wire shapes per the Phase I conversation-gate design (system / user /
// assistant / result / control_request / x-optio-* synthetic events;
// partials arrive as {type:"stream_event", event:{...content_block_delta}}).

import type { ChatItem, ChatState } from '../chat.js';
import { foldControlUpdate } from '../chat.js';
import { explainApiError } from '../apiError.js';
import { parseUploadNotice, uploadNoticeActivityText } from '../uploads.js';
export { initialChatState } from '../chat.js';

const HARNESS_PREFIX = 'System: ';

// Parts of one assistant message (narration, then the answer) render in one
// bubble, separated by a blank line.
const PART_SEPARATOR = '\n\n';

type AssistantItem = Extract<ChatItem, { kind: 'assistant' }>;

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
  const row: ToolItem = { kind: 'tool', name: String(block.name ?? ''), input: block.input, seq, startedAt: at };
  if (typeof block.id === 'string') row.callId = block.id;
  return row;
}

// Apply tool_result blocks to their rows (matched by tool_use_id). A background
// row ignores its immediate result: the task notification finishes it.
function applyToolResults(items: ChatItem[], content: unknown, at: number): ChatItem[] {
  if (!Array.isArray(content)) return items;
  let out = items;
  for (const b of content) {
    if (b?.type !== 'tool_result' || typeof b.tool_use_id !== 'string') continue;
    const idx = out.findIndex((i) => i.kind === 'tool' && i.callId === b.tool_use_id);
    if (idx === -1) continue;
    const row = out[idx] as ToolItem;
    if (row.background) continue;
    out = replaceAt(out, idx, {
      ...row,
      status: b.is_error ? 'failed' : 'done',
      result: trimResult(toolResultText(b.content)),
      endedAt: at,
    });
  }
  return out;
}

// Stop the counters of rows still running: at the end of a turn (background
// rows keep counting: their task outlives the turn) or at session close (all).
function freezeRunning(items: ChatItem[], at: number, includeBackground: boolean): ChatItem[] {
  let changed = false;
  const out = items.map((i) => {
    if (i.kind !== 'tool' || i.startedAt === undefined || i.endedAt !== undefined) return i;
    if (i.status === 'done' || i.status === 'failed') return i;
    if (i.background && !includeBackground) return i;
    changed = true;
    return { ...i, endedAt: at };
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
  if (idx !== -1) {
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
    const text =
      toolStatus === 'done'
        ? `✓ Background task finished: ${n.summary}`
        : toolStatus === 'failed'
          ? `✗ Background task failed: ${n.summary}`
          : `⏹ Background task stopped: ${n.summary}`;
    items = [...state.items, { kind: 'activity', text, seq }];
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

function pendingIndex(items: ChatItem[]): number {
  return items.findIndex((item) => item.kind === 'assistant' && item.pending);
}

// The pending bubble may keep absorbing the in-flight turn only while it is
// the conversation's tail. Tool rows don't count: they are progress rows, not
// newer conversation content. Anything else after the bubble (activity rows,
// permission cards, user turns) means newer content has been appended — the
// bubble is stale and must not act as an anchor anymore.
function isTail(items: ChatItem[], idx: number): boolean {
  return items.slice(idx + 1).every((i) => i.kind === 'tool');
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
  return [...items, { kind: 'assistant', text: delta, pending: true, seq, msgId: null, openPart: 0 }];
}

// A content block's final assistant event (it carries the block's full text).
// Within the same message it replaces the part its deltas streamed, or appends
// a new part when nothing streamed (replays hold no stream_events). A different
// message, or a pending bubble that is no longer the tail, opens a fresh bubble.
function applyBlockText(items: ChatItem[], seq: number, text: string, msgId?: string): ChatItem[] {
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
      delete next.openPart;
      return replaceAt(items, idx, next);
    }
    items = finalizeAt(items, idx);
  }
  return [...items, { kind: 'assistant', text, pending: true, seq, msgId: msgId ?? null }];
}

// Finalize the in-flight assistant bubble (pending -> false). The result text
// replaces the bubble's text unless the bubble already ends with it: narration
// parts earlier in the same message must survive the end of the turn. Creates
// a finalized bubble if there is result text but no pending bubble (e.g. a
// replay that skipped partials).
function finalizePending(items: ChatItem[], seq: number, resultText: string | null): ChatItem[] {
  const idx = pendingIndex(items);
  if (idx === -1) {
    if (resultText === null || resultText === '') return items;
    return [...items, { kind: 'assistant', text: resultText, pending: false, seq, msgId: null }];
  }
  const current = items[idx] as AssistantItem;
  const keep = resultText === null || resultText === '' || current.text.endsWith(resultText);
  const next: AssistantItem = { ...current, text: keep ? current.text : resultText, pending: false };
  delete next.openPart;
  return replaceAt(items, idx, next);
}

// Insert a user message before the assistant bubble it triggered. With
// `--replay-user-messages` Claude streams the whole answer FIRST and only
// echoes the user message afterward, so the streaming assistant bubble already
// exists (and has an earlier seq) when the user echo arrives. Ordering by seq
// — or appending on arrival — would therefore render the answer above the
// question. Conversation order is what we want, so the echoed user turn slots
// in front of the in-flight assistant bubble — but ONLY while that bubble is
// the conversation's tail (modulo ephemeral tool rows). A stale pending
// bubble (e.g. replayed from a buffer captured mid-turn, never finalized)
// must not pull later, unrelated user events above newer content.
function insertBeforePending(items: ChatItem[], rows: ChatItem[]): ChatItem[] {
  const idx = pendingIndex(items);
  if (idx === -1 || !isTail(items, idx)) return [...items, ...rows];
  return [...items.slice(0, idx), ...rows, ...items.slice(idx)];
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
      const withResults = applyToolResults(state.items, ev.message?.content, eventTime(ev, now));
      if (withResults !== state.items) state = { ...state, items: withResults };
      // A background command ended and the CLI injected its notification as a
      // user turn: apply it to the Bash row; never render it as a user bubble.
      // Trust `origin` when the CLI sends it: only an origin explicitly tagged
      // task-notification is treated as one, so a genuine user prompt that
      // happens to start with the literal text renders normally. Fall back to
      // the text-prefix sniff only for older replays that carry no origin.
      const rawText = extractText(ev.message?.content);
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
      // just its attachment row. Either way the agent is working.
      if (text === '' || text.startsWith(HARNESS_PREFIX)) {
        let items = attach ? [...state.items, attach] : state.items;
        if (text !== '') items = [...items, { kind: 'activity', text, seq }];
        return { ...state, items, busy: true };
      }
      // Wire echo of an optimistically-rendered local message (sent from this
      // widget): confirm the local bubble in place instead of inserting a
      // duplicate (the attachment row slots just before it). FIFO by text.
      const localIdx = state.items.findIndex(
        (i) => i.kind === 'user' && i.local === true && i.text === text,
      );
      if (localIdx !== -1) {
        const confirmed = { ...state.items[localIdx] } as Extract<ChatItem, { kind: 'user' }>;
        delete confirmed.local;
        const items = attach
          ? [...state.items.slice(0, localIdx), attach, confirmed, ...state.items.slice(localIdx + 1)]
          : [...state.items.slice(0, localIdx), confirmed, ...state.items.slice(localIdx + 1)];
        return { ...state, items, busy: true };
      }
      // Replayed / un-echoed prompt: slot the attachment row + user bubble in
      // front of the in-flight assistant bubble (the answer streams first).
      const rows: ChatItem[] = attach
        ? [attach, { kind: 'user', text, seq }]
        : [{ kind: 'user', text, seq }];
      return { ...state, items: insertBeforePending(state.items, rows), busy: true };
    }

    // Synthetic, widget-emitted: render the operator's own message the moment
    // it is accepted by the listener, instead of waiting for Claude's echo
    // (which only arrives after the answer starts streaming).
    case 'x-optio-local-user': {
      const text = typeof ev.text === 'string' ? ev.text : '';
      if (text === '') return state;
      return {
        ...state,
        items: [...state.items, { kind: 'user', text, seq, local: true }],
        busy: true,
      };
    }

    case 'x-optio-local-error': {
      // A client-side upload failure the view surfaces immediately (transient —
      // not replayed on resume, unlike the successful-filename activity rows).
      const text = typeof ev.text === 'string' ? ev.text : '';
      if (text === '') return state;
      return { ...state, items: [...state.items, { kind: 'error', text, seq }] };
    }

    case 'assistant': {
      const blocks = Array.isArray(ev.message?.content) ? ev.message.content : [];
      const msgId = typeof ev.message?.id === 'string' ? ev.message.id : undefined;
      const at = eventTime(ev, now);
      let items = state.items;
      for (const block of blocks) {
        const text = blockText(block);
        if (text !== null) {
          // The agent is answering (or narrating) — complete this block's
          // part of the bubble.
          items = applyBlockText(items, seq, text, msgId);
        } else if (block?.type === 'tool_use') {
          // A persistent row per call; its tool_result (a later user event)
          // finishes it.
          items = [...items, toolRow(block, seq, at)];
        }
      }
      return items === state.items ? state : { ...state, items };
    }

    case 'stream_event': {
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
      const resultText = typeof ev.result === 'string' ? ev.result : null;
      const items = freezeRunning(state.items, eventTime(ev, lastWireTime(state, now)), false);
      // An API/model error arrives as a result with is_error — surface it as a
      // distinct, explained error item instead of a plain agent bubble.
      if (ev.is_error) {
        const msg = explainApiError(resultText ?? '', ev.api_error_status);
        return { ...state, items: [...items, { kind: 'error', text: msg, seq }], busy: false };
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
      return { ...state, items: [...state.items, item] };
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
      // Session ended: stop every running counter, background rows included.
      const item: ChatItem = { kind: 'closed', reason: String(ev.reason ?? ''), seq };
      const items = freezeRunning(state.items, lastWireTime(state, now), true);
      return { ...state, items: [...items, item], busy: false, closed: true };
    }

    case 'system': {
      // The CLI brackets every turn with session_state_changed running/idle,
      // including the follow-up turn it starts on its own when a background
      // job finishes (CLI 2.1.269), so busy follows it.
      if (ev.subtype === 'session_state_changed') {
        if (ev.state === 'running') return state.busy ? state : { ...state, busy: true };
        if (ev.state === 'idle') return state.busy ? { ...state, busy: false } : state;
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
        return applyTaskNotice(
          state,
          {
            taskId: ev.task_id,
            toolUseId: typeof ev.tool_use_id === 'string' ? ev.tool_use_id : null,
            status: String(ev.status ?? ''),
            summary: String(ev.summary ?? ''),
          },
          eventTime(ev, lastWireTime(state, now)),
          seq,
        );
      }
      return state;
    }

    default:
      // x-optio-unparseable, unknown control traffic, etc.
      return state;
  }
}
