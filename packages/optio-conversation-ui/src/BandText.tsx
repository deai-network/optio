import type { ChatItem, CustomControl, SessionControl } from './chat.js';
import { customOptionLabel } from './CustomControls.js';
import { useLanguage } from './text.js';

// The switch and resume bands' texts (owner request 2026-10-12). A
// task-defined option is named by its label in the UI language, lowercased
// ("Switched to fast mode."). Every band text ends with a period, as the
// resume and compaction bands' do.

type ResumedItem = Extract<ChatItem, { kind: 'resumed' }>;
type SwitchedItem = Extract<ChatItem, { kind: 'switched' }>;

// A switch band: optio's own pick of a task-defined control's option,
// "Switched to <option> mode.". Else (a model switch optio did not ask for, or
// a control or option `customControls` does not have) "Model switched to
// <model>." while the built-in model control is shown (`controls` has it): its
// option's label for the model, else the model's id; "Model switched." when it
// is not shown or the model is not known.
export function switchBandText(
  item: SwitchedItem,
  controls: SessionControl[] | undefined,
  customControls: CustomControl[] | undefined,
  language: string,
): string {
  if (item.control !== undefined && item.option !== undefined) {
    const label = customOptionLabel(customControls, item.control, item.option, language);
    if (label !== undefined) return `Switched to ${label.toLowerCase()} mode.`;
  }
  const modelControl = controls?.find((c) => c.id === 'model');
  if (modelControl === undefined || item.model === undefined) return 'Model switched.';
  const option = modelControl.options?.find((o) => o.value === item.model);
  return `Model switched to ${option?.label ?? item.model}.`;
}

// A resume band: "Session suspended and resumed in <option> mode." for
// the task-defined options the resumed run launched with (several joined
// with ", "); without one `customControls` has, the text as before.
export function resumeBandText(
  item: ResumedItem, customControls: CustomControl[] | undefined, language: string,
): string {
  const labels = (item.options ?? [])
    .map((o) => customOptionLabel(customControls, o.control, o.option, language))
    .filter((label): label is string => label !== undefined)
    .map((label) => label.toLowerCase());
  return labels.length > 0
    ? `Session suspended and resumed in ${labels.join(', ')} mode.`
    : 'Session suspended and resumed.';
}

export function SwitchBandText({ item, controls, customControls }: {
  item: SwitchedItem;
  controls: SessionControl[] | undefined;
  customControls: CustomControl[] | undefined;
}) {
  const language = useLanguage();
  return <>{switchBandText(item, controls, customControls, language)}</>;
}

export function ResumeBandText({ item, customControls }: {
  item: ResumedItem;
  customControls: CustomControl[] | undefined;
}) {
  const language = useLanguage();
  return <>{resumeBandText(item, customControls, language)}</>;
}
