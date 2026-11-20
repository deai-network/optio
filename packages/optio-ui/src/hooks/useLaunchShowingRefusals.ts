import { useTranslation } from 'react-i18next';
import { routeApiError, useMessageSink } from 'vultus-core';
import { optioErrorRoutes } from '../error-routes.js';
import { useProcessActions, type LaunchOptions } from './useProcessActions.js';

/**
 * `useProcessActions().launch`, showing a refusal: it launches with `launchAsync`, and a rejection
 * goes through `optioErrorRoutes` (route `processes.launch`) to vultus-core's MessageSink, as
 * optio-ui's actions show an error outside a form: `t('errors.processes.launch.<reason>')`, else the
 * error's message, else `t('common.error')`. The sink is the app's: a vultus-antd `VultusProvider`'s
 * antd message, or `OptioProvider`'s `messageSink`. An accepted launch shows nothing.
 *
 * Made for an `onLaunch` (LaunchControls, ProcessItem, ProcessList, ProcessTreeView). It returns
 * nothing: a confirmation that launches (a task's warning) closes at once, as with `launch`.
 */
export function useLaunchShowingRefusals(): (processId: string, opts?: LaunchOptions) => void {
  const { launchAsync } = useProcessActions();
  const { t } = useTranslation();
  const showError = useMessageSink();
  const launchShowingRefusal = async (processId: string, opts?: LaunchOptions) => {
    try {
      await launchAsync(processId, opts);
    } catch (err) {
      routeApiError(err, { route: 'processes.launch', t: (key) => t(key), showError }, optioErrorRoutes);
    }
  };
  return (processId, opts) => {
    void launchShowingRefusal(processId, opts);
  };
}
