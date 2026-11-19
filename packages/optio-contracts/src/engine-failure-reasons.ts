// Browser-safe Zod enums for engine RPC failure reasons.
//
// This module exists so failure-reason enums can be re-exported from
// `optio-contracts` to browser bundles (optio-ui, optio-dashboard) without
// pulling in `@clamator/protocol`, which uses node:crypto. The contract source
// (optio-engine-to-api.ts) imports from here; index.ts re-exports from here.

import { z } from 'zod';

export const LaunchFailureReason = z.enum([
  'not-found',
  'not-launchable',
  'no-resume-support',
  'launch-blocked',
  'shutting-down',
  // Task factories: a launch of a factory without `params`, `params` on a
  // process that is not a factory, and values the factory refused (the
  // message names the parameter: "<id>: <what>").
  'parameters-required',
  'not-a-factory',
  'invalid-parameters',
  // A factory launch's stamp (`metadata`) with keys of the factory's own
  // metadata (with `task_for`, the instance's too) or the reserved
  // `taskFactory` (the message names them).
  'invalid-metadata',
  // `resumeFrom` (resume from an older snapshot) without `resume: true`.
  'invalid-request',
  // `resumeFrom`: the task has no hook to discard the newer snapshots.
  'no-snapshot-support',
  // `resumeFrom`: no valid snapshot of the process has that `capturedAt`.
  'snapshot-not-found',
  // `resumeFrom`: an unfinished capture is pending (Resurrect or a plain Resume settles it).
  'capture-pending',
]);

export const CancelFailureReason = z.enum([
  'not-found',
  'not-cancellable',
]);

export const DismissFailureReason = z.enum([
  'not-found',
  'not-dismissable',
]);

export const ResurrectFailureReason = z.enum([
  'not-found',
  'not-resurrectable',
  'no-resurrect-support',
  'resurrect-in-progress',
  'launch-blocked',
  'shutting-down',
  // A launch with `resumeFrom` of the process, or a run a launch scheduled, is in progress.
  'not-launchable',
]);

// Why `listSnapshots` gave no list: no such process, or its task has no
// snapshot list hook.
export const ListSnapshotsFailureReason = z.enum([
  'not-found',
  'no-snapshot-support',
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
export type ResurrectFailureReason = z.infer<typeof ResurrectFailureReason>;
export type ListSnapshotsFailureReason = z.infer<typeof ListSnapshotsFailureReason>;
export type GroupCancelFailureReason = z.infer<typeof GroupCancelFailureReason>;
export type BlockLaunchesFailureReason = z.infer<typeof BlockLaunchesFailureReason>;
