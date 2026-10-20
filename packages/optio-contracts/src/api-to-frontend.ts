import { initContract } from '@ts-rest/core';
import { z } from 'zod';
import { PaginationQuerySchema, PaginatedResponseSchema, ErrorSchema, ObjectIdSchema, ProcessIdParamSchema } from './schemas/common.js';
import { ProcessSchema, ProcessStateSchema, LogEntrySchema, ProcessMetadataFilterSchema, MetadataFilterQueryParamSchema } from './schemas/process.js';
import { LaunchFailureReason, CancelFailureReason, DismissFailureReason, ResurrectFailureReason } from './engine-failure-reasons.js';

const c = initContract();

const LaunchErrorBody = z.object({
  reason: LaunchFailureReason,
  message: z.string(),
});

const CancelErrorBody = z.object({
  reason: CancelFailureReason,
  message: z.string(),
});

const DismissErrorBody = z.object({
  reason: DismissFailureReason,
  message: z.string(),
});

const ResurrectErrorBody = z.object({ reason: ResurrectFailureReason, message: z.string() });

/**
 * Why `getConversation` gave no record. The engine's conversation archive
 * answers not-found / no-conversation-support / no-conversation (404),
 * not-suspended (409) and read-failed (500); optio-api adds forbidden (403,
 * the host's `authorize` refused the 'widget' action) and engine-unavailable
 * (503: no published archive, the engine unreachable or too slow, or an answer
 * that was not the archive's).
 */
export const ConversationFailureReason = z.enum([
  'forbidden',
  'not-found',
  'no-conversation-support',
  'no-conversation',
  'not-suspended',
  'read-failed',
  'engine-unavailable',
]);
export type ConversationFailureReason = z.infer<typeof ConversationFailureReason>;

const ConversationErrorBody = z.object({ reason: ConversationFailureReason, message: z.string() });

/**
 * A suspended process's saved conversation record: `events` are `[seq, event]`
 * pairs (the recent part a resume replays); `capturedAt` is the ISO time of the
 * snapshot it was read from, or null.
 */
export const ConversationRecordSchema = z.object({
  events: z.array(z.unknown()),
  capturedAt: z.string().nullable(),
});
export type ConversationRecord = z.infer<typeof ConversationRecordSchema>;

const ProcessTreeNodeSchema = ProcessSchema.extend({
  children: z.array(z.lazy(() => ProcessSchema.extend({ children: z.array(z.any()) }))),
});

const InstanceQuerySchema = z.object({
  database: z.string().optional(),
  prefix: z.string().optional(),
});

