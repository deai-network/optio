// Cross-package manifest of typed-error routes exposed by the api-to-frontend
// contract. Consumers (e.g. excavator's frontend `lint:error-coverage`) read
// this to enforce exhaustive (route, reason) coverage in their error-routing
// registries for routes the frontend reaches via optio-ui.
//
// Only api-to-frontend routes whose responses carry a typed
// `{reason, message}` body are listed; engine-internal admin RPCs
// (groupCancel, blockLaunches) are not exposed to the frontend.

import {
  LaunchFailureReason,
  CancelFailureReason,
  DismissFailureReason,
} from './engine-failure-reasons.js';

export const apiToFrontendRouteErrorReasons = {
  'processes.launch':  LaunchFailureReason.options,
  'processes.cancel':  CancelFailureReason.options,
  'processes.dismiss': DismissFailureReason.options,
} as const;

export type ApiToFrontendRouteId = keyof typeof apiToFrontendRouteErrorReasons;
