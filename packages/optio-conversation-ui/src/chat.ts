// Shared engine-neutral chat model: the normalized state every protocol
// adapter reduces its native wire events into, and the shape the generic
// conversation widget renders.

export type ChatItem =
  | {
      kind: 'user';
      text: string;
      seq: number;
      local?: boolean;
      // Steering: a "Send when ready" message the agent has not taken yet.
      // Rendered muted and dashed ("Queued"), pinned at the bottom; it does
      // not count as newer content, so streaming continues above it.
      queued?: boolean;
      // The id optio gave the message (POST /send response, x-optio-queued,
      // x-optio-taken). Kept once the message is taken.
      queueId?: string;
    }
  | {
      kind: 'assistant';
      text: string;
      pending: boolean;
      seq: number;
      msgId: string | null;
      // Reducer-private (claudecode): start offset in `text` of the content
      // block currently streaming via deltas; absent when none is open. Not
      // rendered.
      openPart?: number;
    }
  // muted: a quiet one-line note (e.g. an operator interrupt, an undelivered
  // message) instead of the harness System: bubble.
  | { kind: 'activity'; text: string; seq: number; muted?: boolean }
  | { kind: 'thinking'; text: string; seq: number }
  | {
      kind: 'tool';
      name: string;
      input: unknown;
      seq: number;
      preview?: string;
      // Lifecycle from the ACP `status` (pending/in_progress → running;
      // completed → done; failed → failed). Drives the ⟳/✓/✗ glyph and the
      // verbosity rules (description-while-active hides a tool once it is no
      // longer running; verbose collapses a finished tool). Absent → treated as
      // running (back-compat with engines that don't report status).
      // 'stopped': the call was stopped rather than completing or failing;
      // treated as finished, not failed. claudecode sets it for a background
      // task reported stopped or killed, and for every row still running when
      // the session closes or a resumed run replaces the one that started it.
      status?: 'running' | 'done' | 'failed' | 'stopped';
      // Wire id of the call (claudecode tool_use.id): matches its tool_result
      // and background-task events.
      callId?: string;
      // The call's output (claudecode: tool_result text, or a background task's
      // summary), trimmed; rendered under the args in verbose.
      result?: string;
      // Epoch ms. startedAt drives the live elapsed counter; endedAt freezes it
      // (result, background completion, end of turn, session close).
      startedAt?: number;
      endedAt?: number;
      // A backgrounded shell command: its immediate tool_result does not finish
      // the row; the background-task notification does.
      background?: boolean;
      // The background task's id (claudecode system/task_started task_id):
      // matches its system/task_updated events.
      taskId?: string;
    }
  | {
      kind: 'permission';
      requestId: string;
      toolName: string;
      input: unknown;
      answered: 'allow' | 'deny' | null;
      seq: number;
      // Human-readable detail derived from the ACP `toolCall.content` text when
      // `rawInput` is absent (kimi/cursor permission cards + lazy tool_calls
      // carry the detail only in `content`). Rendered when the input KV is empty.
      preview?: string;
    }
  | { kind: 'error'; text: string; seq: number }
  | { kind: 'closed'; reason: string; seq: number };

// Engine-neutral session control — one live, UI-renderable knob a wrapper
// exposes for its running session (model, thinking effort, mode, ...). Mirrors
// the Python `SessionControl` dataclass in optio_agents.session_controls; the
// `model` selector is just the `id="model"` control.
export interface ControlOption {
  value: string;
  label: string;
  description?: string;
  disabled?: boolean;
  whyDisabled?: string;
}

export interface SessionControl {
  id: string;
  kind: 'select' | 'boolean' | 'segmented' | 'slider';
  label: string;
  value: string | boolean;
  category?: string;
  description?: string;
  options?: ControlOption[]; // kind === 'select'
  levels?: string[]; // kind === 'segmented' | 'slider'
  disabled?: boolean; // whole control unchangeable (e.g. single option)
  whyDisabled?: string; // hover explanation when disabled
}

