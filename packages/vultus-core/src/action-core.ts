import type React from 'react';
import type { MutableRefObject } from 'react';
import { getVerdict, getReason } from './decision.js';
import { resolve } from './resolve.js';
import { routeApiError } from './routeApiError.js';
import { parseApiError } from './parseApiError.js';
import type { MessageSink } from './MessageSink.js';
import type {
  ActionErrorCtx,
  ActionOptions,
  ActionStatus,
  ConfirmationSpec,
  Decision,
  ErrorRoutesRegistry,
} from './types.js';

/**
 * Hookless core of the action framework, shared by useAction (single) and
 * useActionList (dynamic list) so the firing machinery + ActionStatus shape
 * live in exactly one place — neither entry point reimplements the other.
 */

export interface ResolvedFields {
  label: string;
  icon: React.ReactNode | undefined;
  variant: 'default' | 'primary' | 'danger';
  disabled: boolean;
  reason: string | undefined;
  invisible: boolean;
  confirmation: ConfirmationSpec | undefined;
}

/** Resolve the static (hookless) ActionStatus fields from the options. */
export function resolveActionFields(opts: ActionOptions<any, any, any>): ResolvedFields {
  const enabledD = resolve<Decision>(opts.enabled, true);
  return {
    label: resolve(opts.label, ''),
    icon: resolve<React.ReactNode | undefined>(opts.icon, undefined),
    variant: resolve<'default' | 'primary' | 'danger'>(opts.variant, 'default'),
    disabled: !getVerdict(enabledD, true),
    reason: getReason(enabledD),
    invisible: resolve(opts.invisible, false),
    confirmation: resolve<ConfirmationSpec | undefined>(opts.confirmation, undefined),
  };
}

export interface FirePromiseDeps<RouteId extends string> {
  disabled: boolean;
  reason: string | undefined;
  getPending: () => boolean;
  setPending: (value: boolean) => void;
  setErrors: (errors: string[]) => void;
  errCtxRef: MutableRefObject<ActionErrorCtx | null>;
  t: (key: string, opts?: Record<string, unknown>) => string;
  registry?: ErrorRoutesRegistry<RouteId>;
  messageSink?: MessageSink;
}

/** Build the async fire (pending guard + error routing) for one action. */
export function makeFirePromise<TArgs, TResult, RouteId extends string>(
  opts: ActionOptions<TArgs, TResult, RouteId>,
  deps: FirePromiseDeps<RouteId>,
): (args: TArgs) => Promise<void> {
  return async (args: TArgs) => {
    if (deps.disabled) {
      console.warn(`Action '${opts.id}': disabled — ${deps.reason ?? '(no reason)'}`);
      return;
    }
    if (deps.getPending()) {
      console.warn(`Action '${opts.id}': already pending — ignoring fire`);
      return;
    }
    deps.setErrors([]);
    deps.setPending(true);
    try {
      const result = await opts.fire(args);
      opts.onSuccess?.(result as TResult);
    } catch (err) {
      if (opts.errorRoute && deps.registry) {
        routeApiError(
          err,
          {
            route: opts.errorRoute,
            form: deps.errCtxRef.current?.form,
            setInlineError: deps.errCtxRef.current?.setInlineError,
            t: deps.t,
            showError: deps.messageSink,
          },
          deps.registry,
        );
      }
      const parsed = parseApiError(err);
      deps.setErrors([parsed.message ?? 'Action failed']);
    } finally {
      deps.setPending(false);
    }
  };
}

/** Assemble the public ActionStatus from resolved fields + state + fire. */
export function assembleStatus<TArgs>(
  opts: ActionOptions<TArgs, any, any>,
  fields: ResolvedFields,
  pending: boolean,
  errors: string[],
  firePromise: (args: TArgs) => Promise<void>,
): ActionStatus<TArgs> {
  const fire = (args: TArgs) => {
    void firePromise(args);
  };
  return {
    id: opts.id,
    label: fields.label,
    icon: fields.icon,
    variant: fields.variant,
    pending,
    disabled: fields.disabled,
    reason: fields.reason,
    invisible: fields.invisible,
    confirmation: fields.confirmation,
    errors,
    fire: fire as ActionStatus<TArgs>['fire'],
    firePromise: firePromise as ActionStatus<TArgs>['firePromise'],
  };
}
