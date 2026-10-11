import { OneOfSegmented, useOneOfField } from 'vultus-antd';
import { CUSTOM_CONTROL_PREFIX, type CustomControl } from './chat.js';
import { type LocalizedText, resolveText, useLanguage } from './text.js';

// A task-defined text (text.ts: the shared LocalizedText) in `language`, or
// undefined without one.
function optionalText(text: LocalizedText | null | undefined, language: string): string | undefined {
  return text === null || text === undefined ? undefined : resolveText(text, language);
}

function CustomOneOf({ control: c, language, disabled, onPick }: {
  control: CustomControl;
  language: string;
  disabled: boolean;
  onPick: (id: string, value: string) => Promise<void>;
}) {
  const field = useOneOfField<string>({
    id: `${CUSTOM_CONTROL_PREFIX}${c.id}`,
    label: resolveText(c.label, language),
    description: optionalText(c.description, language),
    enabled: !disabled,
    choices: c.options.map((o) => ({
      value: o.value,
      label: resolveText(o.label, language),
      description: optionalText(o.description, language),
    })),
    // No option matches the running settings: '' highlights no segment.
    value: c.value ?? '',
    // Awaited, so the control stays locked until the listener answers.
    commit: { fire: (next) => onPick(c.id, next) },
  });
  return (
    <span data-testid={`custom-control-${c.id}`} style={{ alignSelf: 'center' }}>
      <OneOfSegmented field={field} size="small" />
    </span>
  );
}

// Task-defined session controls (widgetData.customControls), each a vultus
// OneOfSegmented led by its muted caption, as SessionControls renders its
// controls: the caption carries `optio-cc-control-label`, so the toolbar's
// compact mode hides it too (the segmented keeps the label as its accessible
// name). Option descriptions are the segments' tooltips. Texts follow the UI
// language. Disabled while a turn runs or the conversation is closed. A pick
// calls onChange, which resolves to null once applied, else to why not, shown
// through onError (the input bar's error slot). Every control carries a
// `custom-control-<id>` data-testid.
export function CustomControls({ controls, disabled, onChange, onError }: {
  controls: CustomControl[];
  disabled: boolean;
  onChange: (id: string, value: string) => Promise<string | null>;
  onError: (message: string | null) => void;
}) {
  const language = useLanguage();
  if (!controls.length) return null;
  const pick = async (id: string, value: string) => {
    onError(null);
    let failure: string | null;
    try {
      failure = await onChange(id, value);
    } catch {
      failure = 'Switch failed — retry.';
    }
    if (failure) onError(failure);
  };
  return (
    <>
      {controls.map((c) => (
        <span key={c.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span className="optio-cc-control-label" style={{ fontSize: 12, opacity: 0.65, whiteSpace: 'nowrap' }}>
            {resolveText(c.label, language)}
          </span>
          <CustomOneOf control={c} language={language} disabled={disabled} onPick={pick} />
        </span>
      ))}
    </>
  );
}
