import { ObjectId, type Db } from 'mongodb';
import type { ProcessMetadataFilter } from './types.js';
import { metadataFilterToMongo } from './metadata-filter-query.js';
import type { OptioContext } from './context.js';
import { resolveDb, resolveOptioEngine } from './resolve.js';
import { UNRESTRICTED, type Access } from './auth.js';
import {
  andScope, findActionTarget, findReadableProcess, gateProcess, scopedResyncFilter,
} from './access-scope.js';
import { andVisibility, pruneOrphans } from './visibility.js';
import { fetchConversation, type ConversationFetchResult } from './conversation-archive.js';
import type {
  LaunchFailureReason as LaunchFailureReasonType,
  CancelFailureReason as CancelFailureReasonType,
  DismissFailureReason as DismissFailureReasonType,
  ResurrectFailureReason as ResurrectFailureReasonType,
  ConversationFailureReason as ConversationFailureReasonType,
} from 'optio-contracts';

function col(db: Db, prefix: string) {
  return db.collection(`${prefix}_processes`);
}

function stripServerSideFields<T extends Record<string, any>>(proc: T): Omit<T, 'widgetUpstream'> {
  const { widgetUpstream: _omit, ...rest } = proc;
  return rest;
}

function toResponse(proc: any) {
  const stripped = stripServerSideFields(proc);
  return {
    ...stripped,
    _id: proc._id.toString(),
    parentId: proc.parentId?.toString(),
    rootId: proc.rootId.toString(),
  };
}

// --- Query handlers ---

export interface ListQuery {
  cursor?: string;
  limit: number;
  rootId?: string;
  state?: string;
  metadataFilter?: ProcessMetadataFilter;
}

export interface ListProcessesQuery extends ListQuery {
  database?: string;
  prefix?: string;
}

export async function listProcesses(
  ctx: OptioContext,
  query: ListProcessesQuery,
  access: Access = UNRESTRICTED,
) {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  const { cursor, limit, rootId, state, metadataFilter } = query;

  const filter: Record<string, unknown> = andVisibility(
    andScope({ ...metadataFilterToMongo(metadataFilter) }, access.scope),
    access.visibility,
  );
  if (rootId) filter.rootId = new ObjectId(rootId);
  if (state) filter['status.state'] = state;
  if (cursor) filter._id = { $gt: new ObjectId(cursor) };

  const [items, totalCount] = await Promise.all([
    col(db, prefix).find(filter).sort({ _id: 1 }).limit(limit + 1).toArray(),
    col(db, prefix).countDocuments(filter),
  ]);
  const hasNext = items.length > limit;
  if (hasNext) items.pop();
  return {
    items: items.map(toResponse),
    nextCursor: hasNext ? items[items.length - 1]._id.toString() : null,
    totalCount,
  };
}

export async function getProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
  access: Access = UNRESTRICTED,
) {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  const proc = await findReadableProcess(col(db, prefix), id, access);
  if (!proc) return null;
  return toResponse(proc);
}

async function buildTree(
  db: Db,
  prefix: string,
  processId: ObjectId,
  visibility: ProcessMetadataFilter | null,
  maxDepth?: number,
  currentDepth = 0,
): Promise<any> {
  const proc = await col(db, prefix).findOne({ _id: processId });
  if (!proc) return null;

  let children: any[] = [];
  if (maxDepth === undefined || currentDepth < maxDepth) {
    const childDocs = await col(db, prefix)
      .find(andVisibility({ parentId: processId }, visibility))
      .sort({ order: 1 })
      .toArray();

    children = await Promise.all(
      childDocs.map((c) => buildTree(db, prefix, c._id, visibility, maxDepth, currentDepth + 1)),
    );
    children = children.filter(Boolean);
  }

  const stripped = stripServerSideFields(proc);
  return {
    ...stripped,
    _id: proc._id.toString(),
    parentId: proc.parentId?.toString(),
    rootId: proc.rootId.toString(),
    progress: proc.progress,
    children,
  };
}

