import { ObjectId } from 'mongodb';
import { publishLaunch, publishCancel, publishDismiss, publishResync } from './publisher.js';
function col(db, prefix) {
    return db.collection(`${prefix}_processes`);
}
function toResponse(proc) {
    return {
        ...proc,
        _id: proc._id.toString(),
        parentId: proc.parentId?.toString(),
        rootId: proc.rootId.toString(),
    };
}
export async function listProcesses(db, prefix, query) {
    const { cursor, limit, rootId, type, state, targetId } = query;
    const filter = {};
    if (rootId)
        filter.rootId = new ObjectId(rootId);
    if (type)
        filter.type = type;
    if (state)
        filter['status.state'] = state;
    if (targetId)
        filter['metadata.targetId'] = targetId;
    if (cursor)
        filter._id = { $gt: new ObjectId(cursor) };
    const [items, totalCount] = await Promise.all([
        col(db, prefix).find(filter).sort({ _id: 1 }).limit(limit + 1).toArray(),
        col(db, prefix).countDocuments(filter),
    ]);
    const hasNext = items.length > limit;
    if (hasNext)
        items.pop();
    return {
        items: items.map(toResponse),
        nextCursor: hasNext ? items[items.length - 1]._id.toString() : null,
        totalCount,
    };
}
export async function getProcess(db, prefix, id) {
    const proc = await col(db, prefix).findOne({ _id: new ObjectId(id) });
    if (!proc)
        return null;
    return toResponse(proc);
}
async function buildTree(db, prefix, processId, maxDepth, currentDepth = 0) {
    const proc = await col(db, prefix).findOne({ _id: processId });
    if (!proc)
        return null;
    let children = [];
    if (maxDepth === undefined || currentDepth < maxDepth) {
        const childDocs = await col(db, prefix)
            .find({ parentId: processId })
            .sort({ order: 1 })
            .toArray();
        children = await Promise.all(childDocs.map((c) => buildTree(db, prefix, c._id, maxDepth, currentDepth + 1)));
        children = children.filter(Boolean);
    }
    return {
        ...proc,
        _id: proc._id.toString(),
        parentId: proc.parentId?.toString(),
        rootId: proc.rootId.toString(),
        progress: proc.progress,
        children,
    };
}
export async function getProcessTree(db, prefix, id, maxDepth) {
    return buildTree(db, prefix, new ObjectId(id), maxDepth);
}
export async function getProcessLog(db, prefix, id, query) {
    const proc = await col(db, prefix).findOne({ _id: new ObjectId(id) });
    if (!proc)
        return null;
    const { cursor, limit } = query;
    const startIdx = cursor ? parseInt(cursor, 10) : 0;
    const logSlice = proc.log.slice(startIdx, startIdx + limit + 1);
    const hasNext = logSlice.length > limit;
    if (hasNext)
        logSlice.pop();
    return {
        items: logSlice,
        nextCursor: hasNext ? String(startIdx + limit) : null,
        totalCount: proc.log.length,
    };
}
export async function getProcessTreeLog(db, prefix, id, query) {
    const proc = await col(db, prefix).findOne({ _id: new ObjectId(id) });
    if (!proc)
        return null;
    const { maxDepth, cursor, limit } = query;
    const filter = { rootId: proc.rootId };
    if (maxDepth !== undefined) {
        filter.depth = { $lte: proc.depth + maxDepth };
    }
    const allProcs = await col(db, prefix).find(filter).toArray();
    const allLogs = allProcs.flatMap((p) => p.log.map((entry) => ({
        ...entry,
        processId: p._id.toString(),
        processLabel: p.name,
    })));
    allLogs.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
    const startIdx = cursor ? parseInt(cursor, 10) : 0;
    const logSlice = allLogs.slice(startIdx, startIdx + limit + 1);
    const hasNext = logSlice.length > limit;
    if (hasNext)
        logSlice.pop();
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
export async function launchProcess(db, redis, prefix, id) {
    const proc = await col(db, prefix).findOne({ _id: new ObjectId(id) });
    if (!proc) {
        return { status: 404, body: { message: 'Process not found' } };
    }
    if (!LAUNCHABLE_STATES.includes(proc.status.state)) {
        return { status: 409, body: { message: `Cannot launch process in state: ${proc.status.state}` } };
    }
    await publishLaunch(redis, prefix, proc.processId);
    return { status: 200, body: toResponse(proc) };
}
export async function cancelProcess(db, redis, prefix, id) {
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
    await publishCancel(redis, prefix, proc.processId);
    return { status: 200, body: toResponse(proc) };
}
export async function dismissProcess(db, redis, prefix, id) {
    const proc = await col(db, prefix).findOne({ _id: new ObjectId(id) });
    if (!proc) {
        return { status: 404, body: { message: 'Process not found' } };
    }
    if (!END_STATES.includes(proc.status.state)) {
        return { status: 409, body: { message: `Cannot dismiss process in state: ${proc.status.state}` } };
    }
    await publishDismiss(redis, prefix, proc.processId);
    return { status: 200, body: toResponse(proc) };
}
export async function resyncProcesses(redis, prefix, clean = false) {
    await publishResync(redis, prefix, clean);
    return { message: clean ? 'Nuke and resync requested' : 'Resync requested' };
}
//# sourceMappingURL=handlers.js.map