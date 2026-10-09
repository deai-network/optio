/**
 * One change stream per (database, prefix) per API process, shared by every
 * live stream on it: each change goes to the subscribers it is relevant to,
 * which re-read at most once a second. Where change streams are unavailable
 * (a standalone mongod, no permission to watch) or while one is down, the hub
 * asks every subscriber to re-read once a second instead -- the old polling.
 *
 * Spec: docs/2026-10-09-api-change-streams-design.md
 */
import type { ChangeStream, ChangeStreamDocument, Db, Document, MongoClient, Timestamp } from 'mongodb';
import { toProcessChange, type ProcessChange } from './process-change-relevance.js';

/** The server cannot provide change streams (standalone, or not allowed). */
export class ChangeStreamsUnavailable extends Error {}

/** MongoDB error codes that mean "cannot watch here": change streams need a
 *  replica set (40573); the user may not watch (13, Unauthorized). */
const UNAVAILABLE_CODES = new Set([40573, 13]);

export interface ProcessChangeSource {
  /** Start delivering changes. Rejects with ChangeStreamsUnavailable when the
   *  server cannot watch. After it resolves, `onError` is called once when
   *  the watch fails or ends. */
  open(
    onChange: (c: ProcessChange) => void,
    onError: (err: unknown) => void,
  ): Promise<{ close(): Promise<void> }>;
}

export interface HubSubscriber {
  isRelevant(c: ProcessChange): boolean;
  refresh(): void;
}

export interface ProcessChangeHubOptions {
  /** A subscriber re-reads at most once per this interval (leading edge). */
  minIntervalMs?: number;
  /** Polling mode: every subscriber re-reads at this interval. */
  pollIntervalMs?: number;
  /** After ChangeStreamsUnavailable, try the change stream again after this. */
  retryUnavailableMs?: number;
  /** Reopen backoff after any other error: from min, doubling, up to max. */
  backoffMinMs?: number;
  backoffMaxMs?: number;
}

interface Entry {
  sub: HubSubscriber;
  last: number;
  timer: ReturnType<typeof setTimeout> | null;
}

export type HubMode = 'idle' | 'opening' | 'streaming' | 'polling';

export class ProcessChangeHub {
  private readonly entries = new Map<HubSubscriber, Entry>();
  private readonly minIntervalMs: number;
  private readonly pollIntervalMs: number;
  private readonly retryUnavailableMs: number;
  private readonly backoffMinMs: number;
  private readonly backoffMaxMs: number;
  private _mode: HubMode = 'idle';
  private opening: Promise<void> | null = null;
  private handle: { close(): Promise<void> } | null = null;
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private backoffMs: number;
  /** Bumped whenever the current stream (or open attempt) is abandoned, so
   *  its late callbacks are ignored. */
  private generation = 0;

  constructor(private readonly source: ProcessChangeSource, opts: ProcessChangeHubOptions = {}) {
    this.minIntervalMs = opts.minIntervalMs ?? 1000;
    this.pollIntervalMs = opts.pollIntervalMs ?? 1000;
    this.retryUnavailableMs = opts.retryUnavailableMs ?? 300_000;
    this.backoffMinMs = opts.backoffMinMs ?? 1000;
    this.backoffMaxMs = opts.backoffMaxMs ?? 30_000;
    this.backoffMs = this.backoffMinMs;
  }

  get mode(): HubMode {
    return this._mode;
  }

  /** Number of subscribers. */
  get size(): number {
    return this.entries.size;
  }

  /** Resolves once the change stream is open or the hub is polling; the
   *  subscriber makes its initial read after that, so no change is lost. */
  async subscribe(sub: HubSubscriber): Promise<void> {
    if (!this.entries.has(sub)) this.entries.set(sub, { sub, last: -Infinity, timer: null });
    if (this._mode === 'idle') {
      this._mode = 'opening';
      this.opening = this.tryOpen(true);
    }
    if (this._mode === 'opening' && this.opening) await this.opening;
  }

  unsubscribe(sub: HubSubscriber): void {
    const entry = this.entries.get(sub);
    if (!entry) return;
    if (entry.timer) clearTimeout(entry.timer);
    this.entries.delete(sub);
    if (this.entries.size === 0) this.shutdown();
  }

  private shutdown(): void {
    this.generation++;
    this.stopPolling();
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    const handle = this.handle;
    this.handle = null;
    if (handle) void handle.close().catch(() => {});
    this.backoffMs = this.backoffMinMs;
    this.opening = null;
    this._mode = 'idle';
  }

  /** `initial`: the first attempt from idle, whose subscribers make their
   *  initial reads themselves; every later (re)open re-reads everyone once. */
  private async tryOpen(initial: boolean): Promise<void> {
    const gen = ++this.generation;
    try {
      const handle = await this.source.open(
        (c) => this.onChange(gen, c),
        (err) => this.onStreamError(gen, err),
      );
      if (gen !== this.generation) {
        void handle.close().catch(() => {});
        return;
      }
      this.handle = handle;
      this._mode = 'streaming';
      this.stopPolling();
      this.backoffMs = this.backoffMinMs;
      if (!initial) this.refreshAll();
    } catch (err) {
      if (gen !== this.generation) return;
      this.enterPolling();
      this.scheduleReopen(err instanceof ChangeStreamsUnavailable ? this.retryUnavailableMs : this.nextBackoff());
    }
  }

