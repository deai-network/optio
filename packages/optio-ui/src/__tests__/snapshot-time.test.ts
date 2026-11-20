import { describe, it, expect } from 'vitest';
import { formatSnapshotTimes } from '../snapshot-time.js';

const short = (iso: string) =>
  new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' })
    .format(new Date(iso));

describe('formatSnapshotTimes', () => {
  it('formats each time with the medium date and the short time', () => {
    const times = ['2026-11-19T13:05:12.345Z', '2026-11-19T12:00:00.000Z'];

    expect(formatSnapshotTimes(times, 'en-GB', 'UTC')).toEqual(times.map(short));
  });

  it('shows the seconds of the times whose short forms collide', () => {
    const times = ['2026-11-19T13:05:12.345Z', '2026-11-19T13:05:40.000Z', '2026-11-19T12:00:00.000Z'];

    const formatted = formatSnapshotTimes(times, 'en-GB', 'UTC');

    expect(formatted[0]).toContain('13:05:12');
    expect(formatted[1]).toContain('13:05:40');
    expect(formatted[2]).toBe(short(times[2]));
  });

  it('takes the default locale when none is given', () => {
    expect(() => formatSnapshotTimes(['2026-11-19T13:05:12.345Z'])).not.toThrow();
  });
});
