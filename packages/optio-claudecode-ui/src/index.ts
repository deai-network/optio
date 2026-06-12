export { ClaudeCodeConversationWidget, registerClaudeCodeConversationWidget } from './ClaudeCodeConversationWidget.js';
export { reduceEvent, initialChatState } from './events.js';
export type { ChatItem, ChatState } from './events.js';
// The standalone answer renderer (markdown + mermaid + all rendering fixes),
// for consumers that render answers outside the chat widget.
export { AnswerBlock } from './AnswerBlock.js';
// One-line tool-call summary (salient key from the input), for consumers that
// render their own activity feed outside the chat widget.
export { toolSummary } from './toolSummary.js';
