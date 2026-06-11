// Shared engine-neutral chat model: the normalized state every protocol
// adapter reduces its native wire events into, and the shape the generic
// conversation widget renders.

export type ChatItem =
  | { kind: 'user'; text: string; seq: number; local?: boolean }
  | { kind: 'assistant'; text: string; pending: boolean; seq: number; msgId: string | null }
  | { kind: 'activity'; text: string; seq: number }
  | { kind: 'tool'; name: string; input: unknown; seq: number }
  | {
      kind: 'permission';
      requestId: string;
      toolName: string;
      input: unknown;
      answered: 'allow' | 'deny' | null;
      seq: number;
    }
  | { kind: 'closed'; reason: string; seq: number };

export interface ChatState {
  items: ChatItem[];
  busy: boolean;
  closed: boolean;
}

export const initialChatState: ChatState = {
  items: [],
  busy: false,
  closed: false,
};
