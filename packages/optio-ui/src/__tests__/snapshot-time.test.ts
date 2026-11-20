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

  it('answers an empty list without building a formatter', () => {
    // A formatter for this time zone would throw a RangeError.
    expect(formatSnapshotTimes([], 'en-GB', 'No/Such_Zone')).toEqual([]);
  });

  it('takes the default locale for one Intl cannot read', () => {
    const iso = '2026-11-19T13:05:12.345Z';
    const runtimeDefault = new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC',
    }).format(new Date(iso));

    expect(formatSnapshotTimes([iso], '!!', 'UTC')).toEqual([runtimeDefault]);
  });

  it('shows a time that does not parse as it is', () => {
    const iso = '2026-11-19T13:05:12.345Z';

    expect(formatSnapshotTimes(['not a time', iso], 'en-GB', 'UTC')).toEqual(['not a time', short(iso)]);
    expect(formatSnapshotTimes(['not a time', 'not a time'], 'en-GB', 'UTC')).toEqual(['not a time', 'not a time']);
  });
});
