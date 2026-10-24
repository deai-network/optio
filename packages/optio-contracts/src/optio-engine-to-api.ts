import { z } from 'zod';
import { defineContract, defineMethod, defineNotification } from '@clamator/protocol';
import { ProcessSchema, ProcessMetadataFilterSchema } from './schemas/process.js';
import {
  LaunchFailureReason,
  CancelFailureReason,
  DismissFailureReason,
  ResurrectFailureReason,
  GroupCancelFailureReason,
  BlockLaunchesFailureReason,
} from './engine-failure-reasons.js';

const ProcessIdParam = z.string().min(1);

const launchResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), process: ProcessSchema }),
  // `message` is optio-core's text of an `invalid-parameters` or
  // `invalid-metadata` refusal.
  z.object({ ok: z.literal(false), reason: LaunchFailureReason, message: z.string().optional() }),
]);

const cancelResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), process: ProcessSchema }),
  z.object({ ok: z.literal(false), reason: CancelFailureReason }),
]);

const dismissResult = z.discriminatedUnion('ok', [
  // `process` is absent when dismissing deleted the record (an ended factory launch).
  z.object({ ok: z.literal(true), process: ProcessSchema.optional() }),
  z.object({ ok: z.literal(false), reason: DismissFailureReason }),
]);

const resurrectResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), process: ProcessSchema }),
  z.object({ ok: z.literal(false), reason: ResurrectFailureReason }),
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

const materializeUploadResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), path: z.string() }),
  z.object({ ok: z.literal(false), reason: z.string() }),
]);

export const optioEngineContract = defineContract('optio-engine', {
  launch: defineMethod({
    params: z.object({
      processId: ProcessIdParam,
      resume: z.boolean().optional(),
      // Required (no `.optional()`) but nullable: every caller must
      // consciously supply the initiating session token, or explicit null
      // for unattended launches.
      sessionId: z.string().nullable(),
      // A task factory's parameter values (required for a factory, refused
      // for any other process).
      params: z.record(z.unknown()).optional(),
      // Overrides the factory's default retention of the launch's record:
      // true = deleted when it ends, false = kept until dismissed.
      ephemeral: z.boolean().optional(),
      // A task factory launch's stamp, from optio-api's `launchMetadata` hook
      // (server-side only: the frontend's launch body cannot carry it); merged
      // into the new launch's metadata.
      metadata: z.record(z.union([z.string(), z.number(), z.boolean(), z.null()])).optional(),
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
  resurrect: defineMethod({
    params: z.object({
      processId: ProcessIdParam,
      // Like launch: the initiating session, or explicit null.
      sessionId: z.string().nullable(),
    }),
    result: resurrectResult,
  }),
  groupCancel: defineMethod({
    params: z.object({
      metadataFilter: ProcessMetadataFilterSchema,
      blockNewLaunches: z.boolean().optional(),
      persist: z.boolean().optional(),
      reason: z.string().optional(),
      purgeRecords: z.boolean().optional(),
    }),
    result: groupCancelResult,
  }),
  groupCancelAndWait: defineMethod({
    params: z.object({
      metadataFilter: ProcessMetadataFilterSchema,
      blockNewLaunches: z.boolean().optional(),
      persist: z.boolean().optional(),
      reason: z.string().optional(),
      purgeRecords: z.boolean().optional(),
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
  materializeUpload: defineMethod({
    params: z.object({
      processId: ProcessIdParam,
      blobId: z.string(),
      filename: z.string(),
    }),
    result: materializeUploadResult,
  }),
  resync: defineNotification({
    params: z.object({
      clean: z.boolean().optional(),
      metadataFilter: ProcessMetadataFilterSchema.optional(),
    }),
  }),
});
