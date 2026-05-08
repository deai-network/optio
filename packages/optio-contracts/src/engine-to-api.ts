import { z } from 'zod';
import { defineContract, defineMethod, defineNotification } from '@clamator/protocol';
import { ProcessSchema, ProcessMetadataFilterSchema } from './schemas/process.js';
import {
  LaunchFailureReason,
  CancelFailureReason,
  DismissFailureReason,
  GroupCancelFailureReason,
  BlockLaunchesFailureReason,
} from './engine-failure-reasons.js';

const ProcessIdParam = z.string().min(1);

const launchResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), process: ProcessSchema }),
  z.object({ ok: z.literal(false), reason: LaunchFailureReason }),
]);

const cancelResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), process: ProcessSchema }),
  z.object({ ok: z.literal(false), reason: CancelFailureReason }),
]);

const dismissResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), process: ProcessSchema }),
  z.object({ ok: z.literal(false), reason: DismissFailureReason }),
]);

const groupCancelResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), cancelledCount: z.number().int().nonnegative() }),
  z.object({ ok: z.literal(false), reason: GroupCancelFailureReason }),
]);

const groupCancelAndWaitResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), cancelledCount: z.number().int().nonnegative() }),
  z.object({ ok: z.literal(false), reason: GroupCancelFailureReason }),
]);

const blockLaunchesResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true) }),
  z.object({ ok: z.literal(false), reason: BlockLaunchesFailureReason }),
]);

const unblockLaunchesResult = z.object({
  removed: z.number().int().nonnegative(),
});

export const engineContract = defineContract('engine', {
  launch: defineMethod({
    params: z.object({
      processId: ProcessIdParam,
      resume: z.boolean().optional(),
    }),
    result: launchResult,
  }),
  cancel: defineMethod({
    params: z.object({ processId: ProcessIdParam }),
    result: cancelResult,
  }),
  dismiss: defineMethod({
    params: z.object({ processId: ProcessIdParam }),
    result: dismissResult,
  }),
  groupCancel: defineMethod({
    params: z.object({
      metadataFilter: ProcessMetadataFilterSchema,
      blockNewLaunches: z.boolean().optional(),
      persist: z.boolean().optional(),
      reason: z.string().optional(),
    }),
    result: groupCancelResult,
  }),
  groupCancelAndWait: defineMethod({
    params: z.object({
      metadataFilter: ProcessMetadataFilterSchema,
      blockNewLaunches: z.boolean().optional(),
      persist: z.boolean().optional(),
      reason: z.string().optional(),
    }),
    result: groupCancelAndWaitResult,
  }),
  blockLaunches: defineMethod({
    params: z.object({
      launchFilter: ProcessMetadataFilterSchema,
      reason: z.string().optional(),
    }),
    result: blockLaunchesResult,
  }),
  unblockLaunches: defineMethod({
    params: z.object({ launchFilter: ProcessMetadataFilterSchema }),
    result: unblockLaunchesResult,
  }),
  resync: defineNotification({
    params: z.object({
      clean: z.boolean().optional(),
      metadataFilter: ProcessMetadataFilterSchema.optional(),
    }),
  }),
});
