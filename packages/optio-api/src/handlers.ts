import { ObjectId, type Db } from 'mongodb';
import { publishLaunch, publishCancel, publishDismiss, publishResync } from './publisher.js';
import type { ProcessMetadataFilter } from './types.js';
import { metadataFilterToMongo } from './metadata-filter-query.js';
import { findProcessByEitherId } from './process-id-resolver.js';
import type { OptioContext } from './context.js';
import { resolveDb } from './resolve-db.js';
import type { LaunchFailureReason as LaunchFailureReasonType } from 'optio-contracts';

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

export async function listProcesses(ctx: OptioContext, query: ListProcessesQuery) {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  const { cursor, limit, rootId, state, metadataFilter } = query;

  const filter: Record<string, unknown> = {
    ...metadataFilterToMongo(metadataFilter),
  };
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
) {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  const proc = await findProcessByEitherId(col(db, prefix), id);
  if (!proc) return null;
  return toResponse(proc);
}

async function buildTree(db: Db, prefix: string, processId: ObjectId, maxDepth?: number, currentDepth = 0): Promise<any> {
  const proc = await col(db, prefix).findOne({ _id: processId });
  if (!proc) return null;

  let children: any[] = [];
  if (maxDepth === undefined || currentDepth < maxDepth) {
    const childDocs = await col(db, prefix)
      .find({ parentId: processId })
      .sort({ order: 1 })
      .toArray();

    children = await Promise.all(
      childDocs.map((c) => buildTree(db, prefix, c._id, maxDepth, currentDepth + 1)),
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
) {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  // Resolve the entry-point doc first so we can accept either id form.
  // Internal recursion in `buildTree` walks via `parentId` ObjectId
  // references and does not need the lookup helper.
  const entry = await findProcessByEitherId(col(db, prefix), id);
  if (!entry) return null;
  return buildTree(db, prefix, entry._id as ObjectId, query.maxDepth);
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
) {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  const proc = await findProcessByEitherId(col(db, prefix), id);
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
) {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  const proc = await findProcessByEitherId(col(db, prefix), id);
  if (!proc) return null;

  const { maxDepth, cursor, limit } = query;
  const filter: Record<string, unknown> = { rootId: proc.rootId };
  if (maxDepth !== undefined) {
    filter.depth = { $lte: proc.depth + maxDepth };
  }
  const allProcs = await col(db, prefix).find(filter).toArray();

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

// --- Command handlers ---

const LAUNCHABLE_STATES = ['idle', 'done', 'failed', 'cancelled'];
const CANCELLABLE_STATES = ['running', 'scheduled'];
const END_STATES = ['done', 'failed', 'cancelled'];

export type CommandResult =
  | { status: 200; body: any }
  | { status: 404; body: { message: string } }
  | { status: 409; body: { message: string } };

export type LaunchCommandResult =
  | { status: 200; body: any }
  | { status: 404 | 409; body: { reason: LaunchFailureReasonType; message: string } };

const LAUNCH_STATUS: Record<LaunchFailureReasonType, 404 | 409> = {
  'not-found': 404,
  'not-launchable': 409,
  'no-resume-support': 409,
  'launch-blocked': 409,
};

const MESSAGES: Record<string, string> = {
  'not-found': 'Process not found',
  'not-launchable': 'Process is not in a launchable state',
  'no-resume-support': 'This task does not support resume',
  'launch-blocked': 'Launches matching this filter are currently blocked',
};

function launchFail(reason: LaunchFailureReasonType): LaunchCommandResult {
  return { status: LAUNCH_STATUS[reason], body: { reason, message: MESSAGES[reason] } };
}

export async function launchProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
  resume: boolean = false,
): Promise<LaunchCommandResult> {
  const { db, database, prefix } = resolveDb(ctx.dbOpts, query);
  const engine = ctx.engineCache.get(database, prefix);

  const proc = await findProcessByEitherId(col(db, prefix), id);
  if (!proc) return launchFail('not-found');
  if (!LAUNCHABLE_STATES.includes(proc.status.state)) return launchFail('not-launchable');
  if (resume && !proc.supportsResume) return launchFail('no-resume-support');

  const result = await engine.launch({ processId: proc.processId, resume });
  if (result.ok) return { status: 200, body: toResponse(result.process) };
  return launchFail(result.reason);
}

export async function cancelProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
): Promise<CommandResult> {
  const { db, database, prefix } = resolveDb(ctx.dbOpts, query);
  const proc = await findProcessByEitherId(col(db, prefix), id);
  if (!proc) {
    return { status: 404, body: { message: 'Process not found' } };
  }
  if (!proc.cancellable) {
    return { status: 409, body: { message: 'Process is not cancellable' } };
  }
  if (!CANCELLABLE_STATES.includes(proc.status.state)) {
    return { status: 409, body: { message: `Cannot cancel process in state: ${proc.status.state}` } };
  }
  await publishCancel(ctx.redis, database, prefix, proc.processId);
  return { status: 200, body: toResponse(proc) };
}

export async function dismissProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
): Promise<CommandResult> {
  const { db, database, prefix } = resolveDb(ctx.dbOpts, query);
  const proc = await findProcessByEitherId(col(db, prefix), id);
  if (!proc) {
    return { status: 404, body: { message: 'Process not found' } };
  }
  if (!END_STATES.includes(proc.status.state)) {
    return { status: 409, body: { message: `Cannot dismiss process in state: ${proc.status.state}` } };
  }
  await publishDismiss(ctx.redis, database, prefix, proc.processId);
  return { status: 200, body: toResponse(proc) };
}

export async function resyncProcesses(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  clean: boolean = false,
  metadataFilter?: ProcessMetadataFilter,
): Promise<{ message: string }> {
  const { database, prefix } = resolveDb(ctx.dbOpts, query);
  await publishResync(ctx.redis, database, prefix, clean, metadataFilter);
  return { message: clean ? 'Nuke and resync requested' : 'Resync requested' };
}
