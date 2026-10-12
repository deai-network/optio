import { createContext } from 'react';
import { OneOfSegmented, useOneOfField } from 'vultus-antd';
import { CUSTOM_CONTROL_PREFIX, type CustomControl } from './chat.js';
import { type LocalizedText, resolveText, useLanguage } from './text.js';

// What a failed control pick shows (the one definition of each text). Only
// a failure retrying may fix (no answer, an unexpected one) says retry.
export const SWITCH_FAILED_RETRY = 'Switch failed — retry.';
const SWITCH_BUSY = 'Switch failed: the agent is busy.';
const SWITCH_CLOSED = 'Switch failed: the conversation has ended.';
const SWITCH_UNAVAILABLE = 'Switch failed: this option is not available.';

// The text for a failed pick, from the listener's /control answer (null: no
// answer): claude's own message when it refused the switch, else the reason
// in words.
export function controlFailure(body: Record<string, unknown> | null): string {
  if (typeof body?.error === 'string' && body.error) return `Switch failed: ${body.error}`;
  switch (body?.reason) {
    case 'busy':
      return SWITCH_BUSY;
    case 'closed':
      return SWITCH_CLOSED;
    // A control or value this session does not have: retrying cannot help.
    case 'bad-id':
    case 'bad-value':
    case 'not-allowed':
      return SWITCH_UNAVAILABLE;
    default:
      return SWITCH_FAILED_RETRY;
  }
}

// The input bar's error slot (ConversationView hands it to CustomControls as
// onError), for an engine view around ConversationView to show its built-in
// controls' failures in too: it provides a ref, which ConversationView fills
// while mounted.
export type ControlErrorSink = { current: ((message: string | null) => void) | null };
export const ControlErrorSlot = createContext<ControlErrorSink | null>(null);

// A task-defined text (text.ts: the shared LocalizedText) in `language`, or
// undefined without one.
function optionalText(text: LocalizedText | null | undefined, language: string): string | undefined {
  return text === null || text === undefined ? undefined : resolveText(text, language);
}

// The label of a custom control's option in `language`; undefined for a
// control or option not in `controls`.
export function customOptionLabel(
  controls: CustomControl[] | undefined, controlId: string, optionValue: string, language: string,
): string | undefined {
  const option = controls?.find((c) => c.id === controlId)?.options.find((o) => o.value === optionValue);
  return option ? resolveText(option.label, language) : undefined;
}

function CustomOneOf({ control: c, language, disabled, onPick }: {
  control: CustomControl;
  language: string;
  disabled: boolean;
  onPick: (id: string, value: string) => Promise<void>;
}) {
  const description = optionalText(c.description, language);
  const field = useOneOfField<string>({
    id: `${CUSTOM_CONTROL_PREFIX}${c.id}`,
    // The accessible name: the caption, else the description, else the id.
    label: optionalText(c.label, language) ?? description ?? c.id,
    description,
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
// name). A control with no label has no caption; its description, else its
// id, is its accessible name. Option descriptions are the segments' tooltips.
// Texts follow the UI language. Disabled while a turn runs or the
// conversation is closed. A pick calls onChange, which resolves to null once
// applied, else to why not, shown through onError (the input bar's error
// slot, also offered through ControlErrorSlot). Every control carries a
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
      failure = SWITCH_FAILED_RETRY;
    }
    if (failure) onError(failure);
  };
  return (
    <>
      {controls.map((c) => {
        const caption = optionalText(c.label, language);
        return (
          <span key={c.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            {caption !== undefined ? (
              <span className="optio-cc-control-label" style={{ fontSize: 12, opacity: 0.65, whiteSpace: 'nowrap' }}>
                {caption}
              </span>
            ) : null}
            <CustomOneOf control={c} language={language} disabled={disabled} onPick={pick} />
          </span>
        );
      })}
    </>
  );
}
