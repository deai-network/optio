// Browser-safe Zod enums for engine RPC failure reasons.
//
// This module exists so failure-reason enums can be re-exported from
// `optio-contracts` to browser bundles (optio-ui, optio-dashboard) without
// pulling in `@clamator/protocol`, which uses node:crypto. The contract source
// (engine-to-api.ts) imports from here; index.ts re-exports from here.

import { z } from 'zod';

export const LaunchFailureReason = z.enum([
  'not-found',
  'not-launchable',
  'no-resume-support',
  'launch-blocked',
]);

export const CancelFailureReason = z.enum([
  'not-found',
  'not-cancellable',
]);

export const DismissFailureReason = z.enum([
  'not-found',
  'not-dismissable',
]);

export const GroupCancelFailureReason = z.enum([
  'invalid-persist-without-block',
]);

export const BlockLaunchesFailureReason = z.enum([
  'invalid-filter',
]);

export type LaunchFailureReason = z.infer<typeof LaunchFailureReason>;
export type CancelFailureReason = z.infer<typeof CancelFailureReason>;
export type DismissFailureReason = z.infer<typeof DismissFailureReason>;
export type GroupCancelFailureReason = z.infer<typeof GroupCancelFailureReason>;
export type BlockLaunchesFailureReason = z.infer<typeof BlockLaunchesFailureReason>;
