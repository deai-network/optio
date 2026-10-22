import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import type { Process } from 'optio-contracts';
import { parseApiError, useAction, useActionErrorCtx, type ActionStatus, type FormLike } from 'vultus-core';
import { optioErrorRoutes, type OptioErrorRouteId } from '../error-routes.js';
import { EPHEMERAL_FIELD_ID, parametersOf, refusedParameter } from '../task-factory.js';
import { useProcessActions } from './useProcessActions.js';

/** What a task factory's launch needs of its record (a list's, a tree's, or the API's). */
export interface TaskFactoryRecord {
  _id: string;
  name: string;
  warning?: string | null;
  parameters?: unknown[] | null;
  /** How long the factory keeps an ended launch (null: until dismissed); it sets the `ephemeral` field's start. */
  ttlSeconds?: number | null;
}

/**
 * The launch of a task factory with its form's values, as a vultus action
 * (spec 2026-10-20-task-factory §3): `fire(values)` launches the factory with
 * them as `params`, and `onLaunched` gets the child it made.
 *
 * Call it inside the form's FormPanel: a refusal reaches the form from there,
 * through the error routes (optioErrorRoutes, route `processes.launch`). An
 * `invalid-parameters` one names its parameter ("<id>: <what>"): <what> shows
 * on that field, the values entered stay.
 */
export function useTaskFactoryLaunchAction(
  factory: TaskFactoryRecord,
  { onLaunched, hiddenLabels }: {
    onLaunched?: (child: Process) => void;
    /** The label of each form field that is not shown, by id: a refusal naming one shows in the form under it. */
    hiddenLabels?: Readonly<Record<string, string>>;
  } = {},
): ActionStatus<Record<string, unknown>> {
  const { t } = useTranslation();
  const { launchAsync } = useProcessActions();
  const form = useActionErrorCtx()?.form;
  const ids = useMemo(() => parametersOf(factory).map((p) => p.id), [factory]);
  return useAction<Record<string, unknown>, Process, OptioErrorRouteId>(
    {
      id: 'optio.taskFactory.launch',
      label: t('processes.factoryLaunch', { defaultValue: 'Launch' }),
      variant: 'primary',
      errorRoute: 'processes.launch',
      fire: async (values) => {
        // The form's `ephemeral` field is the launch's, the others' values its params.
        const { [EPHEMERAL_FIELD_ID]: ephemeral, ...params } = values;
        try {
          return await launchAsync(factory._id, {
            params,
            ...(typeof ephemeral === 'boolean' ? { ephemeral } : {}),
          });
        } catch (err) {
          throw placeRefusal(err, ids, form, hiddenLabels ?? {});
        }
      },
      onSuccess: (child) => onLaunched?.(child),
    },
    optioErrorRoutes,
  );
}

/**
 * Where a launch refusal shows, as the error the action fails with. An
 * `invalid-parameters` one naming a field of the form: on that field (what is
 * wrong), and its reason's text in the form. One naming none, or a field that
 * is not shown (`hiddenLabels`, then as "<label>: <what>"): its own message,
 * in the form (the reason's text would not say what is wrong), so it goes on
 * without its reason. Any other: by its reason (the error routes).
 */
function placeRefusal(
  err: unknown,
  ids: string[],
  form: FormLike | undefined,
  hiddenLabels: Readonly<Record<string, string>>,
): unknown {
  const { status, reason, message } = parseApiError(err);
  if (reason !== 'invalid-parameters') return err;
  const refused = refusedParameter(message, ids);
  if (refused && Object.hasOwn(hiddenLabels, refused.id)) {
    return { status, body: { message: `${hiddenLabels[refused.id]}: ${refused.problem}` } };
  }
  if (refused && form) {
    form.setFields([{ name: refused.id, errors: [refused.problem] }]);
    return err;
  }
  return { status, body: { message } };
}
