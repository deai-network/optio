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

  // Runtime
  status: ProcessStatusSchema,
  progress: ProgressSchema,
  log: z.array(LogEntrySchema),

  createdAt: DateSchema,
});

export type Process = z.infer<typeof ProcessSchema>;
export type ProcessState = z.infer<typeof ProcessStateSchema>;
export type LogEntry = z.infer<typeof LogEntrySchema>;
