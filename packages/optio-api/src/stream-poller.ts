import { ObjectId, type Db } from 'mongodb';
import type { ProcessMetadataFilter } from 'optio-contracts';
import { metadataFilterToMongo } from './metadata-filter-query.js';

export interface StreamPollerOptions {
  db: Db;
  prefix: string;
  sendEvent: (data: unknown) => void;
  onError: () => void;
  metadataFilter?: ProcessMetadataFilter;
}

export interface ListPollerHandle {
  start(): void;
  stop(): void;
}

export function createListPoller(opts: StreamPollerOptions): ListPollerHandle {
  const { db, prefix, sendEvent, onError, metadataFilter } = opts;
  const col = db.collection(`${prefix}_processes`);
  const filter = metadataFilterToMongo(metadataFilter);
  let interval: ReturnType<typeof setInterval> | null = null;
  let lastSnapshot = '';

  async function poll() {
    try {
      const allProcs = await col.find(filter).sort({ depth: 1, order: 1, _id: 1 }).toArray();
      const snapshot = JSON.stringify(
        allProcs.map((p: any) => ({
          id: p._id,
          state: p.status?.state,
          percent: p.progress?.percent,
          message: p.progress?.message,
          supportsResume: p.supportsResume ?? false,
          hasSavedState: p.hasSavedState ?? false,
        })),
      );

      if (snapshot !== lastSnapshot) {
        lastSnapshot = snapshot;
        sendEvent({
          type: 'update',
          processes: allProcs.map((p: any) => ({
            _id: p._id.toString(),
            processId: p.processId,
            name: p.name,
            status: p.status,
            progress: p.progress,
            cancellable: p.cancellable,
            special: p.special,
            warning: p.warning,
            metadata: p.metadata,
            depth: p.depth ?? 0,
            supportsResume: p.supportsResume ?? false,
            hasSavedState: p.hasSavedState ?? false,
          })),
        });
      }
    } catch {
      stop();
      onError();
    }
  }

  function start() {
    interval = setInterval(poll, 1000);
  }

  function stop() {
    if (interval) {
      clearInterval(interval);
      interval = null;
    }
  }

  return { start, stop };
}

export interface TreePollerOptions extends Omit<StreamPollerOptions, 'metadataFilter'> {
  rootId: string;
  baseDepth: number;
  maxDepth?: number;
}

export function createTreePoller(opts: TreePollerOptions): ListPollerHandle {
  const { db, prefix, sendEvent, onError, rootId, baseDepth, maxDepth } = opts;
  const col = db.collection(`${prefix}_processes`);
  let interval: ReturnType<typeof setInterval> | null = null;
  let lastSnapshot = '';
  const lastLogCounts = new Map<string, number>();
  let firstPoll = true;

  async function poll() {
    try {
      const filter: Record<string, unknown> = { rootId: new ObjectId(rootId) };
      if (maxDepth !== undefined) {
        filter.depth = { $lte: baseDepth + maxDepth };
      }

      const allProcs = await col.find(filter).sort({ depth: 1, order: 1 }).toArray();
      const snapshot = JSON.stringify(
        allProcs.map((p: any) => ({
          id: p._id, status: p.status, progress: p.progress,
          widgetData: p.widgetData, uiWidget: p.uiWidget,
          supportsResume: p.supportsResume ?? false,
          hasSavedState: p.hasSavedState ?? false,
          metadata: p.metadata,
        })),
      );

      if (snapshot !== lastSnapshot) {
        lastSnapshot = snapshot;
        sendEvent({
          type: 'update',
          processes: allProcs.map((p: any) => ({
            _id: p._id.toString(),
            parentId: p.parentId?.toString() ?? null,
            rootId: p.rootId?.toString() ?? null,
            name: p.name,
            status: p.status,
            progress: p.progress,
            cancellable: p.cancellable ?? false,
            depth: p.depth,
            order: p.order,
            widgetData: p.widgetData,
            uiWidget: p.uiWidget,
            supportsResume: p.supportsResume ?? false,
            hasSavedState: p.hasSavedState ?? false,
            metadata: p.metadata,
          })),
        });
      }

      // Detect log changes
      let logCleared = false;
      const newLogEntries: any[] = [];
      for (const p of allProcs) {
        const pid = p._id.toString();
        const logLen = (p.log ?? []).length;
        const lastLen = lastLogCounts.get(pid) ?? 0;

        if (logLen < lastLen) {
          logCleared = true;
          lastLogCounts.set(pid, 0);
        }

        const effectiveLastLen = lastLogCounts.get(pid) ?? 0;
        if (logLen > effectiveLastLen) {
          const entries = (p.log ?? []).slice(firstPoll ? 0 : effectiveLastLen);
          for (const entry of entries) {
            newLogEntries.push({
              ...entry,
              processId: pid,
              processLabel: p.name,
            });
          }
          lastLogCounts.set(pid, logLen);
        }
      }
      firstPoll = false;

      if (logCleared) {
        sendEvent({ type: 'log-clear' });
      }
      if (newLogEntries.length > 0) {
        newLogEntries.sort((a: any, b: any) =>
          new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
        );
        sendEvent({ type: 'log', entries: newLogEntries });
      }
    } catch {
      stop();
      onError();
    }
  }

  function start() {
    interval = setInterval(poll, 1000);
  }

  function stop() {
    if (interval) {
      clearInterval(interval);
      interval = null;
    }
  }

  return { start, stop };
}