export async function getProcessTree(
  ctx: OptioContext,
  query: { database?: string; prefix?: string; maxDepth?: number },
  id: string,
  access: Access = UNRESTRICTED,
) {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  // Resolve the entry-point doc first so we can accept either id form.
  // Internal recursion in `buildTree` walks via `parentId` ObjectId
  // references and does not need the lookup helper; the whole tree shares
  // the entry point's tenant, so the scope is checked there only. Below the
  // entry point only the visibility condition is added, never scope (see
  // AGENTS.md "Access scope and authorization").
  const entry = await findReadableProcess(col(db, prefix), id, access);
  if (!entry) return null;
  return buildTree(db, prefix, entry._id as ObjectId, access.visibility, query.maxDepth);
}

export interface PaginationQuery {
  cursor?: string;
  limit: number;
}

export interface GetProcessLogQuery extends PaginationQuery {
  database?: string;
  prefix?: string;
}

export async function getProcessLog(
  ctx: OptioContext,
  query: GetProcessLogQuery,
  id: string,
  access: Access = UNRESTRICTED,
) {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  const proc = await findReadableProcess(col(db, prefix), id, access);
  if (!proc) return null;

  const { cursor, limit } = query;
  const startIdx = cursor ? parseInt(cursor, 10) : 0;
  const logSlice = proc.log.slice(startIdx, startIdx + limit + 1);
  const hasNext = logSlice.length > limit;
  if (hasNext) logSlice.pop();

  return {
    items: logSlice,
    nextCursor: hasNext ? String(startIdx + limit) : null,
    totalCount: proc.log.length,
  };
}

export interface TreeLogQuery extends PaginationQuery {
  maxDepth?: number;
}

export interface GetProcessTreeLogQuery extends TreeLogQuery {
  database?: string;
  prefix?: string;
}

export async function getProcessTreeLog(
  ctx: OptioContext,
  query: GetProcessTreeLogQuery,
  id: string,
  access: Access = UNRESTRICTED,
) {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  const proc = await findReadableProcess(col(db, prefix), id, access);
  if (!proc) return null;

  const { maxDepth, cursor, limit } = query;
  let filter: Record<string, unknown> = { rootId: proc.rootId };
  if (maxDepth !== undefined) {
    filter.depth = { $lte: proc.depth + maxDepth };
  }
  filter = andVisibility(filter, access.visibility);
  let allProcs = await col(db, prefix).find(filter).toArray();
  if (access.visibility) {
    allProcs = pruneOrphans(allProcs);
  }

  const allLogs = allProcs.flatMap((p: any) =>
    p.log.map((entry: any) => ({
      ...entry,
      processId: p._id.toString(),
      processLabel: p.name,
    })),
  );
  allLogs.sort((a: any, b: any) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());

  const startIdx = cursor ? parseInt(cursor, 10) : 0;
  const logSlice = allLogs.slice(startIdx, startIdx + limit + 1);
  const hasNext = logSlice.length > limit;
  if (hasNext) logSlice.pop();
  return {
    items: logSlice,
    nextCursor: hasNext ? String(startIdx + limit) : null,
    totalCount: allLogs.length,
  };
}

// --- Conversation record of a suspended process (proxied from the engine) ---

/**
 * `GET /processes/:id/conversation`. `headers` must go out with the answer:
 * the record is decrypted conversation content, never to be cached.
 */
export type ConversationResult = (
  | ConversationFetchResult
  | { status: 403; body: { reason: ConversationFailureReasonType; message: string } }
) & { headers: { 'Cache-Control': 'no-store' } };

const NO_STORE = { 'Cache-Control': 'no-store' } as const;

