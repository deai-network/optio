import { useMemo, type SyntheticEvent } from 'react';
import { Alert, Form, Modal, Space } from 'antd';
import type { FormRule } from 'antd';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import type { Process } from 'optio-contracts';
import {
  calculateVisible, initialValueOf, resolveIn, useAction, useFieldContext, type FieldCtx, type FieldDefinition,
} from 'vultus-core';
import { ActionButton, Field, FormPanel, FormSubmitButton, type FieldProps } from 'vultus-antd';
import { useTaskFactoryLaunchAction, type TaskFactoryRecord } from '../hooks/useTaskFactoryLaunch.js';
import { isNoValue, launchFormDefinitions, parametersOf, type TaskFactoryParameter } from '../task-factory.js';
import { mergeOverrides, type TaskFactoryOverrides } from '../task-factory-overrides.js';

export interface TaskFactoryLaunchModalProps {
  /** The factory's record: its `_id`, `name`, `warning` and `parameters`. */
  process: TaskFactoryRecord;
  open: boolean;
  /** Called to close it: Cancel, the close icon, Escape, and after a launch. */
  onClose: () => void;
  /** Called with the launched child once the launch is accepted (the modal then closes). */
  onLaunched?: (child: Process) => void;
  /**
   * Per field id (a parameter's, or `ephemeral`), a partial vultus-core definition merged into
   * that field's (`mergeOverrides`): e.g. `{ visible: false, initialValue: false }` hides a
   * field and launches with its value. An id the form lacks, or a `kind`/`id` override, throws.
   */
  overrides?: TaskFactoryOverrides;
}

// Events inside the modal bubble, along React's tree, to the handlers around
// the launch control that opened it: a process tree's keyboard navigation
// would take the arrow keys, Home and End typed in a field, a clickable row a
// click in the form. They stop at the modal.
const stop = (e: SyntheticEvent) => e.stopPropagation();

/**
 * The launch form of a task factory (spec 2026-10-20-task-factory §3): the
 * factory's `warning` on top, then one vultus-antd `Field` per parameter
 * (labels in the UI language, starting at the parameters' initial values),
 * and the launch, a vultus action. A refusal shows in the form (an
 * `invalid-parameters` one on the field it names), the values entered stay;
 * a launch closes it and hands the child to `onLaunched`. Each opening starts
 * from the initial values again.
 */
export function TaskFactoryLaunchModal({
  process, open, onClose, onLaunched, overrides,
}: TaskFactoryLaunchModalProps) {
  const { t } = useTranslation();
  return (
    <span style={{ display: 'contents' }} onClick={stop} onMouseDown={stop} onKeyDown={stop}>
      <Modal
        open={open}
        title={t('processes.factoryTitle', {
          defaultValue: 'Launch “{{name}}”',
          name: process.name,
          interpolation: { escapeValue: false },
        })}
        onCancel={onClose}
        footer={null}
        destroyOnHidden
      >
        <FormPanel layout="vertical">
          <LaunchForm process={process} onClose={onClose} onLaunched={onLaunched} overrides={overrides} />
        </FormPanel>
      </Modal>
    </span>
  );
}

// Inside the FormPanel, as its actions must be: that is where a refusal finds the form.
function LaunchForm({ process, onClose, onLaunched, overrides }: Omit<TaskFactoryLaunchModalProps, 'open'>) {
  const { t, i18n } = useTranslation();
  const ctx = useFieldContext();
  // Throws, as it renders, for an override of a field the form lacks or of a kind or id.
  const definitions = useMemo(
    () => mergeOverrides(launchFormDefinitions(process, i18n.language, t), overrides),
    [process, i18n.language, t, overrides],
  );
  const items = definitions.map((definition) => resolveItem(definition, ctx));
  const hiddenLabels = Object.fromEntries(items.filter((i) => !i.visible).map((i) => [i.definition.id, i.label]));
  const launch = useTaskFactoryLaunchAction(process, {
    onLaunched: (child) => {
      onLaunched?.(child);
      onClose();
    },
    hiddenLabels,
  });
  const cancel = useAction({
    id: 'optio.taskFactory.cancel',
    label: t('processes.factoryCancel', { defaultValue: 'Cancel' }),
    fire: () => onClose(),
  });
  return (
    <>
      {process.warning && <Alert type="warning" showIcon title={process.warning} style={{ marginBottom: 16 }} />}
      {items.map((item) => (
        <FieldItem
          key={item.definition.id}
          item={item}
          parameter={parametersOf(process).find((p) => p.id === item.definition.id)}
        />
      ))}
      <Space style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <ActionButton action={cancel} />
        <FormSubmitButton action={launch} />
      </Space>
    </>
  );
}

/** A merged definition with its label, visibility and initial value as vultus resolves them. */
interface FormItem {
  definition: FieldDefinition;
  label: string;
  visible: boolean;
  initialValue: unknown;
}

function resolveItem(definition: FieldDefinition, ctx: FieldCtx): FormItem {
  const where = (name: string) => `Field '${definition.id}': option '${name}'`;
  return {
    definition,
    label: resolveIn(definition.label, '', ctx, where('label')),
    visible: calculateVisible(
      definition.id,
      resolveIn<boolean | undefined>(definition.visible, undefined, ctx, where('visible')),
      resolveIn<boolean | undefined>(definition.hidden, undefined, ctx, where('hidden')),
    ),
    initialValue: initialValueOf(definition, ctx),
  };
}

/**
 * One field as a form item: the form keeps its value under the field's id
 * (a parameter's, the launch's `params`; `ephemeral`, the launch's own), and
 * checks what vultus-core has no option for — a parameter's required value, a
 * text's `maxLength` — as optio-core will at the launch. A field that is not
 * visible is a hidden item: out of sight, its value still in the form.
 */
function FieldItem({ item, parameter }: { item: FormItem; parameter: TaskFactoryParameter | undefined }) {
  const { t } = useTranslation();
  const rules: FormRule[] = parameter ? [{
    validator: async (_rule, value) => {
      const problem = valueProblem(parameter, value, t);
      if (problem) throw new Error(problem);
    },
  }] : [];
  return (
    <Form.Item
      name={item.definition.id}
      label={item.label}
      initialValue={item.initialValue}
      hidden={!item.visible}
      // The mark only where a value can be missing (the other kinds always have one).
      required={parameter !== undefined && parameter.required !== false
        && (parameter.kind === 'text' || parameter.kind === 'manyOf')}
      rules={rules}
    >
      <FieldControl definition={item.definition} />
    </Form.Item>
  );
}

/** A vultus-antd Field as an antd form control: the form item gives it the value and takes its changes. */
function FieldControl({ definition, value, onChange }: {
  definition: FieldDefinition;
  value?: unknown;
  onChange?: (value: unknown) => void;
}) {
  // The form holds what the field asked for, so the value fits the definition's kind.
  return <Field<FieldDefinition> definition={definition} value={value as FieldProps['value']} onChange={onChange} />;
}

/** What the form refuses in a value before launching, or null. */
function valueProblem(parameter: TaskFactoryParameter, value: unknown, t: TFunction): string | null {
  if (isNoValue(parameter, value)) {
    return parameter.required === false ? null : t('processes.factoryRequired', { defaultValue: 'Required' });
  }
  // Characters as optio-core counts them (code points).
  if (parameter.kind === 'text' && parameter.maxLength !== undefined && [...String(value)].length > parameter.maxLength) {
    return t('processes.factoryMaxLength', { defaultValue: 'At most {{count}} characters', count: parameter.maxLength });
  }
  return null;
}
