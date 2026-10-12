import type { ChatItem } from './chat.js';

/** React keys for the transcript's rows: a row's kind and seq, numbered when
 *  one event yields several rows of a kind. An item carries its event's seq,
 *  and one event can yield several rows (a prompt sent with an upload becomes
 *  the muted "Attached" row and the prompt's bubble), so seq alone repeats. */
export function transcriptKeys(items: readonly ChatItem[]): string[] {
  const seen = new Map<string, number>();
  return items.map((item) => {
    const base = `${item.kind}-${item.seq}`;
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    return n === 0 ? base : `${base}-${n}`;
  });
}
