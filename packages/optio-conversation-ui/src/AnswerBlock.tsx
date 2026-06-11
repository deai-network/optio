import { Markdown } from './Markdown.js';

// The one public seam for rendering an assistant answer outside the chat
// widget: markdown with embedded mermaid diagrams and all the rendering
// fixes (stable component map, list spacing, GFM tables, streaming-safe
// diagram fallback) baked in. Consumers pass the answer text and stay
// ignorant of the machinery — improvements land here and flow everywhere.
export function AnswerBlock({ text }: { text: string }) {
  return <Markdown>{text}</Markdown>;
}
