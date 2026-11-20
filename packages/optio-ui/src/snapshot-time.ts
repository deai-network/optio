/**
 * The times of a process's snapshots (`useProcessSnapshots`' `capturedAt`s) as a launch menu names
 * them: the medium date and the short time, in `locale` and `timeZone` (the runtime's when left
 * out). An entry whose short form equals another entry's shows the seconds too (`timeStyle:
 * 'medium'`), so two snapshots of the same minute read differently.
 */
export function formatSnapshotTimes(
  capturedAts: readonly string[],
  locale?: string,
  timeZone?: string,
): string[] {
  const short = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short', timeZone });
  const shortForms = capturedAts.map((capturedAt) => short.format(new Date(capturedAt)));
  const collides = (form: string, i: number) => shortForms.some((other, j) => j !== i && other === form);
  if (!shortForms.some(collides)) return shortForms;
  const medium = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'medium', timeZone });
  return shortForms.map((form, i) => (collides(form, i) ? medium.format(new Date(capturedAts[i])) : form));
}
