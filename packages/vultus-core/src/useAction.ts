import { useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useActionErrorCtx } from './useActionErrorCtx.js';
import { useMessageSink } from './MessageSink.js';
import { resolveActionFields, makeFirePromise, assembleStatus } from './action-core.js';
import type { ActionOptions, ActionStatus, ErrorRoutesRegistry } from './types.js';

export function useAction<TArgs = void, TResult = void, RouteId extends string = string>(
  opts: ActionOptions<TArgs, TResult, RouteId>,
  registry?: ErrorRoutesRegistry<RouteId>,
): ActionStatus<TArgs> {
  const errCtx = useActionErrorCtx();
  const errCtxRef = useRef(errCtx);
  errCtxRef.current = errCtx;
  const { t } = useTranslation();
  const messageSink = useMessageSink();

  const [pending, setPending] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const pendingRef = useRef(pending);
  pendingRef.current = pending;

  const fields = resolveActionFields(opts);

  const firePromise = useMemo(
    () =>
      makeFirePromise(opts, {
        disabled: fields.disabled,
        reason: fields.reason,
        getPending: () => pendingRef.current,
        setPending,
        setErrors,
        errCtxRef,
        t,
        registry,
        messageSink,
      }),
    [opts, fields.disabled, fields.reason, registry, t, messageSink],
  );

  // Granular deps by design: memo invalidates on the specific opts/fields
  // primitives below, not on the whole `opts`/`fields` objects (which are
  // fresh each render and would defeat the memo).
  return useMemo<ActionStatus<TArgs>>(
    () => assembleStatus(opts, fields, pending, errors, firePromise),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      opts.id,
      fields.label,
      fields.icon,
      fields.variant,
      pending,
      fields.disabled,
      fields.reason,
      fields.invisible,
      fields.confirmation,
      errors,
      firePromise,
    ],
  );
}
