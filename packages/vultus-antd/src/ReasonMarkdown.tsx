import { Markdown } from './Markdown.js';

/** Inline markdown for action disable-reasons inside a Tooltip. Thin preset of <Markdown>. */
export function ReasonMarkdown({ children }: { children: string }) {
  return <Markdown inline>{children}</Markdown>;
}
