// Shared engine-neutral chat model: the normalized state every protocol
// adapter reduces its native wire events into, and the shape the generic
// conversation widget renders.

export type ChatItem =
  | { kind: 'user'; text: string; seq: number; local?: boolean }
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
  | { kind: 'activity'; text: string; seq: number }
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
      // 'stopped' (claudecode background tasks only): the job was stopped or
      // killed rather than completing or failing; treated as finished, not
      // failed.
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
