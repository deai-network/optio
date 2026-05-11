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
  type LaunchCommandResult,
  type CancelCommandResult,
  type DismissCommandResult,
} from './handlers.js';

export type { ProcessMetadataFilter } from './types.js';

// Stream poller (for custom adapters)
export { createListPoller, createTreePoller, type StreamPollerOptions, type TreePollerOptions, type ListPollerHandle } from './stream-poller.js';

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
