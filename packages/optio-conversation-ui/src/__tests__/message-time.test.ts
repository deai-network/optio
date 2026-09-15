import { describe, it, expect } from 'vitest';
import {
  formatMessageTime, formatMessageTimeFull, formatMessageTimeInterval, formatMessageTimeIntervalFull,
} from '../messageTime.js';

// Fixed local Date(...) constructors (not ISO strings) so the expectations
// are built with the same local-date pieces the formatter itself reads —
// this passes under any host timezone, per the plan's test requirement.

describe('formatMessageTime', () => {
  it('a message from today shows HH:MM (24h)', () => {
    const now = new Date(2026, 8, 14, 20, 5).getTime();
    const ts = new Date(2026, 8, 14, 6, 3).getTime();
    expect(formatMessageTime(ts, now)).toBe('06:03');
  });

  it('a message from an earlier day this year shows a short date + time', () => {
    const now = new Date(2026, 8, 14, 10, 0).getTime();
    const ts = new Date(2026, 8, 13, 16, 29).getTime();
    expect(formatMessageTime(ts, now)).toBe('13 Sep 16:29');
  });

  it('a message from a different year also shows the year', () => {
    const now = new Date(2026, 8, 14, 10, 0).getTime();
    const ts = new Date(2024, 8, 13, 16, 29).getTime();
    expect(formatMessageTime(ts, now)).toBe('13 Sep 2024 16:29');
  });

  it('midnight rollover: a message from just after midnight today is "today", not yesterday', () => {
    const now = new Date(2026, 8, 14, 0, 5).getTime();
    const ts = new Date(2026, 8, 14, 0, 1).getTime();
    expect(formatMessageTime(ts, now)).toBe('00:01');
  });

  it('a message a few minutes into tomorrow (relative to now) is not "today"', () => {
    const now = new Date(2026, 8, 14, 23, 55).getTime();
    const ts = new Date(2026, 8, 15, 0, 2).getTime();
    expect(formatMessageTime(ts, now)).toBe('15 Sep 00:02');
  });
});

describe('formatMessageTimeFull', () => {
  it('renders the full local date and time, independent of now', () => {
    const ts = new Date(2026, 8, 13, 16, 29, 0).getTime();
    const full = formatMessageTimeFull(ts);
    // Locale-formatted (toLocaleString) — assert it carries the pieces
    // rather than pinning an exact locale-specific string.
    expect(full).toContain('2026');
    expect(full).toMatch(/16:29|4:29/);
  });
});

// Fix 12 (owner ruling 2026-09-14): a streamed agent message shows a
// "HH:MM - HH:MM" interval when its start and end fall in different local
// minutes, and a single HH:MM otherwise -- dates included exactly where
// formatMessageTime's own rules would add them to either side.
describe('formatMessageTimeInterval', () => {
  it('start and end in the same minute show one time, not a range', () => {
    const now = new Date(2026, 8, 14, 20, 5).getTime();
    const start = new Date(2026, 8, 14, 6, 3, 1).getTime();
    const end = new Date(2026, 8, 14, 6, 3, 59).getTime();
    expect(formatMessageTimeInterval(start, end, now)).toBe('06:03');
  });

  it('start and end in different minutes show "a - b"', () => {
    const now = new Date(2026, 8, 14, 20, 5).getTime();
    const start = new Date(2026, 8, 14, 6, 3).getTime();
    const end = new Date(2026, 8, 14, 6, 9).getTime();
    expect(formatMessageTimeInterval(start, end, now)).toBe('06:03 - 06:09');
  });

  it('an interval spanning midnight dates each side independently, as formatMessageTime would', () => {
    const now = new Date(2026, 8, 15, 10, 0).getTime();
    const start = new Date(2026, 8, 14, 23, 58).getTime();
    const end = new Date(2026, 8, 15, 0, 2).getTime();
    expect(formatMessageTimeInterval(start, end, now)).toBe('14 Sep 23:58 - 00:02');
  });
});

describe('formatMessageTimeIntervalFull', () => {
  it('with no start, shows just the end (independent of now)', () => {
    const end = new Date(2026, 8, 13, 16, 29, 0).getTime();
    expect(formatMessageTimeIntervalFull(undefined, end)).toBe(formatMessageTimeFull(end));
  });

  it('with a start equal to the end, still shows just the one time (no redundant range)', () => {
    const t = new Date(2026, 8, 13, 16, 29, 0).getTime();
    expect(formatMessageTimeIntervalFull(t, t)).toBe(formatMessageTimeFull(t));
  });

  it('with a distinct start, shows the full "start - end" range', () => {
    const start = new Date(2026, 8, 13, 16, 29, 0).getTime();
    const end = new Date(2026, 8, 13, 16, 31, 0).getTime();
    expect(formatMessageTimeIntervalFull(start, end)).toBe(
      `${formatMessageTimeFull(start)} - ${formatMessageTimeFull(end)}`,
    );
  });
});
