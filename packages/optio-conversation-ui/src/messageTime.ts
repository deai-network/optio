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
