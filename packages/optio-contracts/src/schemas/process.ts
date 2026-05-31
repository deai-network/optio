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

// Operator object that lives at each leaf.
export const FilterLeafOps = z
  .object({
    eq:     FilterScalar.optional(),
    ne:     FilterScalar.optional(),
    in:     z.array(FilterScalar).optional(),
    nin:    z.array(FilterScalar).optional(),
    exists: z.boolean().optional(),
    gt:     FilterScalar.optional(),
    gte:    FilterScalar.optional(),
    lt:     FilterScalar.optional(),
    lte:    FilterScalar.optional(),
  })
  .strict()
  .refine((o) => Object.keys(o).length > 0, 'leaf needs at least one operator');

// Recursive predicate tree. A predicate node is exactly one of:
//   { AND: [...] }   { OR: [...] }   { NOT: ... }   { "field": LeafOps, ... }
// .strict() forbids mixing combinator keys with field keys in one object.
export const ProcessMetadataPredicateSchema: z.ZodType<unknown> = z.lazy(() =>
  z.union([
    z.object({ AND: z.array(ProcessMetadataPredicateSchema).min(1) }).strict(),
    z.object({ OR:  z.array(ProcessMetadataPredicateSchema).min(1) }).strict(),
    z.object({ NOT: ProcessMetadataPredicateSchema }).strict(),
    z.record(FilterFieldPath, FilterLeafOps),
  ]),
);

// Legacy flat shape: keys are field names, values are scalars (implicit AND of equality).
export const ProcessMetadataFilterLegacySchema = z.record(FilterFieldPath, FilterScalar);

// Public union (backwards compatible). Predicate branch first so it wins on
// any input that has combinator keys or operator-object values; flat scalar
// shapes fall through to the legacy branch.
export const ProcessMetadataFilterSchema = z.union([
  ProcessMetadataPredicateSchema,
  ProcessMetadataFilterLegacySchema,
]);

export const MetadataFilterQueryParamSchema = z
  .string()
  .transform((s, ctx) => {
    try {
      return JSON.parse(s);
    } catch {
      ctx.addIssue({ code: 'custom', message: 'metadataFilter must be valid JSON' });
      return z.NEVER;
    }
  })
  .pipe(ProcessMetadataFilterSchema)
  .optional();

export type Process = z.infer<typeof ProcessSchema>;
export type ProcessState = z.infer<typeof ProcessStateSchema>;
export type LogEntry = z.infer<typeof LogEntrySchema>;
export type FilterScalar = z.infer<typeof FilterScalar>;
export type FilterLeafOps = z.infer<typeof FilterLeafOps>;
export type ProcessMetadataPredicate = z.infer<typeof ProcessMetadataPredicateSchema>;
export type ProcessMetadataFilter = z.infer<typeof ProcessMetadataFilterSchema>;
export type BrowserOpenRequest = z.infer<typeof BrowserOpenRequestSchema>;
export type SessionEvent = z.infer<typeof SessionEventSchema>;