  private onChange(gen: number, c: ProcessChange): void {
    if (gen !== this.generation) return;
    for (const entry of this.entries.values()) {
      if (c.op === 'invalidate' || entry.sub.isRelevant(c)) this.requestRefresh(entry);
    }
  }

  private onStreamError(gen: number, _err: unknown): void {
    if (gen !== this.generation) return;
    this.generation++;
    const handle = this.handle;
    this.handle = null;
    if (handle) void handle.close().catch(() => {});
    this.enterPolling();
    this.scheduleReopen(this.nextBackoff());
  }

  private nextBackoff(): number {
    const delay = this.backoffMs;
    this.backoffMs = Math.min(this.backoffMs * 2, this.backoffMaxMs);
    return delay;
  }

  private scheduleReopen(delayMs: number): void {
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      if (this.entries.size > 0) void this.tryOpen(false);
    }, delayMs);
  }

  private enterPolling(): void {
    this._mode = 'polling';
    if (!this.pollTimer) {
      this.pollTimer = setInterval(() => this.refreshAll(), this.pollIntervalMs);
    }
  }

  private stopPolling(): void {
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.pollTimer = null;
  }

  private refreshAll(): void {
    for (const entry of this.entries.values()) this.requestRefresh(entry);
  }

  /** At most one refresh per minIntervalMs: at once after a quiet interval,
   *  else once at its end however many requests arrive meanwhile. */
  private requestRefresh(entry: Entry): void {
    if (entry.timer) return;
    const wait = entry.last + this.minIntervalMs - Date.now();
    if (wait <= 0) {
      entry.last = Date.now();
      entry.sub.refresh();
      return;
    }
    entry.timer = setTimeout(() => {
      entry.timer = null;
      if (!this.entries.has(entry.sub)) return;
      entry.last = Date.now();
      entry.sub.refresh();
    }, wait);
  }
}

function unavailable(err: unknown): boolean {
  const code = (err as { code?: unknown } | null)?.code;
  return typeof code === 'number' && UNAVAILABLE_CODES.has(code);
}

/** A ProcessChangeSource over `{prefix}_processes` of `db`. */
export function createMongoProcessChangeSource(db: Db, prefix: string): ProcessChangeSource {
  return {
    async open(onChange, onError) {
      const hello = await db.admin().command({ hello: 1 });
      if (!hello.setName) {
        throw new ChangeStreamsUnavailable('MongoDB is not a replica set; change streams are unavailable');
      }
      // From the cluster time of `hello`: nothing written after open()
      // resolves is missed.
      const startAtOperationTime = hello.operationTime as Timestamp | undefined;
      const stream: ChangeStream<Document> = db.collection(`${prefix}_processes`).watch(
        [], startAtOperationTime ? { startAtOperationTime } : {},
      );
      let closed = false;
      const deliver = (ev: ChangeStreamDocument<Document> | null) => {
        const change = ev ? toProcessChange(ev) : null;
        if (change) onChange(change);
      };
      try {
        // Creates the cursor on the server now, so a refusal shows here.
        deliver(await stream.tryNext());
      } catch (err) {
        await stream.close().catch(() => {});
        if (unavailable(err)) throw new ChangeStreamsUnavailable(String(err));
        throw err;
      }
      void (async () => {
        try {
          while (!closed) deliver(await stream.next());
        } catch (err) {
          if (!closed) onError(err);
        }
      })();
      return {
        async close() {
          closed = true;
          await stream.close();
        },
      };
    },
  };
}

const UNAVAILABLE_SOURCE: ProcessChangeSource = {
  async open() {
    throw new ChangeStreamsUnavailable('change streams switched off (OPTIO_API_CHANGE_STREAMS=off)');
  },
};

const registry = new WeakMap<MongoClient, Map<string, ProcessChangeHub>>();

/** The hub of `{prefix}_processes` in `db`, shared per client, database name
 *  and prefix (multi-database mode may create a new Db object per request).
 *  OPTIO_API_CHANGE_STREAMS=off makes new hubs poll. */
export function getProcessChangeHub(db: Db, prefix: string): ProcessChangeHub {
  let hubs = registry.get(db.client);
  if (!hubs) {
    hubs = new Map();
    registry.set(db.client, hubs);
  }
  const key = `${db.databaseName}\u0000${prefix}`;
  let hub = hubs.get(key);
  if (!hub) {
    const source = process.env.OPTIO_API_CHANGE_STREAMS === 'off'
      ? UNAVAILABLE_SOURCE
      : createMongoProcessChangeSource(db, prefix);
    hub = new ProcessChangeHub(source);
    hubs.set(key, hub);
  }
  return hub;
}
