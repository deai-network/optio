import { describe, it, expect } from 'vitest';
import { formatMessageTime, formatMessageTimeFull } from '../messageTime.js';

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
