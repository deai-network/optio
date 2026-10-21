/**
 * optio-ui's own error routes (vultus-core's ErrorRoutesRegistry): for each
 * route an optio-ui action reaches through its `errorRoute`, the failure
 * reasons the API answers with (optio-contracts' apiToFrontendRouteErrorReasons
 * lists them per route) and the text each shows, `t(i18nKey)` — the app's
 * translation (optio-ui ships no texts of its own for them). Today one route:
 * `processes.launch`, the launch of TaskFactoryLaunchModal.
 */
import type { LaunchFailureReason } from 'optio-contracts';
import type { ErrorRoutesRegistry, RegistryEntry } from 'vultus-core';

export type OptioErrorRouteId = 'processes.launch';

// Every launch refusal: a reason optio-contracts adds fails the typecheck here until it has its text.
const launchReasons: Record<LaunchFailureReason, RegistryEntry> = {
  'not-found': { i18nKey: 'errors.processes.launch.not-found' },
  'not-launchable': { i18nKey: 'errors.processes.launch.not-launchable' },
  'no-resume-support': { i18nKey: 'errors.processes.launch.no-resume-support' },
  'launch-blocked': { i18nKey: 'errors.processes.launch.launch-blocked' },
  'shutting-down': { i18nKey: 'errors.processes.launch.shutting-down' },
  'parameters-required': { i18nKey: 'errors.processes.launch.parameters-required' },
  'not-a-factory': { i18nKey: 'errors.processes.launch.not-a-factory' },
  // Beside the field its message names (TaskFactoryLaunchModal shows the message there).
  'invalid-parameters': { i18nKey: 'errors.processes.launch.invalid-parameters' },
};

export const optioErrorRoutes: ErrorRoutesRegistry<OptioErrorRouteId> = {
  'processes.launch': launchReasons,
};
