import { useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useActionErrorCtx } from './useActionErrorCtx.js';
import { useMessageSink } from './MessageSink.js';
import { resolveActionFields, makeFirePromise, assembleStatus } from './action-core.js';
import type { ActionOptions, ActionStatus, ErrorRoutesRegistry } from './types.js';

/**
 * Build a DYNAMIC list of actions from a spec array — the list counterpart to
 * useAction. The hook count is fixed regardless of list length (one shared
 * error context, one pending/errors map, one memo), so it is usable where
 * useAction — a per-call hook — cannot be looped (rules of hooks). Pending and
 * errors are tracked per action id in the shared maps; each action gets real
 * per-item pending. The firing machinery + ActionStatus shape are the exact
 * same shared helpers useAction uses (see action-core.ts) — nothing is
 * reimplemented.
 */
export function useActionList<TArgs = void, TResult = void, RouteId extends string = string>(
  specs: ActionOptions<TArgs, TResult, RouteId>[],
  registry?: ErrorRoutesRegistry<RouteId>,
): ActionStatus<TArgs>[] {
  const errCtx = useActionErrorCtx();
  const errCtxRef = useRef(errCtx);
  errCtxRef.current = errCtx;
  const { t } = useTranslation();
  const messageSink = useMessageSink();

  const [pendingMap, setPendingMap] = useState<Record<string, boolean>>({});
  const [errorsMap, setErrorsMap] = useState<Record<string, string[]>>({});
  const pendingRef = useRef(pendingMap);
  pendingRef.current = pendingMap;

  return useMemo(
    () =>
      specs.map((opts) => {
        const fields = resolveActionFields(opts);
        const firePromise = makeFirePromise(opts, {
          disabled: fields.disabled,
          reason: fields.reason,
          getPending: () => !!pendingRef.current[opts.id],
          setPending: (value) => setPendingMap((m) => ({ ...m, [opts.id]: value })),
          setErrors: (errs) => setErrorsMap((m) => ({ ...m, [opts.id]: errs })),
          errCtxRef,
          t,
          registry,
          messageSink,
        });
        return assembleStatus(
          opts,
          fields,
          !!pendingMap[opts.id],
          errorsMap[opts.id] ?? [],
          firePromise,
        );
      }),
    [specs, pendingMap, errorsMap, registry, t, messageSink],
  );
}
