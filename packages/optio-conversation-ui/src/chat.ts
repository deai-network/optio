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
      // Epoch ms this message was sent, from the wire (or, while still
      // `local`/`queued` live, the view's own send-time echo). Absent when no
      // time is known (e.g. a still-queued bubble in a replay) — never
      // invented. See messageTime.ts.
      timestamp?: number;
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
      // The operator interrupted this answer: it keeps its text and renders
      // with a jagged bottom edge.
      interrupted?: boolean;
      // Epoch ms this message STARTED (Fix 12, owner ruling 2026-09-14: a
      // "HH:MM - HH:MM" interval needs an exact start, not the end Fix 4 used
      // to show here). Priority, set once and never overwritten: (1) the
      // listener's x-optio-message-start ts for this message id, when seen
      // before the bubble opened; (2) otherwise, if the message's first
      // content block is a thinking block, that block's own wire timestamp;
      // (3) otherwise state.lastEventAt as it stood just before the bubble
      // opened (the previous wire event — an approximation, not exact).
      // Absent when none of these exists (the very first message of a
      // conversation, replayed with no marker) — never invented. See
      // messageTime.ts.
      timestamp?: number;
      // Epoch ms of the LAST wire event seen for this message (every
      // assistant event for it updates this, unlike `timestamp` above).
      // Absent until the CLI's own (timestamped) assistant event arrives — a
      // streaming delta carries none. This is what Fix 4 used to store in
      // `timestamp`. See messageTime.ts.
      endTimestamp?: number;
    }
  // muted: a quiet one-line note (e.g. an operator interrupt, an undelivered
  // message) instead of the harness System: bubble.
  | {
      kind: 'activity';
      text: string;
      seq: number;
      muted?: boolean;
      // Epoch ms wire time of the "System: …" echo this row was built from
      // (Fix 9, owner ruling 2026-09-14: "they must show their time"). Set
      // only for a non-muted harness System: row; absent for a muted notice
      // (operator interrupt, undelivered message), a background-task row or
      // an upload notice — never invented. See messageTime.ts.
      timestamp?: number;
      // Fix 13b: set only on a "Not delivered" note created from a
      // command_lifecycle 'cancelled'/'discarded'/'refused' for a queued
      // bubble's own uuid (never on the plain session-end notes dropUndelivered
      // makes) — the uuid the note replaced, so a later x-optio-requeued for
      // the same id can find and reverse it (steering.py's own resend for a
      // "Send now up to" can emit its command_lifecycle 'cancelled' well
      // before the x-optio-requeued that says it was a requeue, not a drop).
      queueId?: string;
    }
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
  | {
      kind: 'error';
      text: string;
      seq: number;
      // Epoch ms this error was produced. See messageTime.ts.
      timestamp?: number;
    }
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
  // Reducer-private (claudecode, Fix 12): the x-optio-message-start marker's
  // {id, ts} once seen, not yet attached to a bubble. Consumed (and cleared)
  // the moment the next assistant bubble opens — live (the first delta) or
  // replay (the first assistant event) alike — as that message's start
  // timestamp. A marker always precedes the stream_event/assistant event(s)
  // for the message it announces, so at most one is ever pending.
  pendingMessageStart?: { id: string; ts: number };
  // Reducer-private (claudecode): set by x-optio-interrupt until the
  // interrupted turn's result; rowSeq is the seq of its "Interrupted by you"
  // row. While set, the CLI's own cancel artefacts are swallowed and the
  // interrupted message's final text lands in front of that row. itemSeqs is
  // every item's own (persistent) seq this exact firing has marked
  // (interrupted:true bubbles, stopped:true tool rows) -- fix round 2: a
  // too-late race (see undoInterrupt) must undo only these, never an
  // unrelated item (e.g. an earlier turn's own, already-finalized interrupt)
  // that merely happens to match the same pattern.
  interrupt?: { rowSeq: number; itemSeqs: number[] };
}

type UserItem = Extract<ChatItem, { kind: 'user' }>;

// The one row an operator interrupt adds (every engine's reducer uses it).
export const INTERRUPTED_BY_YOU = '⏹ Interrupted by you';

// -- Steering: queued bubbles (engine-neutral; every reducer uses these) ----

export function isQueued(item: ChatItem): item is UserItem {
  return item.kind === 'user' && item.queued === true;
}

// Fix 13b: a queued bubble, OR a "Not delivered" note the reducer made from
// a command_lifecycle 'cancelled'/'discarded'/'refused' for one (it keeps
// the old id as `queueId` — see claudecode/events.ts — in case a later
// x-optio-requeued restores it). Both are transient, bottom-pinned
// placeholders for a message that has not yet reached its final resting
// place; new content must go in front of either kind exactly the same way,
// or a message taken/echoed AFTER the note was made would be appended past
// it instead of before it, corrupting the order of already-resolved
// messages relative to the note and breaking the streaming-merge tail check
// for whatever bubble opens next (isTail, claudecode/events.ts).
export function isPinned(item: ChatItem): boolean {
  return isQueued(item) || (item.kind === 'activity' && item.queueId !== undefined);
}

// Queued bubbles (and their "Not delivered" notes, Fix 13b) are pinned at
// the bottom: new conversation content goes in front of the first one.
export function appendItems(items: ChatItem[], rows: ChatItem[]): ChatItem[] {
  if (rows.length === 0) return items;
  const q = items.findIndex(isPinned);
  if (q === -1) return [...items, ...rows];
  return [...items.slice(0, q), ...rows, ...items.slice(q)];
}

// The agent took the queued bubble at idx: it leaves the pinned group and
// lands at the take point (the end of the conversation content), with any
// rows that belong in front of it (an attachment row). `timestamp`, when
// given (the taking echo's own wire time), replaces whatever send-time the
// bubble carried while queued — "once the message is taken, the transcript
// user message shows the echo's wire timestamp".
export function takeQueuedAt(
  items: ChatItem[], idx: number, before: ChatItem[] = [], timestamp?: number,
): ChatItem[] {
  const taken: UserItem = { ...(items[idx] as UserItem) };
  delete taken.queued;
  delete taken.local;
  if (timestamp !== undefined) taken.timestamp = timestamp;
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

// x-optio-queued: either a Send when ready the agent has not taken yet
// (fired while the agent is busy — a genuine queue, pinned and offering
// "Send now"), or interrupt_and_send announcing its own steered text right
// after the turn it interrupted has already ended (final-review M3 of fix
// 8) — by the time that reaches here the agent is idle again (the result
// already cleared `busy`), and the text is already on its way to being sent
// as a new turn, not waiting for one. `busy` (the reducer's own state at the
// moment this event is folded) tells the two apart: only the busy case gets
// the pinned, dashed "Queued" bubble with "Send now" (which would otherwise
// send an empty steer into the turn this message itself just started); the
// idle case is a plain unconfirmed local bubble, exactly as if the view's
// own local echo had created it. Either way it supersedes (or is
// superseded by) the view's local echo of the same id — whichever arrives
// first holds the slot; a message already taken/confirmed stays that way.
export function addQueued(items: ChatItem[], id: string, text: string, seq: number, busy: boolean): ChatItem[] {
  const idx = items.findIndex((i) => i.kind === 'user' && i.queueId === id);
  if (idx === -1) {
    return busy
      ? [...items, { kind: 'user', text, seq, queued: true, queueId: id }]
      : [...items, { kind: 'user', text, seq, local: true, queueId: id }];
  }
  const cur = items[idx] as UserItem;
  if (!cur.local || !busy) return items;
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
