/**
 * A TaskFactory's parameters as its process record carries them, and how
 * optio-ui hands them to vultus. Spec: docs/2026-10-20-task-factory-design.md
 * §1.3 (the parameters), §1.4 (the checks), §3 (the form).
 *
 * A parameter is a vultus-core field definition as plain data, with what
 * vultus-core has no option for: `required` (default true), a text's
 * `maxLength`, and labels (a field's, a choice's) that may be maps of language
 * code to text. optio-core checked the definitions at sync, so they are
 * trusted here; the form (TaskFactoryLaunchModal) checks `required` and
 * `maxLength` itself, and optio-core checks every value again at launch.
 */
import type { TFunction } from 'i18next';
import { initialValueOf, NO_FIELD_CONTEXT, type FieldDefinition } from 'vultus-core';

/** A label: a text, or a map of language code to text. */
export type ParameterLabel = string | Record<string, string>;

export interface ParameterChoice {
  value: string;
  label?: ParameterLabel;
}

interface ParameterBase {
  /** An identifier: a launch refusal names the parameter with it ("<id>: <what>"). */
  id: string;
  label?: ParameterLabel;
  /** Default true: a launch needs a value (see isNoValue). */
  required?: boolean;
}

export type TaskFactoryParameter = ParameterBase & (
  | { kind: 'boolean'; initialValue?: boolean }
  | { kind: 'oneOf'; choices: ParameterChoice[]; initialValue?: string }
  | { kind: 'manyOf'; choices: ParameterChoice[]; initialValue?: string[] }
  | { kind: 'numeric'; min?: number; max?: number; step?: number; integer?: boolean; initialValue?: number }
  | { kind: 'range'; min: number; max: number; step?: number; integer?: boolean; initialValue?: [number, number] }
  | { kind: 'text'; maxLength?: number; initialValue?: string }
);

/** A factory record's parameters (ProcessSchema carries them as `unknown[]`). */
export function parametersOf(process: { parameters?: unknown[] | null }): TaskFactoryParameter[] {
  return (process.parameters ?? []) as TaskFactoryParameter[];
}

/**
 * The text of `label` in the UI's `language`: that language's (or, for a
 * regional code such as `hu-HU`, its base language's), else the English one,
 * else the first. A plain string is the same in every language.
 */
export function resolveLabel(label: ParameterLabel | undefined, language: string | undefined): string | undefined {
  if (label === undefined || typeof label === 'string') return label;
  for (const lang of [language, language?.split('-')[0], 'en']) {
    if (lang && Object.hasOwn(label, lang)) return label[lang];
  }
  return Object.values(label)[0];
}

/** The parameter's label in `language` (resolveLabel); a parameter without one is named by its id. */
export function labelOf(parameter: TaskFactoryParameter, language: string | undefined): string {
  return resolveLabel(parameter.label, language) ?? parameter.id;
}

/**
 * The parameter as vultus-core's FieldDefinition, for vultus-antd's `<Field>`:
 * its label (labelOf) and its choices' labels resolved for `language`,
 * `required` and `maxLength` left out.
 */
export function fieldDefinitionOf(parameter: TaskFactoryParameter, language: string | undefined): FieldDefinition {
  const { required: _required, label: _label, ...options } = parameter;
  const definition: Record<string, unknown> = { ...options, label: labelOf(parameter, language) };
  delete definition.maxLength;
  if (parameter.kind === 'oneOf' || parameter.kind === 'manyOf') {
    definition.choices = parameter.choices.map((c) => (
      c.label === undefined ? { value: c.value } : { value: c.value, label: resolveLabel(c.label, language) }
    ));
  }
  // The kinds and their options are vultus-core's (optio-core refuses any other key at sync).
  return definition as unknown as FieldDefinition;
}

/** The form's starting value: the parameter's `initialValue`, else its kind's own default (vultus-core's). */
export function initialValueOfParameter(parameter: TaskFactoryParameter): unknown {
  return initialValueOf(fieldDefinitionOf(parameter, undefined), NO_FIELD_CONTEXT);
}

/** The id of the built-in field that is not a parameter: whether the launch is removed when it ends. */
export const EPHEMERAL_FIELD_ID = 'ephemeral';

/** The largest of days, hours, minutes, seconds that divides `seconds`, in words. */
function durationInWords(seconds: number, t: TFunction): string {
  const units: [string, number, string, string][] = [
    ['processes.durationDays', 86400, '{{count}} day', '{{count}} days'],
    ['processes.durationHours', 3600, '{{count}} hour', '{{count}} hours'],
    ['processes.durationMinutes', 60, '{{count}} minute', '{{count}} minutes'],
    ['processes.durationSeconds', 1, '{{count}} second', '{{count}} seconds'],
  ];
  const [key, size, one, other] = units.find(([, size]) => seconds % size === 0) ?? units[3];
  return t(key, { count: seconds / size, defaultValue_one: one, defaultValue_other: other });
}

/**
 * The launch form's definitions (spec 2026-10-22-task-factory-launch-options
 * §3.1): the record's parameters (fieldDefinitionOf), then the built-in
 * boolean `ephemeral`, starting checked unless the factory keeps its launches
 * for a TTL (`ttlSeconds`), whose help says for how long.
 */
export function launchFormDefinitions(
  record: { parameters?: unknown[] | null; ttlSeconds?: number | null },
  language: string | undefined,
  t: TFunction,
): FieldDefinition[] {
  const ttl = record.ttlSeconds ?? null;
  const help = ttl === null
    ? t(
      'processes.launchEphemeralHelp',
      'Checked: the launch disappears from the list when it ends. Unchecked: it stays, with its log, until it is removed.',
    )
    : t('processes.launchEphemeralHelpTtl', {
      defaultValue: 'Checked: the launch disappears from the list when it ends. '
        + 'Unchecked: it stays, with its log, until it is removed, at most {{duration}} after it ends.',
      duration: durationInWords(ttl, t),
      interpolation: { escapeValue: false },
    });
  const ephemeral: FieldDefinition = {
    kind: 'boolean',
    id: EPHEMERAL_FIELD_ID,
    widget: 'checkbox',
    label: t('processes.launchEphemeral', 'Remove when finished'),
    description: help,
    initialValue: ttl === null,
  };
  return [...parametersOf(record).map((p) => fieldDefinitionOf(p, language)), ephemeral];
}

/**
 * Whether `value` is no value of `parameter`, as optio-core counts it (a
 * required parameter needs one): null or undefined; for a text the empty
 * string, for a manyOf the empty list.
 */
export function isNoValue(parameter: TaskFactoryParameter, value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (parameter.kind === 'text') return value === '';
  if (parameter.kind === 'manyOf') return Array.isArray(value) && value.length === 0;
  return false;
}

/**
 * The parameter an `invalid-parameters` refusal names, and what is wrong with
 * its value: the message is "<id>: <what>". Null when the message names none of
 * `ids` (the form's parameters) or has no such prefix.
 */
export function refusedParameter(
  message: string | undefined,
  ids: readonly string[],
): { id: string; problem: string } | null {
  const match = message?.match(/^([A-Za-z_][A-Za-z0-9_]*): (.*)$/s);
  if (!match || !ids.includes(match[1])) return null;
  return { id: match[1], problem: match[2] };
}
