import { initContract } from '@ts-rest/core';
import { z } from 'zod';
import { PaginationQuerySchema, PaginatedResponseSchema, ErrorSchema, ObjectIdSchema } from './schemas/common.js';
import { ProcessSchema, ProcessStateSchema, LogEntrySchema } from './schemas/process.js';

const c = initContract();

const ProcessTreeNodeSchema = ProcessSchema.extend({
  children: z.array(z.lazy(() => ProcessSchema.extend({ children: z.array(z.any()) }))),
});

export const processesContract = c.router({
  list: {
    method: 'GET',
    path: '/processes/:prefix',
    pathParams: z.object({ prefix: z.string() }),
    query: PaginationQuerySchema.extend({
      rootId: ObjectIdSchema.optional(),
      type: z.string().optional(),
      state: ProcessStateSchema.optional(),
      targetId: z.string().optional(),
    }),
    responses: {
      200: PaginatedResponseSchema(ProcessSchema),
    },
    summary: 'List and filter processes',
  },
  get: {
    method: 'GET',
    path: '/processes/:prefix/:id',
    pathParams: z.object({ prefix: z.string(), id: ObjectIdSchema }),
    responses: {
      200: ProcessSchema,
      404: ErrorSchema,
    },
    summary: 'Get single process',
  },
  getTree: {
    method: 'GET',
    path: '/processes/:prefix/:id/tree',
    pathParams: z.object({ prefix: z.string(), id: ObjectIdSchema }),
    query: z.object({
      maxDepth: z.coerce.number().int().min(0).optional(),
    }),
    responses: {
      200: ProcessTreeNodeSchema,
      404: ErrorSchema,
    },
    summary: 'Get full process subtree',
  },
  getLog: {
    method: 'GET',
    path: '/processes/:prefix/:id/log',
    pathParams: z.object({ prefix: z.string(), id: ObjectIdSchema }),
    query: PaginationQuerySchema,
    responses: {
      200: PaginatedResponseSchema(LogEntrySchema),
      404: ErrorSchema,
    },
    summary: 'Get log entries for a single process',
  },
  getTreeLog: {
    method: 'GET',
    path: '/processes/:prefix/:id/tree/log',
    pathParams: z.object({ prefix: z.string(), id: ObjectIdSchema }),
    query: PaginationQuerySchema.extend({
      maxDepth: z.coerce.number().int().min(0).optional(),
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
    path: '/processes/:prefix/:id/launch',
    pathParams: z.object({ prefix: z.string(), id: ObjectIdSchema }),
    body: c.noBody(),
    responses: {
      200: ProcessSchema,
      404: ErrorSchema,
      409: ErrorSchema,
    },
    summary: 'Launch a process',
  },
  cancel: {
    method: 'POST',
    path: '/processes/:prefix/:id/cancel',
    pathParams: z.object({ prefix: z.string(), id: ObjectIdSchema }),
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
    path: '/processes/:prefix/:id/dismiss',
    pathParams: z.object({ prefix: z.string(), id: ObjectIdSchema }),
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
    path: '/processes/:prefix/resync',
    pathParams: z.object({ prefix: z.string() }),
    body: z.object({ clean: z.boolean().optional() }),
    responses: {
      200: z.object({ message: z.string() }),
    },
    summary: 'Re-sync process definitions',
  },
});