export const processesContract = c.router({
  list: {
    method: 'GET',
    path: '/processes',
    query: PaginationQuerySchema.extend({
      rootId: ObjectIdSchema.optional(),
      state: ProcessStateSchema.optional(),
      database: z.string().optional(),
      prefix: z.string().optional(),
      metadataFilter: MetadataFilterQueryParamSchema,
    }),
    responses: {
      200: PaginatedResponseSchema(ProcessSchema),
    },
    summary: 'List and filter processes',
  },
  get: {
    method: 'GET',
    path: '/processes/:id',
    pathParams: z.object({ id: ProcessIdParamSchema }),
    query: InstanceQuerySchema,
    responses: {
      200: ProcessSchema,
      404: ErrorSchema,
    },
    summary: 'Get single process',
  },
  getTree: {
    method: 'GET',
    path: '/processes/:id/tree',
    pathParams: z.object({ id: ProcessIdParamSchema }),
    query: z.object({
      maxDepth: z.coerce.number().int().min(0).optional(),
      database: z.string().optional(),
      prefix: z.string().optional(),
    }),
    responses: {
      200: ProcessTreeNodeSchema,
      404: ErrorSchema,
    },
    summary: 'Get full process subtree',
  },
  getLog: {
    method: 'GET',
    path: '/processes/:id/log',
    pathParams: z.object({ id: ProcessIdParamSchema }),
    query: PaginationQuerySchema.extend({
      database: z.string().optional(),
      prefix: z.string().optional(),
    }),
    responses: {
      200: PaginatedResponseSchema(LogEntrySchema),
      404: ErrorSchema,
    },
    summary: 'Get log entries for a single process',
  },
  getTreeLog: {
    method: 'GET',
    path: '/processes/:id/tree/log',
    pathParams: z.object({ id: ProcessIdParamSchema }),
    query: PaginationQuerySchema.extend({
      maxDepth: z.coerce.number().int().min(0).optional(),
      database: z.string().optional(),
      prefix: z.string().optional(),
    }),
    responses: {
      200: PaginatedResponseSchema(LogEntrySchema.extend({
        processId: ObjectIdSchema,
        processLabel: z.string(),
      })),
      404: ErrorSchema,
    },
    summary: 'Get merged log entries across subtree',
  },
  getConversation: {
    method: 'GET',
    path: '/processes/:id/conversation',
    pathParams: z.object({ id: ProcessIdParamSchema }),
    query: InstanceQuerySchema,
    responses: {
      200: ConversationRecordSchema,
      401: ErrorSchema,
      403: ConversationErrorBody,
      404: ConversationErrorBody,
      409: ConversationErrorBody,
      500: ConversationErrorBody,
      503: ConversationErrorBody,
    },
    summary: 'Read a suspended process\'s saved conversation record (read-only; proxied from the engine)',
  },
  launch: {
    method: 'POST',
    path: '/processes/:id/launch',
    pathParams: z.object({ id: ProcessIdParamSchema }),
    query: InstanceQuerySchema,
    body: z.object({
      resume: z.boolean().optional(),
      sessionId: z.string().nullable().optional(),
      // A task factory's parameter values; the answer is the child's record.
      params: z.record(z.unknown()).optional(),
    }).optional(),
    responses: {
      200: ProcessSchema,
      400: LaunchErrorBody,
      404: LaunchErrorBody,
      409: LaunchErrorBody,
    },
    summary: 'Launch a process (optionally in resume mode; a task factory with parameter values)',
  },
  cancel: {
    method: 'POST',
    path: '/processes/:id/cancel',
    pathParams: z.object({ id: ProcessIdParamSchema }),
    query: InstanceQuerySchema,
    body: c.noBody(),
    responses: {
      200: ProcessSchema,
      404: CancelErrorBody,
      409: CancelErrorBody,
    },
    summary: 'Request process cancellation',
  },
  dismiss: {
    method: 'POST',
    path: '/processes/:id/dismiss',
    pathParams: z.object({ id: ProcessIdParamSchema }),
    query: InstanceQuerySchema,
    body: c.noBody(),
    responses: {
      200: ProcessSchema,
      404: DismissErrorBody,
      409: DismissErrorBody,
    },
    summary: 'Dismiss process (reset to idle)',
  },
  resurrect: {
    method: 'POST',
    path: '/processes/:id/resurrect',
    pathParams: z.object({ id: ProcessIdParamSchema }),
    query: InstanceQuerySchema,
    body: z.object({
      sessionId: z.string().nullable().optional(),
    }).optional(),
    responses: {
      200: ProcessSchema,
      404: ResurrectErrorBody,
      409: ResurrectErrorBody,
    },
    summary: 'Save the work a failed run left on its host, then resume',
  },
  resync: {
    method: 'POST',
    path: '/processes/resync',
    query: InstanceQuerySchema,
    body: z.object({ clean: z.boolean().optional(), metadataFilter: ProcessMetadataFilterSchema.optional() }),
    responses: {
      202: z.object({ message: z.string() }),
    },
    summary: 'Re-sync process definitions',
  },
});

const InstanceSchema = z.object({
  database: z.string(),
  prefix: z.string(),
  live: z.boolean(),
});

export const discoveryContract = c.router({
  instances: {
    method: 'GET',
    path: '/optio/instances',
    responses: {
      200: z.object({ instances: z.array(InstanceSchema) }),
    },
    summary: 'Discover active optio instances across databases',
  },
});
