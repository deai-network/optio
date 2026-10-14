import { ObjectId, type Db } from 'mongodb';
import type { ProcessMetadataFilter } from 'optio-contracts';
import { metadataFilterToMongo } from './metadata-filter-query.js';
import { andScope } from './access-scope.js';
import type { ScopeFilter } from './auth.js';
import { pruneOrphans } from './visibility.js';
import { processReads } from './process-reads.js';
import {
  LIST_PROJECTION, listRelevant, treeRelevant, sessionRelevant, type ProcessChange,
} from './process-change-relevance.js';
import { getProcessChangeHub, type HubSubscriber, type ProcessChangeHub } from './process-change-hub.js';

export interface StreamPollerOptions {
  db: Db;
  prefix: string;
  sendEvent: (data: unknown) => void;
  onError: () => void;
  metadataFilter?: ProcessMetadataFilter;
  /** Access scope (auth.ts): only processes inside it are emitted. */
  scope?: ScopeFilter | null;
  /** Visibility predicate (auth.ts), resolved once at stream open like
   *  `scope`: only processes it admits are read, through `processReads`.
   *  Required (M9: forgetting it would silently stream everything) --
   *  `null`: no restriction, identical queries to today. */
  visibility: ProcessMetadataFilter | null;
  /** The change hub to follow; default: the shared hub of (db, prefix). */
  hub?: ProcessChangeHub;
}

export interface ListPollerHandle {
  start(): void;
  stop(): void;
}

/**
 * Run `read` once on start, then whenever the hub reports a change `rule`
 * finds relevant (the hub throttles to one read a second, and polls once a
 * second where change streams are unavailable). Reads never overlap: a refresh
 * during a read runs one more read after it. While a read is in flight every
 * change counts as relevant, since `rule` judges by the last read's members
 * and the read in flight may have missed the change. A failed read stops the
 * stream and calls `onError`.
 *
 * Spec: docs/2026-10-09-api-change-streams-design.md
 */
function driveByChanges(
  opts: { db: Db; prefix: string; hub?: ProcessChangeHub; onError: () => void },
  read: () => Promise<void>,
  rule: (c: ProcessChange) => boolean,
): ListPollerHandle {
  const hub = opts.hub ?? getProcessChangeHub(opts.db, opts.prefix);
  let stopped = false;
  let reading = false;
  let again = false;

  async function run(): Promise<void> {
    if (stopped) return;
    if (reading) {
      again = true;
      return;
    }
    reading = true;
    try {
      do {
        again = false;
        await read();
      } while (again && !stopped);
    } catch {
      stop();
      opts.onError();
    } finally {
      reading = false;
    }
  }

  const sub: HubSubscriber = {
    isRelevant: (c) => reading || rule(c),
    refresh: () => { void run(); },
  };

  function start() {
    stopped = false;
    hub.subscribe(sub).then(
      () => run(),
      () => {
        if (stopped) return;
        stop();
        opts.onError();
      },
    );
  }

  function stop() {
    if (stopped) return;
    stopped = true;
    hub.unsubscribe(sub);
  }

  return { start, stop };
}

export function createListPoller(opts: StreamPollerOptions): ListPollerHandle {
  const { db, prefix, sendEvent, metadataFilter, scope, visibility } = opts;
  const reads = processReads(db, prefix, visibility ?? null);
  const filter = andScope(metadataFilterToMongo(metadataFilter), scope ?? null);
  let members = new Set<string>();
  let lastSnapshot = '';

  async function read() {
    const allProcs = await reads
      .find(filter, { projection: LIST_PROJECTION })
      .sort({ depth: 1, order: 1, _id: 1 })
      .toArray();
    members = new Set(allProcs.map((p: any) => String(p._id)));
    const snapshot = JSON.stringify(
      allProcs.map((p: any) => ({
        id: p._id,
        state: p.status?.state,
        percent: p.progress?.percent,
        message: p.progress?.message,
        supportsResume: p.supportsResume ?? false,
        hasSavedState: p.hasSavedState ?? false,
        supportsResurrect: p.supportsResurrect ?? false,
        hasUnsavedWork: p.hasUnsavedWork ?? false,
        resurrecting: p.resurrecting ?? false,
        autoResumeScheduled: p.autoResumeScheduled ?? false,
        browserOpenRequests: p.browserOpenRequests ?? [],
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
          supportsResurrect: p.supportsResurrect ?? false,
          hasUnsavedWork: p.hasUnsavedWork ?? false,
          resurrecting: p.resurrecting ?? false,
          autoResumeScheduled: p.autoResumeScheduled ?? false,
          browserOpenRequests: p.browserOpenRequests ?? [],
        })),
      });
    }
  }

  return driveByChanges(opts, read, (c) => listRelevant(c, members));
}