export interface MultiTreeRoot {
  rootId: ObjectId;
  baseDepth: number;
}

export interface MultiTreePollerOptions {
  db: Db;
  prefix: string;
  sendEvent: (data: unknown) => void;
  onError: () => void;
  treeRoots: MultiTreeRoot[];
  flatIds: ObjectId[];
  maxDepth?: number;
}

export function createMultiTreePoller(opts: MultiTreePollerOptions): ListPollerHandle {
  const { db, prefix, sendEvent, onError, treeRoots, flatIds, maxDepth } = opts;
  const col = db.collection(`${prefix}_processes`);
  let interval: ReturnType<typeof setInterval> | null = null;
  let lastSnapshot = '';
  const lastLogCounts = new Map<string, number>();
  let firstPoll = true;

  async function poll() {
    try {
      const branches: Record<string, unknown>[] = [];
      if (treeRoots.length > 0) {
        branches.push({
          $or: treeRoots.map((r) => {
            const f: Record<string, unknown> = { rootId: r.rootId };
            if (maxDepth !== undefined) {
              f.depth = { $lte: r.baseDepth + maxDepth };
            }
            return f;
          }),
        });
      }
      if (flatIds.length > 0) {
        branches.push({ _id: { $in: flatIds } });
      }
      if (branches.length === 0) return;
      const filter = branches.length === 1 ? branches[0] : { $or: branches };

      const allProcs = await col.find(filter).sort({ depth: 1, order: 1 }).toArray();
      const snapshot = JSON.stringify(
        allProcs.map((p: any) => ({
          id: p._id, status: p.status, progress: p.progress,
          widgetData: p.widgetData, uiWidget: p.uiWidget,
          supportsResume: p.supportsResume ?? false,
          hasSavedState: p.hasSavedState ?? false,
          metadata: p.metadata,
        })),
      );

      if (snapshot !== lastSnapshot) {
        lastSnapshot = snapshot;
        sendEvent({
          type: 'update',
          processes: allProcs.map((p: any) => ({
            _id: p._id.toString(),
            parentId: p.parentId?.toString() ?? null,
            rootId: p.rootId?.toString() ?? null,
            processId: p.processId,
            name: p.name,
            status: p.status,
            progress: p.progress,
            cancellable: p.cancellable ?? false,
            depth: p.depth,
            order: p.order,
            widgetData: p.widgetData,
            uiWidget: p.uiWidget,
            supportsResume: p.supportsResume ?? false,
            hasSavedState: p.hasSavedState ?? false,
            metadata: p.metadata,
          })),
        });
      }

      const logClearedRoots = new Set<string>();
      const newLogEntries: any[] = [];
      for (const p of allProcs) {
        const pid = p._id.toString();
        const logLen = (p.log ?? []).length;
        const lastLen = lastLogCounts.get(pid) ?? 0;

        if (logLen < lastLen) {
          logClearedRoots.add(p.rootId?.toString() ?? '');
          lastLogCounts.set(pid, 0);
        }

        const effectiveLastLen = lastLogCounts.get(pid) ?? 0;
        if (logLen > effectiveLastLen) {
          const entries = (p.log ?? []).slice(firstPoll ? 0 : effectiveLastLen);
          for (const entry of entries) {
            newLogEntries.push({
              ...entry,
              processId: pid,
              processLabel: p.name,
              rootId: p.rootId?.toString() ?? null,
            });
          }
          lastLogCounts.set(pid, logLen);
        }
      }
      firstPoll = false;

      for (const rid of logClearedRoots) {
        sendEvent({ type: 'log-clear', rootId: rid });
      }
      if (newLogEntries.length > 0) {
        newLogEntries.sort(
          (a: any, b: any) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
        );
        sendEvent({ type: 'log', entries: newLogEntries });
      }
    } catch {
      stop();
      onError();
    }
  }

  function start() {
    interval = setInterval(poll, 1000);
  }

  function stop() {
    if (interval) {
      clearInterval(interval);
      interval = null;
    }
  }

  return { start, stop };
}
