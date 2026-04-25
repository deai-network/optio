import { ObjectId, type Db } from 'mongodb';
import type { Redis } from 'ioredis';
import { publishLaunch, publishCancel, publishDismiss, publishResync } from './publisher.js';

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
  [key: string]: unknown;
}

export async function listProcesses(db: Db, prefix: string, query: ListQuery) {
  const { cursor, limit, rootId, state, ...rest } = query;
  const filter: Record<string, unknown> = {};

  if (rootId) filter.rootId = new ObjectId(rootId);
  if (state) filter['status.state'] = state;
  if (cursor) filter._id = { $gt: new ObjectId(cursor) };

  // Extract metadata.* query params
  for (const [key, value] of Object.entries(rest)) {
    if (key.startsWith('metadata.') && typeof value === 'string') {
      filter[key] = value;
    }
  }

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

export async function getProcess(db: Db, prefix: string, id: string) {
  const proc = await col(db, prefix).findOne({ _id: new ObjectId(id) });
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

export async function getProcessTree(db: Db, prefix: string, id: string, maxDepth?: number) {
  return buildTree(db, prefix, new ObjectId(id), maxDepth);
}

export interface PaginationQuery {
  cursor?: string;
  limit: number;
}

export async function getProcessLog(db: Db, prefix: string, id: string, query: PaginationQuery) {
  const proc = await col(db, prefix).findOne({ _id: new ObjectId(id) });
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

export async function getProcessTreeLog(db: Db, prefix: string, id: string, query: TreeLogQuery) {
  const proc = await col(db, prefix).findOne({ _id: new ObjectId(id) });
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

export async function launchProcess(db: Db, redis: Redis, database: string, prefix: string, id: string, resume: boolean = false): Promise<CommandResult> {
  const proc = await col(db, prefix).findOne({ _id: new ObjectId(id) });
  if (!proc) {
    return { status: 404, body: { message: 'Process not found' } };
  }
  if (!LAUNCHABLE_STATES.includes(proc.status.state)) {
    return { status: 409, body: { message: `Cannot launch process in state: ${proc.status.state}` } };
  }
  if (resume && !proc.supportsResume) {
    return { status: 409, body: { message: 'This task does not support resume' } };
  }
  await publishLaunch(redis, database, prefix, proc.processId, resume);
  return { status: 200, body: toResponse(proc) };
}

export async function cancelProcess(db: Db, redis: Redis, database: string, prefix: string, id: string): Promise<CommandResult> {
  const proc = await col(db, prefix).findOne({ _id: new ObjectId(id) });
  if (!proc) {
    return { status: 404, body: { message: 'Process not found' } };
  }
  if (!proc.cancellable) {
    return { status: 409, body: { message: 'Process is not cancellable' } };
  }
  if (!CANCELLABLE_STATES.includes(proc.status.state)) {
    return { status: 409, body: { message: `Cannot cancel process in state: ${proc.status.state}` } };
  }
  await publishCancel(redis, database, prefix, proc.processId);
  return { status: 200, body: toResponse(proc) };
}

export async function dismissProcess(db: Db, redis: Redis, database: string, prefix: string, id: string): Promise<CommandResult> {
  const proc = await col(db, prefix).findOne({ _id: new ObjectId(id) });
  if (!proc) {
    return { status: 404, body: { message: 'Process not found' } };
  }
  if (!END_STATES.includes(proc.status.state)) {
    return { status: 409, body: { message: `Cannot dismiss process in state: ${proc.status.state}` } };
  }
  await publishDismiss(redis, database, prefix, proc.processId);
  return { status: 200, body: toResponse(proc) };
}

export async function resyncProcesses(redis: Redis, database: string, prefix: string, clean: boolean = false): Promise<{ message: string }> {
  await publishResync(redis, database, prefix, clean);
  return { message: clean ? 'Nuke and resync requested' : 'Resync requested' };
}
