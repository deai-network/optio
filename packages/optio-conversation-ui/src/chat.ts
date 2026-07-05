// Shared engine-neutral chat model: the normalized state every protocol
// adapter reduces its native wire events into, and the shape the generic
// conversation widget renders.

export type ChatItem =
  | { kind: 'user'; text: string; seq: number; local?: boolean }
  | { kind: 'assistant'; text: string; pending: boolean; seq: number; msgId: string | null }
  | { kind: 'activity'; text: string; seq: number }
  | { kind: 'thinking'; text: string; seq: number }
  | { kind: 'tool'; name: string; input: unknown; seq: number }
  | {
      kind: 'permission';
      requestId: string;
      toolName: string;
      input: unknown;
      answered: 'allow' | 'deny' | null;
      seq: number;
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
  kind: 'select' | 'boolean' | 'segmented';
  label: string;
  value: string | boolean;
  category?: string;
  description?: string;
  options?: ControlOption[]; // kind === 'select'
  levels?: string[]; // kind === 'segmented'
  disabled?: boolean; // whole control unchangeable (e.g. single option)
  whyDisabled?: string; // hover explanation when disabled
}

export interface ChatState {
  items: ChatItem[];
  busy: boolean;
  closed: boolean;
  controls: SessionControl[];
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
