// Schemas
export { ObjectIdSchema, ProcessIdParamSchema, PaginationQuerySchema,
         PaginatedResponseSchema, ErrorSchema, DateSchema } from './schemas/common.js';
export { ProcessSchema, ProcessStateSchema, LogEntrySchema,
         ProcessMetadataFilterSchema, MetadataFilterQueryParamSchema } from './schemas/process.js';

// Types
export type { Process, ProcessState, LogEntry, ProcessMetadataFilter } from './schemas/process.js';

// Contract
export { processesContract, discoveryContract } from './api-to-frontend.js';

// Engine contract failure-reason enums (Zod schemas + types) — browser-safe re-exports
export {
  LaunchFailureReason,
  CancelFailureReason,
  DismissFailureReason,
  GroupCancelFailureReason,
  BlockLaunchesFailureReason,
} from './engine-failure-reasons.js';

// Cross-package error-route manifest (consumed by host-app lints)
export {
  apiToFrontendRouteErrorReasons,
  type ApiToFrontendRouteId,
} from './route-error-reasons.js';
