import { useTranslation } from 'react-i18next';
import { routeApiError, useMessageSink } from 'vultus-core';
import { optioErrorRoutes, type OptioErrorRouteId } from '../error-routes.js';

/**
 * Shows a refused request outside a form, as optio-ui's actions show an error there: through
 * `optioErrorRoutes` (`route`, vultus-core's `routeApiError`) to vultus-core's MessageSink.
 * `t('errors.<route>.<reason>')`, else the error's message, else `t('common.error')`. The sink is
 * the app's: a vultus-antd `VultusProvider`'s antd message, or `OptioProvider`'s `messageSink`.
 * Internal: `useLaunchShowingRefusals` and `useResurrectShowingRefusals` use it.
 */
export function useShowRefusal(route: OptioErrorRouteId): (err: unknown) => void {
  const { t } = useTranslation();
  const showError = useMessageSink();
  return (err) => routeApiError(err, { route, t: (key) => t(key), showError }, optioErrorRoutes);
}