/**
 * The saved conversation of a suspended process, read through the engine's
 * conversation archive (`conversation-archive.ts`): the engine decrypts, this
 * only relays. Gated as the 'widget' action (scope: 404, authorize: 403)
 * before anything reaches the engine. The engine is always asked by the
 * process document's `_id`, also when `id` is a `processId` string.
 */
export async function getProcessConversation(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
  access: Access = UNRESTRICTED,
): Promise<ConversationResult> {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  const processes = col(db, prefix);
  const gate = await gateProcess(processes, id, access, 'widget');
  if (!gate.ok && gate.status === 403) {
    return { status: 403, body: { reason: 'forbidden', message: 'Forbidden' }, headers: NO_STORE };
  }
  // Unrestricted access skips gateProcess's lookup; find the document here.
  const doc = gate.ok ? (gate.doc ?? (await findActionTarget(processes, id))) : null;
  if (!doc) {
    return { status: 404, body: { reason: 'not-found', message: 'No such process.' }, headers: NO_STORE };
  }
  const result = await fetchConversation(db, prefix, doc._id.toString());
  return { ...result, headers: NO_STORE };
}

// --- Command handlers ---

// 403: the host's `authorize` hook refused the action (see auth.ts).
export type ForbiddenResult = { status: 403; body: { message: string } };

export type LaunchCommandResult =
  | { status: 200; body: any }
  | { status: 404 | 409; body: { reason: LaunchFailureReasonType; message: string } }
  | ForbiddenResult;

export type CancelCommandResult =
  | { status: 200; body: any }
  | { status: 404 | 409; body: { reason: CancelFailureReasonType; message: string } }
  | ForbiddenResult;

export type DismissCommandResult =
  | { status: 200; body: any }
  | { status: 404 | 409; body: { reason: DismissFailureReasonType; message: string } }
  | ForbiddenResult;

export type ResurrectCommandResult =
  | { status: 200; body: any }
  | { status: 404 | 409; body: { reason: ResurrectFailureReasonType; message: string } }
  | ForbiddenResult;

export type ResyncCommandResult =
  | { status: 202; body: { message: string } }
  | { status: 400 | 403; body: { message: string } };

const FORBIDDEN: ForbiddenResult = { status: 403, body: { message: 'Forbidden' } };

const LAUNCH_STATUS: Record<LaunchFailureReasonType, 404 | 409> = {
  'not-found': 404,
  'not-launchable': 409,
  'no-resume-support': 409,
  'launch-blocked': 409,
};

const CANCEL_STATUS: Record<CancelFailureReasonType, 404 | 409> = {
  'not-found': 404,
  'not-cancellable': 409,
};

const DISMISS_STATUS: Record<DismissFailureReasonType, 404 | 409> = {
  'not-found': 404,
  'not-dismissable': 409,
};

const MESSAGES: Record<
  LaunchFailureReasonType | CancelFailureReasonType | DismissFailureReasonType,
  string
> = {
  'not-found': 'Process not found',
  'not-launchable': 'Process is not in a launchable state',
  'no-resume-support': 'This task does not support resume',
  'launch-blocked': 'Launches matching this filter are currently blocked',
  'not-cancellable': 'Process is not cancellable in its current state',
  'not-dismissable': 'Process is not in a dismissable state',
};

function launchFail(reason: LaunchFailureReasonType): LaunchCommandResult {
  return { status: LAUNCH_STATUS[reason], body: { reason, message: MESSAGES[reason] } };
}

function cancelFail(reason: CancelFailureReasonType): CancelCommandResult {
  return { status: CANCEL_STATUS[reason], body: { reason, message: MESSAGES[reason] } };
}

function dismissFail(reason: DismissFailureReasonType): DismissCommandResult {
  return { status: DISMISS_STATUS[reason], body: { reason, message: MESSAGES[reason] } };
}

