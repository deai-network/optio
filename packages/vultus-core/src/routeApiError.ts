import { parseApiError } from './parseApiError.js';
import type { ApiErrorContext, ErrorRoutesRegistry } from './types.js';

export function routeApiError<RouteId extends string>(
  err: unknown,
  ctx: ApiErrorContext<RouteId>,
  registry: ErrorRoutesRegistry<RouteId>,
): void {
  const parsed = parseApiError(err);
  const routeMap = registry[ctx.route];
  const entry = parsed.reason ? routeMap?.[parsed.reason] : undefined;
  const text = entry?.i18nKey
    ? ctx.t(entry.i18nKey)
    : (parsed.message ?? ctx.t('common.error'));

  if (entry?.field && ctx.form) {
    ctx.form.setFields([{ name: entry.field, errors: [text] }]);
    return;
  }
  if (ctx.setInlineError) {
    ctx.setInlineError(text);
    return;
  }
  (ctx.showError ?? (() => {}))(text);
}
