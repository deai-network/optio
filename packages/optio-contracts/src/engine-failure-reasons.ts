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
