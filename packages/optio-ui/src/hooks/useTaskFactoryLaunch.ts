import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import type { Process } from 'optio-contracts';
import { parseApiError, useAction, useActionErrorCtx, type ActionStatus, type FormLike } from 'vultus-core';
import { optioErrorRoutes, type OptioErrorRouteId } from '../error-routes.js';
import { parametersOf, refusedParameter } from '../task-factory.js';
import { useProcessActions } from './useProcessActions.js';

/** What a task factory's launch needs of its record (a list's, a tree's, or the API's). */
export interface TaskFactoryRecord {
  _id: string;
  name: string;
  warning?: string | null;
  parameters?: unknown[] | null;
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
  { onLaunched }: { onLaunched?: (child: Process) => void } = {},
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
        try {
          return await launchAsync(factory._id, { params: values });
        } catch (err) {
          throw placeRefusal(err, ids, form);
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
 * wrong), and its reason's text in the form. One naming none: its own message,
 * in the form (the reason's text would not say what is wrong), so it goes on
 * without its reason. Any other: by its reason (the error routes).
 */
function placeRefusal(err: unknown, ids: string[], form: FormLike | undefined): unknown {
  const { status, reason, message } = parseApiError(err);
  if (reason !== 'invalid-parameters') return err;
  const refused = refusedParameter(message, ids);
  if (refused && form) {
    form.setFields([{ name: refused.id, errors: [refused.problem] }]);
    return err;
  }
  return { status, body: { message } };
}