export interface ChatState {
  items: ChatItem[];
  busy: boolean;
  closed: boolean;
  controls: SessionControl[];
  // Reducer-private (claudecode): background task ids already applied, so the
  // system event and the injected notification turn for one task apply once.
  finishedTaskIds?: string[];
  // Reducer-private (claudecode): the latest user/assistant wire timestamp seen
  // (epoch ms). The time base for events that carry none (system, result,
  // optio's synthetic events), so a replay shows the live durations.
  lastEventAt?: number;
}

type UserItem = Extract<ChatItem, { kind: 'user' }>;

// -- Steering: queued bubbles (engine-neutral; every reducer uses these) ----

export function isQueued(item: ChatItem): item is UserItem {
  return item.kind === 'user' && item.queued === true;
}

// Queued bubbles are pinned at the bottom: new conversation content goes in
// front of the first one.
export function appendItems(items: ChatItem[], rows: ChatItem[]): ChatItem[] {
  if (rows.length === 0) return items;
  const q = items.findIndex(isQueued);
  if (q === -1) return [...items, ...rows];
  return [...items.slice(0, q), ...rows, ...items.slice(q)];
}

// The agent took the queued bubble at idx: it leaves the pinned group and
// lands at the take point (the end of the conversation content), with any
// rows that belong in front of it (an attachment row).
export function takeQueuedAt(items: ChatItem[], idx: number, before: ChatItem[] = []): ChatItem[] {
  const taken: UserItem = { ...(items[idx] as UserItem) };
  delete taken.queued;
  delete taken.local;
  const rest = [...items.slice(0, idx), ...items.slice(idx + 1)];
  return appendItems(rest, [...before, taken]);
}

// x-optio-taken: optio delivered the messages it held; take them in order.
export function takeQueuedIds(items: ChatItem[], ids: readonly string[]): ChatItem[] {
  let out = items;
  for (const id of ids) {
    const idx = out.findIndex((i) => isQueued(i) && i.queueId === id);
    if (idx !== -1) out = takeQueuedAt(out, idx);
  }
  return out;
}

// x-optio-queued: a Send when ready the agent has not taken yet. It
// supersedes the view's local echo of the same id (whichever arrives first
// holds the slot); a message already taken stays taken.
export function addQueued(items: ChatItem[], id: string, text: string, seq: number): ChatItem[] {
  const idx = items.findIndex((i) => i.kind === 'user' && i.queueId === id);
  if (idx === -1) return [...items, { kind: 'user', text, seq, queued: true, queueId: id }];
  const cur = items[idx] as UserItem;
  if (!cur.local) return items;
  const next: UserItem = { ...cur, queued: true };
  delete next.local;
  return [...items.slice(0, idx), next, ...items.slice(idx + 1)];
}

// The session ended (or a resumed run replaced it) before the agent took
// these: nothing will deliver them now. Each becomes a muted note.
export function dropUndelivered(items: ChatItem[]): ChatItem[] {
  if (!items.some(isQueued)) return items;
  const kept: ChatItem[] = [];
  const notes: ChatItem[] = [];
  for (const i of items) {
    if (isQueued(i)) notes.push({ kind: 'activity', text: `Not delivered: ${i.text}`, seq: i.seq, muted: true });
    else kept.push(i);
  }
  return [...kept, ...notes];
}

export const initialChatState: ChatState = {
  items: [],
  busy: false,
  closed: false,
  controls: [],
};

// Merge a live control update into state.controls. Accepts either a full
// snapshot (controls) or a single-value patch ({id, value}); patch updates the
// matching control's value in place, leaving chat items untouched.
export function foldControlUpdate(
  state: ChatState,
  update: { controls?: SessionControl[]; id?: string; value?: string | boolean },
): ChatState {
  if (update.controls) return { ...state, controls: update.controls };
  if (update.id === undefined) return state;
  return {
    ...state,
    controls: state.controls.map((c) =>
      c.id === update.id ? { ...c, value: update.value as string | boolean } : c,
    ),
  };
}
