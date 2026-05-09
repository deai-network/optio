import { initContract } from '@ts-rest/core';
import { z } from 'zod';
import { PaginationQuerySchema, PaginatedResponseSchema, ErrorSchema, ObjectIdSchema, ProcessIdParamSchema } from './schemas/common.js';
import { ProcessSchema, ProcessStateSchema, LogEntrySchema, ProcessMetadataFilterSchema, MetadataFilterQueryParamSchema } from './schemas/process.js';
import { LaunchFailureReason, CancelFailureReason, DismissFailureReason } from './engine-failure-reasons.js';

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
  launch: {
    method: 'POST',
    path: '/processes/:id/launch',
    pathParams: z.object({ id: ProcessIdParamSchema }),
    query: InstanceQuerySchema,
    body: z.object({ resume: z.boolean().optional() }).optional(),
    responses: {
      200: ProcessSchema,
      404: LaunchErrorBody,
      409: LaunchErrorBody,
    },
    summary: 'Launch a process (optionally in resume mode)',
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
