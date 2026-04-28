// Handlers (framework-agnostic)
export {
  listProcesses,
  getProcess,
  getProcessTree,
  getProcessLog,
  getProcessTreeLog,
  launchProcess,
  cancelProcess,
  dismissProcess,
  resyncProcesses,
  type ListQuery,
  type PaginationQuery,
  type TreeLogQuery,
  type CommandResult,
} from './handlers.js';

// Publishers (for domain code to import)
export { publishLaunch, publishResync } from './publisher.js';
export type { ProcessMetadataFilter } from './types.js';

// Stream poller (for custom adapters)
export { createListPoller, createTreePoller, type StreamPollerOptions, type TreePollerOptions, type ListPollerHandle } from './stream-poller.js';

// Multi-db discovery
export { discoverInstances } from './discovery.js';
export { resolveDb, type DbOptions, type SingleDbOptions, type MultiDbOptions } from './resolve-db.js';

// Metadata filter helpers
export {
  parseMetadataFilterQuery,
  metadataFilterToMongo,
  detectLegacyMetadataParams,
  type ParseResult,
} from './metadata-filter-query.js';
