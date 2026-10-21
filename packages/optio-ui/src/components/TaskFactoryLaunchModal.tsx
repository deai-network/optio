import { useMemo, type SyntheticEvent } from 'react';
import { Alert, Form, Modal, Space } from 'antd';
import type { FormRule } from 'antd';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import type { Process } from 'optio-contracts';
import { useAction, type FieldDefinition } from 'vultus-core';
import { ActionButton, Field, FormPanel, FormSubmitButton, type FieldProps } from 'vultus-antd';
import { useTaskFactoryLaunchAction, type TaskFactoryRecord } from '../hooks/useTaskFactoryLaunch.js';
import {
  fieldDefinitionOf, initialValueOfParameter, isNoValue, labelOf, parametersOf, type TaskFactoryParameter,
} from '../task-factory.js';

export interface TaskFactoryLaunchModalProps {
  /** The factory's record: its `_id`, `name`, `warning` and `parameters`. */
  process: TaskFactoryRecord;
  open: boolean;
  /** Called to close it: Cancel, the close icon, Escape, and after a launch. */
  onClose: () => void;
  /** Called with the launched child once the launch is accepted (the modal then closes). */
  onLaunched?: (child: Process) => void;
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
export function TaskFactoryLaunchModal({ process, open, onClose, onLaunched }: TaskFactoryLaunchModalProps) {
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
          <LaunchForm process={process} onClose={onClose} onLaunched={onLaunched} />
        </FormPanel>
      </Modal>
    </span>
  );
}

// Inside the FormPanel, as its actions must be: that is where a refusal finds the form.
function LaunchForm({ process, onClose, onLaunched }: Omit<TaskFactoryLaunchModalProps, 'open'>) {
  const { t, i18n } = useTranslation();
  const launch = useTaskFactoryLaunchAction(process, {
    onLaunched: (child) => {
      onLaunched?.(child);
      onClose();
    },
  });
  const cancel = useAction({
    id: 'optio.taskFactory.cancel',
    label: t('processes.factoryCancel', { defaultValue: 'Cancel' }),
    fire: () => onClose(),
  });
  return (
    <>
      {process.warning && <Alert type="warning" showIcon title={process.warning} style={{ marginBottom: 16 }} />}
      {parametersOf(process).map((parameter) => (
        <ParameterItem key={parameter.id} parameter={parameter} language={i18n.language} />
      ))}
      <Space style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <ActionButton action={cancel} />
        <FormSubmitButton action={launch} />
      </Space>
    </>
  );
}

/**
 * One parameter as a form item: the form keeps its value under the
 * parameter's id (the launch's `params`), and checks what vultus-core has no
 * option for — a required parameter's value, a text's `maxLength` — as
 * optio-core will at the launch.
 */
function ParameterItem({ parameter, language }: { parameter: TaskFactoryParameter; language: string | undefined }) {
  const { t } = useTranslation();
  const definition = useMemo(() => fieldDefinitionOf(parameter, language), [parameter, language]);
  const rules: FormRule[] = [{
    validator: async (_rule, value) => {
      const problem = valueProblem(parameter, value, t);
      if (problem) throw new Error(problem);
    },
  }];
  return (
    <Form.Item
      name={parameter.id}
      label={labelOf(parameter, language)}
      initialValue={initialValueOfParameter(parameter)}
      // The mark only where a value can be missing (the other kinds always have one).
      required={parameter.required !== false && (parameter.kind === 'text' || parameter.kind === 'manyOf')}
      rules={rules}
    >
      <FieldControl definition={definition} />
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
