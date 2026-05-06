import { z } from 'zod';

export const ObjectIdSchema = z.string().regex(/^[a-f\d]{24}$/i, 'Invalid ObjectId');

/**
 * Path-parameter schema for routes that look up a process by id. Accepts
 * either form the system uses to identify a process: the Mongo ObjectId hex
 * (24 hex chars) used in `_id`, or the application-level `processId` string
 * produced by `mkPid()` (typically of the form
 * `<project>__<kind>_<resource>_<uuid>`). Resolution to a single document
 * is the handler's responsibility — see `findProcessByEitherId` in optio-api.
 *
 * Why permissive: callers (e.g. excavator's recipe-debug flow) may know the
 * `processId` string before the engine has materialized the row in mongo
 * and assigned an ObjectId. Validating strictly as ObjectId here would
 * 400-reject those callers at the contract layer.
 */
export const ProcessIdParamSchema = z.string().min(1);

export const PaginationQuerySchema = z.object({
  cursor: z.string().optional(),
  limit: z.coerce.number().int().min(1).max(100).default(20),
});

export const PaginatedResponseSchema = <T extends z.ZodTypeAny>(itemSchema: T) =>
  z.object({
    items: z.array(itemSchema),
    nextCursor: z.string().nullable(),
    totalCount: z.number().int(),
  });

export const ErrorSchema = z.object({
  message: z.string(),
});

export const DateSchema = z.coerce.date();
