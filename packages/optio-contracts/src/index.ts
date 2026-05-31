// Schemas
export { ObjectIdSchema, ProcessIdParamSchema, PaginationQuerySchema,
         PaginatedResponseSchema, ErrorSchema, DateSchema } from './schemas/common.js';
export { ProcessSchema, ProcessStateSchema, LogEntrySchema,
         ProcessMetadataFilterSchema, MetadataFilterQueryParamSchema,
         ProcessMetadataPredicateSchema, ProcessMetadataFilterLegacySchema,
         FilterScalar, FilterLeafOps, FilterFieldPath,
         BrowserOpenRequestSchema, SessionEventSchema } from './schemas/process.js';
export { SessionEventsStreamMessageSchema } from './schemas/session-events.js';

// Types
export type { Process, ProcessState, LogEntry, ProcessMetadataFilter,
              ProcessMetadataPredicate, FilterScalar, FilterLeafOps,
              BrowserOpenRequest, SessionEvent } from './schemas/process.js';
export type { SessionEventsStreamMessage } from './schemas/session-events.js';

// Filter builder helpers
export { and, or, not, eq, ne, isIn, notIn, exists, gt, gte, lt, lte } from './process-filter-helpers.js';

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