export async function launchProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
  resume: boolean = false,
  sessionId: string | null = null,
  access: Access = UNRESTRICTED,
): Promise<LaunchCommandResult> {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  const gate = await gateProcess(col(db, prefix), id, access, 'launch');
  if (!gate.ok) return gate.status === 404 ? launchFail('not-found') : FORBIDDEN;
  const engine = resolveOptioEngine(ctx, query);
  const result = await engine.launch({ processId: id, resume, sessionId });
  if (result.ok) return { status: 200, body: toResponse(result.process) };
  return launchFail(result.reason);
}

export async function cancelProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
  access: Access = UNRESTRICTED,
): Promise<CancelCommandResult> {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  const gate = await gateProcess(col(db, prefix), id, access, 'cancel');
  if (!gate.ok) return gate.status === 404 ? cancelFail('not-found') : FORBIDDEN;
  const engine = resolveOptioEngine(ctx, query);
  const result = await engine.cancel({ processId: id });
  if (result.ok) return { status: 200, body: toResponse(result.process) };
  return cancelFail(result.reason);
}

export async function dismissProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
  access: Access = UNRESTRICTED,
): Promise<DismissCommandResult> {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  const gate = await gateProcess(col(db, prefix), id, access, 'dismiss');
  if (!gate.ok) return gate.status === 404 ? dismissFail('not-found') : FORBIDDEN;
  const engine = resolveOptioEngine(ctx, query);
  const result = await engine.dismiss({ processId: id });
  if (result.ok) return { status: 200, body: toResponse(result.process) };
  return dismissFail(result.reason);
}

const RESURRECT_STATUS: Record<ResurrectFailureReasonType, 404 | 409> = {
  'not-found': 404,
  'not-resurrectable': 409,
  'no-resurrect-support': 409,
  'resurrect-in-progress': 409,
  'launch-blocked': 409,
  'shutting-down': 409,
};

const RESURRECT_MESSAGES: Record<ResurrectFailureReasonType, string> = {
  'not-found': 'Process not found',
  'not-resurrectable': 'Process has no unsaved work to resurrect, or is still active',
  'no-resurrect-support': 'This task does not support resurrect',
  'resurrect-in-progress': 'A resurrect of this process is already running',
  'launch-blocked': 'Launches matching this filter are currently blocked',
  'shutting-down': 'The engine is shutting down',
};

function resurrectFail(reason: ResurrectFailureReasonType): ResurrectCommandResult {
  return { status: RESURRECT_STATUS[reason], body: { reason, message: RESURRECT_MESSAGES[reason] } };
}

export async function resurrectProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
  sessionId: string | null = null,
  access: Access = UNRESTRICTED,
): Promise<ResurrectCommandResult> {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  const gate = await gateProcess(col(db, prefix), id, access, 'resurrect');
  if (!gate.ok) return gate.status === 404 ? resurrectFail('not-found') : FORBIDDEN;
  const engine = resolveOptioEngine(ctx, query);
  const result = await engine.resurrect({ processId: id, sessionId });
  if (result.ok) return { status: 200, body: toResponse(result.process) };
  return resurrectFail(result.reason);
}

export async function resyncProcesses(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  clean: boolean = false,
  metadataFilter?: ProcessMetadataFilter,
  access: Access = UNRESTRICTED,
): Promise<ResyncCommandResult> {
  // Under a scope the engine regenerates (and prunes) only the scope's tasks.
  const scoped = scopedResyncFilter(metadataFilter, access.scope);
  if (!scoped.ok) return { status: scoped.status, body: { message: scoped.message } };
  if (
    access.restricted
    && !(await access.authorize({
      action: 'resync',
      clean,
      metadataFilter: scoped.filter as Record<string, unknown> | undefined,
    }))
  ) {
    return FORBIDDEN;
  }
  const engine = resolveOptioEngine(ctx, query);
  await engine.resync({ clean, metadataFilter: scoped.filter });
  return {
    status: 202,
    body: { message: clean ? 'Nuke and resync requested' : 'Resync requested' },
  };
}
