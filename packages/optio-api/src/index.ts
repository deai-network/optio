// Handlers (framework-agnostic)
export {
  listProcesses,
  getProcess,
  getProcessTree,
  getProcessLog,
  getProcessTreeLog,
  getProcessConversation,
  getProcessResult,
  launchProcess,
  cancelProcess,
  dismissProcess,
  resurrectProcess,
  resyncProcesses,
  type ListQuery,
  type PaginationQuery,
  type TreeLogQuery,
  type LaunchCommandResult,
  type CancelCommandResult,
  type DismissCommandResult,
  type ResurrectCommandResult,
  type ResyncCommandResult,
  type ForbiddenResult,
  type ConversationResult,
  type ResultDownloadResult,
} from './handlers.js';

// A task's result file (GridFS; for custom adapters)
export {
  resultQueryOf,
  contentDisposition,
  GRIDFS_RESULT_DOWNLOAD,
  type ResultDownloadDeps,
  type ResultQueryResult,
} from './result-download.js';

// Conversation archive proxy (read a suspended process's record; for custom adapters)
export {
  resolveConversationArchive,
  forgetConversationArchive,
  fetchConversation,
  CONVERSATION_ARCHIVE_CACHE_TTL_MS,
  CONVERSATION_ARCHIVE_TIMEOUT_MS,
  CONVERSATION_ARCHIVE_MAX_BODY_BYTES,
  type ConversationArchiveEndpoint,
  type ConversationFetchResult,
} from './conversation-archive.js';

// Authentication + per-request access (scope / visibility / authorize hooks),
// and the launch stamp (launchMetadata, not an access hook)
export {
  checkAuth,
  accessFor,
  resolveAccess,
  hasAccessHooks,
  launchStamp,
  UNRESTRICTED,
  type OptioRole,
  type AuthCallback,
  type AuthResult,
  type Access,
  type AccessHooks,
  type ScopeCallback,
  type ScopeFilter,
  type VisibilityCallback,
  type AuthorizeCallback,
  type AuthorizeInput,
  type OptioAction,
  type ProcessRef,
  type LaunchMetadata,
  type LaunchMetadataCallback,
  type LaunchStamp,
} from './auth.js';
// Scope helpers (for custom adapters)
export {
  andScope,
  scopeToMongo,
  inScope,
  findScopedProcess,
  findReadableProcess,
  gateProcess,
  findActionTarget,
  gateInstances,
  scopedResyncFilter,
  toProcessRef,
  type GateResult,
  type ScopedFilterResult,
} from './access-scope.js';

export type { ProcessMetadataFilter } from './types.js';

// Visibility helpers (for custom adapters)
export {
  normalizeVisibility,
  visibilityToMongo,
  andVisibility,
  isVisible,
  pruneOrphans,
} from './visibility.js';

// The one choke point for process reads (for custom adapters; see AGENTS.md
// "One choke point")
export {
  processCollection,
  processReads,
  processReadsOf,
  type ProcessReads,
} from './process-reads.js';

// Predicate builders (for visibility hooks)
export { and, or, not, leaf, eq, ne, isIn, notIn, exists, gt, gte, lt, lte } from 'optio-contracts';

// Stream poller (for custom adapters)
export {
  createListPoller, createTreePoller, createMultiTreePoller, createSessionEventsPoller,
  type StreamPollerOptions, type TreePollerOptions, type MultiTreePollerOptions,
  type MultiTreeRoot, type SessionEventsPollerOptions, type ListPollerHandle,
} from './stream-poller.js';

// Multi-db discovery
export { discoverInstances } from './discovery.js';
export { resolveDb, resolveOptioEngine, type DbOptions, type SingleDbOptions, type MultiDbOptions } from './resolve.js';

// Metadata filter helpers
export {
  parseMetadataFilterQuery,
  metadataFilterToMongo,
  detectLegacyMetadataParams,
  formatLegacyMetadataMessage,
  type ParseResult,
} from './metadata-filter-query.js';

// Engine RPC client + transport cache.
//
// Layers (see optio-api/AGENTS.md):
//  - Layer 1: createOptioTransports / OptioTransports — caches RpcClient
//    per (database, prefix). Used by RPC-only consumers + adapter authors.
//  - Layer 2: createOptioContext / OptioContext — wraps Layer 1 + Mongo Db
//    for HTTP-handler use. Used by HTTP hosts.
//  - Generated: OptioEngineClient — proxy for the optio-engine clamator
//    contract; wrap any cached transport: `new OptioEngineClient(transports.get(...))`.
export { OptioEngineClient } from './_generated/optio-engine.js';
export { createOptioTransports, type OptioTransports } from './optio-transports.js';
export { createOptioContext, type OptioContext } from './context.js';
