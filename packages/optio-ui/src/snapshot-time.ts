/**
 * The times of a process's snapshots (`useProcessSnapshots`' `capturedAt`s) as a launch menu names
 * them: the medium date and the short time, in `locale` and `timeZone` (the runtime's when left
 * out). An entry whose short form equals another entry's shows the seconds too (`timeStyle:
 * 'medium'`), so two snapshots of the same minute read differently.
 *
 * It runs during render, so it throws nothing for what it is given: an empty list builds no
 * formatter, a locale Intl cannot read gives the runtime's default, and a `capturedAt` that does
 * not parse shows as it is.
 */
export function formatSnapshotTimes(
  capturedAts: readonly string[],
  locale?: string,
  timeZone?: string,
): string[] {
  if (capturedAts.length === 0) return [];
  const dates = capturedAts.map((capturedAt) => new Date(capturedAt));
  const parsed = (i: number) => !Number.isNaN(dates[i].getTime());
  const short = dateTimeFormat(locale, 'short', timeZone);
  const shortForms = dates.map((date, i) => (parsed(i) ? short.format(date) : capturedAts[i]));
  const collides = (i: number) => parsed(i) && shortForms.some((other, j) => j !== i && other === shortForms[i]);
  if (!shortForms.some((_, i) => collides(i))) return shortForms;
  const medium = dateTimeFormat(locale, 'medium', timeZone);
  return shortForms.map((form, i) => (collides(i) ? medium.format(dates[i]) : form));
}

// A locale Intl cannot read (a RangeError) falls back to the runtime's default.
function dateTimeFormat(
  locale: string | undefined,
  timeStyle: 'short' | 'medium',
  timeZone: string | undefined,
): Intl.DateTimeFormat {
  const options: Intl.DateTimeFormatOptions = { dateStyle: 'medium', timeStyle, timeZone };
  try {
    return new Intl.DateTimeFormat(locale, options);
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
    return new Intl.DateTimeFormat(undefined, options);
  }
}
