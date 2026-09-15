// Small pure formatter for the timestamp shown under a message bubble. Takes
// both the message's own epoch-ms timestamp and the current time (`now`) as
// plain arguments — it never reads the clock itself, so the same (timestamp,
// now) pair renders identically live or on replay, at any point later. The
// view passes the current time at render time.

// "HH:MM" (24h, local) for a message from today (same local Y/M/D as `now`);
// otherwise a short local date ("13 Sep", plus the year when it isn't `now`'s
// year) followed by the same "HH:MM".
export function formatMessageTime(timestamp: number, now: number): string {
  const d = new Date(timestamp);
  const n = new Date(now);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const time = `${hh}:${mm}`;
  const sameDay =
    d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate();
  if (sameDay) return time;
  const day = d.getDate();
  const month = d.toLocaleString('en-US', { month: 'short' });
  const year = d.getFullYear() === n.getFullYear() ? '' : ` ${d.getFullYear()}`;
  return `${day} ${month}${year} ${time}`;
}

// Full local date and time, for the hover title. Independent of `now`.
export function formatMessageTimeFull(timestamp: number): string {
  return new Date(timestamp).toLocaleString();
}

// Fix 12 (owner ruling 2026-09-14): a streamed agent message carries a START
// and an END time; the label reads "HH:MM - HH:MM" when they fall in
// different local minutes, and a single "HH:MM" when they don't. Reusing
// formatMessageTime for each side (rather than a separate same-minute check)
// means a day/year boundary between start and end is dated on whichever side
// it falls on, exactly as formatMessageTime already does for a lone
// timestamp — and "different minutes" falls out for free: two instants
// render to the same minute-granularity string only when they share it.
export function formatMessageTimeInterval(start: number, end: number, now: number): string {
  const a = formatMessageTime(start, now);
  const b = formatMessageTime(end, now);
  return a === b ? a : `${a} - ${b}`;
}

// Full local date and time for the hover title (see renderTimeLabel in
// ConversationView.tsx). `start` is optional (see chat.ts: an assistant item
// may have an end with no known start) and independent of `now`, like
// formatMessageTimeFull. Collapses to the single end time when there is no
// start, or the start equals the end — never a redundant "x - x".
export function formatMessageTimeIntervalFull(start: number | undefined, end: number): string {
  if (start === undefined || start === end) return formatMessageTimeFull(end);
  return `${formatMessageTimeFull(start)} - ${formatMessageTimeFull(end)}`;
}
