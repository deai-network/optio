/**
 * optio-ui's own error routes (vultus-core's ErrorRoutesRegistry): for each
 * route an optio-ui action reaches through its `errorRoute`, the failure
 * reasons the API answers with (optio-contracts' apiToFrontendRouteErrorReasons
 * lists them per route) and the text each shows, `t(i18nKey)` — the app's
 * translation (optio-ui ships no texts of its own for them). Three routes:
 * `processes.launch`, the launch of TaskFactoryLaunchModal and of
 * useLaunchShowingRefusals, `processes.dismiss`, the removal of an ended launch
 * (LaunchControls), and `processes.resurrect`, the resurrect of
 * useResurrectShowingRefusals.
 */
import type { DismissFailureReason, LaunchFailureReason, ResurrectFailureReason } from 'optio-contracts';
import type { ErrorRoutesRegistry, RegistryEntry } from 'vultus-core';

export type OptioErrorRouteId = 'processes.launch' | 'processes.dismiss' | 'processes.resurrect';

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
  // The server's launch stamp (optio-api's launchMetadata) collides with the factory's metadata.
  'invalid-metadata': { i18nKey: 'errors.processes.launch.invalid-metadata' },
  // A resume from an older snapshot (`resumeFrom`): sent without `resume: true`; the task has no
  // hook to discard the newer snapshots; no valid snapshot has that `capturedAt`; an unfinished
  // capture is pending (Resurrect or a plain Resume settles it).
  'invalid-request': { i18nKey: 'errors.processes.launch.invalid-request' },
  'no-snapshot-support': { i18nKey: 'errors.processes.launch.no-snapshot-support' },
  'snapshot-not-found': { i18nKey: 'errors.processes.launch.snapshot-not-found' },
  'capture-pending': { i18nKey: 'errors.processes.launch.capture-pending' },
};

// Every dismiss refusal (removing an ended launch).
const dismissReasons: Record<DismissFailureReason, RegistryEntry> = {
  'not-found': { i18nKey: 'errors.processes.dismiss.not-found' },
  'not-dismissable': { i18nKey: 'errors.processes.dismiss.not-dismissable' },
};

// Every resurrect refusal (saving a failed run's unsaved work, then resuming).
const resurrectReasons: Record<ResurrectFailureReason, RegistryEntry> = {
  'not-found': { i18nKey: 'errors.processes.resurrect.not-found' },
  'not-resurrectable': { i18nKey: 'errors.processes.resurrect.not-resurrectable' },
  'no-resurrect-support': { i18nKey: 'errors.processes.resurrect.no-resurrect-support' },
  'resurrect-in-progress': { i18nKey: 'errors.processes.resurrect.resurrect-in-progress' },
  'launch-blocked': { i18nKey: 'errors.processes.resurrect.launch-blocked' },
  'shutting-down': { i18nKey: 'errors.processes.resurrect.shutting-down' },
  // A launch with `resumeFrom` of the process, or a run a launch scheduled, is in progress.
  'not-launchable': { i18nKey: 'errors.processes.resurrect.not-launchable' },
};

export const optioErrorRoutes: ErrorRoutesRegistry<OptioErrorRouteId> = {
  'processes.launch': launchReasons,
  'processes.dismiss': dismissReasons,
  'processes.resurrect': resurrectReasons,
};
