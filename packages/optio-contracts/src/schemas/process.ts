import { z } from 'zod';
import { ObjectIdSchema, DateSchema } from './common.js';

export const ProcessStateSchema = z.enum([
  'idle', 'scheduled', 'running', 'done', 'failed',
  'cancel_requested', 'cancelling', 'cancelled',
]);

const ProcessStatusSchema = z.object({
  state: ProcessStateSchema,
  error: z.string().optional(),
  runningSince: DateSchema.optional(),
  doneAt: DateSchema.optional(),
  duration: z.number().optional(),
  failedAt: DateSchema.optional(),
  stoppedAt: DateSchema.optional(),
});

const ProgressSchema = z.object({
  percent: z.number().min(0).max(100).nullable(),
  message: z.string().optional(),
});

export const LogEntrySchema = z.object({
  timestamp: DateSchema,
  level: z.enum(['event', 'info', 'debug', 'warning', 'error']),
  message: z.string(),
  data: z.record(z.unknown()).optional(),
});

export const BrowserOpenRequestSchema = z.object({
  requestId: z.string(),
  url: z.string(),
});

export const SessionEventSchema = z.discriminatedUnion('type', [
  z.object({ requestId: z.string(), type: z.literal('attention'), reason: z.string() }),
  z.object({ requestId: z.string(), type: z.literal('domain'), keyword: z.string(), data: z.unknown() }),
]);

export const ProcessSchema = z.object({
  _id: ObjectIdSchema,
  processId: z.string(),
  name: z.string(),
  params: z.record(z.unknown()).optional(),
  metadata: z.record(z.unknown()).optional(),

  // Tree structure
  parentId: ObjectIdSchema.optional(),
  rootId: ObjectIdSchema,
  depth: z.number().int().min(0),
  order: z.number().int().min(0),

  // Definition metadata
  cancellable: z.boolean(),
  special: z.boolean().optional(),
  warning: z.string().optional(),
  description: z.string().nullable().optional(),

  // Runtime
  status: ProcessStatusSchema,
  progress: ProgressSchema,
  log: z.array(LogEntrySchema),

  // Widget extensions (widgetUpstream is server-side only and must never appear here)
  uiWidget: z.string().nullable().optional(),
  widgetData: z.unknown().optional(),

  // Resume feature — default false when absent in stored doc (UI treats
  // missing fields as false defensively)
  supportsResume: z.boolean().optional(),
  hasSavedState: z.boolean().optional(),

  // Auto-resume on restart: set on a cancelled, state-saved top-level process
  // whose task opted into auto_resume; the engine re-launches it (resume=true)
  // after a post-boot delay. Cleared on resume / manual launch / failed.
  autoResumeScheduled: z.boolean().optional(),

  // Client-directed events (phase 2). Append-only; never GC'd.
  browserOpenRequests: z.array(BrowserOpenRequestSchema).optional(),
  sessionEvents: z.array(SessionEventSchema).optional(),
  originatingSessionId: z.string().nullable().optional(),

  createdAt: DateSchema,
});

// Allowed leaf scalar value types in the filter.
export const FilterScalar = z.union([z.string(), z.number(), z.boolean(), z.null()]);

// Field-path validator: dotted segments under `metadata.*`, each non-empty,
// no `$` anywhere (defense against Mongo operator injection through paths).
export const FilterFieldPath = z
  .string()
  .regex(/^[^.$]+(\.[^.$]+)*$/, 'invalid field path');

// Filter language is provided by filtrum-core (extracted, backend-agnostic).
// FilterScalar + FilterFieldPath above remain the optio public contract and are
// fed to filtrum. metadata.* prefixing + Mongo translation live in optio-api.
import { makeFilterSchema } from 'filtrum-core';

const _filter = makeFilterSchema({
  allowLegacyFlat: true,
  fieldPath: FilterFieldPath,
  jsonErrorMessage: 'metadataFilter must be valid JSON',
});

export const FilterLeafOps = _filter.LeafOps;
export const ProcessMetadataPredicateSchema = _filter.PredicateSchema;
export const ProcessMetadataFilterLegacySchema = _filter.LegacySchema;
export const ProcessMetadataFilterSchema = _filter.FilterSchema;
export const MetadataFilterQueryParamSchema = _filter.QueryParamSchema;

export type FilterLeafOps = z.infer<typeof FilterLeafOps>;
export type ProcessMetadataPredicate = z.infer<typeof ProcessMetadataPredicateSchema>;
export type ProcessMetadataFilter = z.infer<typeof ProcessMetadataFilterSchema>;

export type Process = z.infer<typeof ProcessSchema>;
export type ProcessState = z.infer<typeof ProcessStateSchema>;
export type LogEntry = z.infer<typeof LogEntrySchema>;
export type FilterScalar = z.infer<typeof FilterScalar>;
export type BrowserOpenRequest = z.infer<typeof BrowserOpenRequestSchema>;
export type SessionEvent = z.infer<typeof SessionEventSchema>;
