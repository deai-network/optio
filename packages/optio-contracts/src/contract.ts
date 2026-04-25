import { initContract } from '@ts-rest/core';
import { z } from 'zod';
import { PaginationQuerySchema, PaginatedResponseSchema, ErrorSchema, ObjectIdSchema } from './schemas/common.js';
import { ProcessSchema, ProcessStateSchema, LogEntrySchema } from './schemas/process.js';

const c = initContract();

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
    }).passthrough(),
    responses: {
      200: PaginatedResponseSchema(ProcessSchema),
    },
    summary: 'List and filter processes',
  },
  get: {
    method: 'GET',
    path: '/processes/:id',
    pathParams: z.object({ id: ObjectIdSchema }),
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
    pathParams: z.object({ id: ObjectIdSchema }),
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
    pathParams: z.object({ id: ObjectIdSchema }),
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
    pathParams: z.object({ id: ObjectIdSchema }),
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
    pathParams: z.object({ id: ObjectIdSchema }),
    query: InstanceQuerySchema,
    body: z.object({ resume: z.boolean().optional() }).optional(),
    responses: {
      200: ProcessSchema,
      404: ErrorSchema,
      409: ErrorSchema,
    },
    summary: 'Launch a process (optionally in resume mode)',
  },
  cancel: {
    method: 'POST',
    path: '/processes/:id/cancel',
    pathParams: z.object({ id: ObjectIdSchema }),
    query: InstanceQuerySchema,
    body: c.noBody(),
    responses: {
      200: ProcessSchema,
      404: ErrorSchema,
      409: ErrorSchema,
    },
    summary: 'Request process cancellation',
  },
  dismiss: {
    method: 'POST',
    path: '/processes/:id/dismiss',
    pathParams: z.object({ id: ObjectIdSchema }),
    query: InstanceQuerySchema,
    body: c.noBody(),
    responses: {
      200: ProcessSchema,
      404: ErrorSchema,
      409: ErrorSchema,
    },
    summary: 'Dismiss process (reset to idle)',
  },
  resync: {
    method: 'POST',
    path: '/processes/resync',
    query: InstanceQuerySchema,
    body: z.object({ clean: z.boolean().optional() }),
    responses: {
      200: z.object({ message: z.string() }),
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
