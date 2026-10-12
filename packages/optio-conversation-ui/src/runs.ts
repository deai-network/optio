import { hasResumeNotice, isPinned, type ChatItem } from './chat.js';

// Empty runs (owner request 2026-10-12): a session suspended and resumed again
// and again leaves a run per resume that holds nothing but the resume band, the
// harness "System: you have been resumed" row and the agent's "ready" reply.
// With ConversationView's hideEmptyRuns those runs are left out.
//
// A run is the transcript from one resume band to the next. The first run,
// before any band, is always shown. A resumed run is empty when every item in
// it is one of: its band, a System row carrying the resume notice, the agent's
// text, its reasoning, or a 'closed' row. Anything else (an operator message,
// a tool call, a permission or question card, an error, a switch or
// compaction band, any other notice) means something happened, and the run
// shows. Items pinned below the conversation (queued messages and their notes)
// are never left out and do not count.

function neutral(item: ChatItem): boolean {
  switch (item.kind) {
    case 'resumed':
    case 'assistant':
    case 'thinking':
    case 'closed':
      return true;
    case 'activity':
      return item.system === true && hasResumeNotice(item.text);
    default:
      return false;
  }
}

/** `items` without its empty resumed runs. `keepLastRun`: the last run shows
 *  even when empty (a live session's current run). Returns `items` itself
 *  when nothing is left out. */
export function withoutEmptyRuns(items: ChatItem[], keepLastRun: boolean): ChatItem[] {
  // The index each run starts at: 0, then every resume band.
  const starts = [0];
  items.forEach((item, i) => {
    if (item.kind === 'resumed' && i > 0) starts.push(i);
  });
  const hidden = new Set<number>();
  starts.forEach((start, r) => {
    if (items[start]?.kind !== 'resumed') return; // the first run, before any band
    const last = r === starts.length - 1;
    if (last && keepLastRun) return;
    const end = last ? items.length : starts[r + 1];
    const run = items.slice(start, end).filter((item) => !isPinned(item));
    if (run.every(neutral)) for (let i = start; i < end; i++) hidden.add(i);
  });
  if (hidden.size === 0) return items;
  return items.filter((item, i) => !hidden.has(i) || isPinned(item));
}
