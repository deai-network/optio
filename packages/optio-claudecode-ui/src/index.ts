export { ClaudeCodeConversationWidget, registerClaudeCodeConversationWidget } from './ClaudeCodeConversationWidget.js';
export { reduceEvent, initialChatState } from './events.js';
export type { ChatItem, ChatState } from './events.js';
// Standalone rendering building blocks, reusable outside the widget
// (markdown with embedded mermaid diagrams, streaming-safe).
export { Markdown } from './Markdown.js';
export { Mermaid } from './Mermaid.js';