export interface TreePollerOptions extends Omit<StreamPollerOptions, 'metadataFilter' | 'scope'> {
  rootId: string;
  baseDepth: number;
  maxDepth?: number;
}

export function createTreePoller(opts: TreePollerOptions): ListPollerHandle {
  const { db, prefix, sendEvent, rootId, baseDepth, maxDepth, visibility } = opts;
  const reads = processReads(db, prefix, visibility ?? null);
  const roots = new Set([rootId]);
  let members = new Set<string>();
  let lastSnapshot = '';
  const lastLogCounts = new Map<string, number>();
  let firstPoll = true;

  async function read() {
    const filter: Record<string, unknown> = { rootId: new ObjectId(rootId) };
    if (maxDepth !== undefined) {
      filter.depth = { $lte: baseDepth + maxDepth };
    }

    let allProcs = await reads.find(filter).sort({ depth: 1, order: 1 }).toArray();
    if (reads.visibility) {
      // No orphans below the entry point (spec "Trees"): a child whose
      // parent fell out of the visibility-filtered read is dropped too.
      allProcs = pruneOrphans(allProcs);
    }
    members = new Set(allProcs.map((p: any) => String(p._id)));
    const snapshot = JSON.stringify(
      allProcs.map((p: any) => ({
        id: p._id, status: p.status, progress: p.progress,
        widgetData: p.widgetData, uiWidget: p.uiWidget,
        supportsResume: p.supportsResume ?? false,
        hasSavedState: p.hasSavedState ?? false,
        supportsResurrect: p.supportsResurrect ?? false,
        hasUnsavedWork: p.hasUnsavedWork ?? false,
        resurrecting: p.resurrecting ?? false,
        autoResumeScheduled: p.autoResumeScheduled ?? false,
        browserOpenRequests: p.browserOpenRequests ?? [],
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
          launchMode: p.launchMode,
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
          supportsResurrect: p.supportsResurrect ?? false,
          hasUnsavedWork: p.hasUnsavedWork ?? false,
          resurrecting: p.resurrecting ?? false,
          autoResumeScheduled: p.autoResumeScheduled ?? false,
          browserOpenRequests: p.browserOpenRequests ?? [],
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
  }

  return driveByChanges(
    opts, read, (c) => treeRelevant(c, roots, members, { metadataChanges: reads.visibility !== null }),
  );
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
  /** Visibility predicate (auth.ts): only processes it admits are read,
   *  through `processReads`. Required (M9) -- `null`: no restriction. */
  visibility: ProcessMetadataFilter | null;
  /** The change hub to follow; default: the shared hub of (db, prefix). */
  hub?: ProcessChangeHub;
}

export function createMultiTreePoller(opts: MultiTreePollerOptions): ListPollerHandle {
  const { db, prefix, sendEvent, treeRoots, flatIds, maxDepth, visibility } = opts;
  const reads = processReads(db, prefix, visibility ?? null);
  const roots = new Set(treeRoots.map((r) => r.rootId.toString()));
  const flatIdStrs = new Set(flatIds.map(String));
  let members = new Set<string>();
  let lastSnapshot = '';
  const lastLogCounts = new Map<string, number>();
  let firstPoll = true;

  async function read() {
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

    let allProcs = await reads.find(filter).sort({ depth: 1, order: 1 }).toArray();
    if (reads.visibility) {
      // Flat ids are fetched standalone (no ancestors queried for them), so
      // a fetched flat row must be kept regardless of whether its parent
      // came back too; processes reached through a tree root still prune
      // against each other normally.
      const keep = new Set(
        allProcs.filter((p: any) => flatIdStrs.has(String(p._id))).map((p: any) => String(p._id)),
      );
      allProcs = pruneOrphans(allProcs, keep);
    }
    // Every requested flat id stays a member regardless of whether this
    // read returned it (no-hook behaviour, unchanged by visibility): a
    // false positive here only costs one extra re-read, while correctness
    // comes from the read itself (which applies visibility); a flat id
    // becoming visible again is already caught by the metadata-change
    // relevance fix below.
    members = new Set([...allProcs.map((p: any) => String(p._id)), ...flatIds.map(String)]);
    const snapshot = JSON.stringify(
      allProcs.map((p: any) => ({
        id: p._id, status: p.status, progress: p.progress,
        widgetData: p.widgetData, uiWidget: p.uiWidget,
        supportsResume: p.supportsResume ?? false,
        hasSavedState: p.hasSavedState ?? false,
        supportsResurrect: p.supportsResurrect ?? false,
        hasUnsavedWork: p.hasUnsavedWork ?? false,
        resurrecting: p.resurrecting ?? false,
        autoResumeScheduled: p.autoResumeScheduled ?? false,
        browserOpenRequests: p.browserOpenRequests ?? [],
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
          launchMode: p.launchMode,
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
          supportsResurrect: p.supportsResurrect ?? false,
          hasUnsavedWork: p.hasUnsavedWork ?? false,
          resurrecting: p.resurrecting ?? false,
          autoResumeScheduled: p.autoResumeScheduled ?? false,
          browserOpenRequests: p.browserOpenRequests ?? [],
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
  }

  return driveByChanges(
    opts, read, (c) => treeRelevant(c, roots, members, { metadataChanges: reads.visibility !== null }),
  );
}

export interface SessionEventsPollerOptions {
  db: Db;
  prefix: string;
  sessionId: string;
  sendEvent: (data: unknown) => void;
  onError: () => void;
  /** Access scope (auth.ts): only processes inside it are read. */
  scope?: ScopeFilter | null;
  /** Visibility predicate (auth.ts): only processes it admits are read,
   *  through `processReads`. Required (M9) -- `null`: no restriction. */
  visibility: ProcessMetadataFilter | null;
  /** The change hub to follow; default: the shared hub of (db, prefix). */
  hub?: ProcessChangeHub;
}

/**
 * Poll-backed session-events feed. Each ~1s tick reads processes whose
 * `originatingSessionId` matches `sessionId` and emits each process's NEW
 * sessionEvents (deduped by length high-water mark per process). Read-only.
 */
export function createSessionEventsPoller(opts: SessionEventsPollerOptions): ListPollerHandle {
  const { db, prefix, sessionId, sendEvent, scope, visibility } = opts;
  const reads = processReads(db, prefix, visibility ?? null);
  const filter = andScope({ originatingSessionId: sessionId }, scope ?? null);
  let members = new Set<string>();
  const lastCounts = new Map<string, number>();

  async function read() {
    const procs = await reads
      .find(filter)
      .project({ sessionEvents: 1 })
      .toArray();
    members = new Set(procs.map((p: any) => String(p._id)));
    for (const p of procs) {
      const pid = p._id.toString();
      const events = (p.sessionEvents ?? []) as any[];
      const seen = lastCounts.get(pid) ?? 0;
      if (events.length > seen) {
        sendEvent({
          type: 'session-events',
          processId: pid,
          events: events.slice(seen),
        });
        lastCounts.set(pid, events.length);
      }
    }
  }

  return driveByChanges(
    opts, read, (c) => sessionRelevant(c, sessionId, members, { metadataChanges: reads.visibility !== null }),
  );
}
