// @ts-nocheck — type inference for ts-rest router handlers requires the full
// monorepo type resolution. The adapter is tested via API integration tests.
import { createExpressEndpoints } from '@ts-rest/express';
import { initContract } from '@ts-rest/core';
import { processesContract } from 'optio-contracts';
import type { Express } from 'express';
import type { Db } from 'mongodb';
import type { MongoClient } from 'mongodb';
import type { Redis } from 'ioredis';
import * as handlers from '../handlers.js';
import { findProcessByEitherId } from '../process-id-resolver.js';
import { createListPoller, createTreePoller } from '../stream-poller.js';
import { detectLegacyMetadataParams, formatLegacyMetadataMessage } from '../metadata-filter-query.js';
import { parseSseOptions, checkLegacyMetadataParams, LegacyMetadataParamError } from '../sse-options.js';
import { discoverInstances } from '../discovery.js';
import { resolveDb, type DbOptions } from '../resolve-db.js';
import type { AuthCallback } from '../auth.js';
import { checkAuth } from '../auth.js';
import { isWriteMethod } from '../widget-proxy-core.js';
import type { EngineClient } from '../_generated/engine.js';
import { createOptioContext } from '../context.js';

export type OptioApiHandle =
  | { engine: EngineClient; closeAll: () => Promise<void>; getEngine?: never }
  | { getEngine: (database: string, prefix: string) => EngineClient; closeAll: () => Promise<void>; engine?: never };

export type OptioApiOptions = {
  redis: Redis;
  prefix?: string;
  authenticate: AuthCallback<import('express').Request>;
} & (
  | { db: Db; mongoClient?: never }
  | { mongoClient: MongoClient; db?: never }
);

const c = initContract();
const apiContract = c.router({ processes: processesContract }, { pathPrefix: '/api' });

export function registerOptioApi(app: Express, opts: OptioApiOptions): OptioApiHandle {
  const { redis } = opts;
  const dbOpts: DbOptions = 'mongoClient' in opts && opts.mongoClient ? { mongoClient: opts.mongoClient } : { db: opts.db! };
  const ctx = createOptioContext({ dbOpts, redis });

  // Global auth enforcement. Runs on every /api/* request before any
  // ts-rest, SSE, or discovery handler.
  app.use('/api', async (req, res, next) => {
    const authResult = await checkAuth(req, opts.authenticate, isWriteMethod(req.method));
    if (authResult) {
      res.status(authResult.status).json(authResult.body);
      return;
    }
    next();
  });

  // Reject legacy metadata.* query params with an explicit migration message.
  // Runs before ts-rest validation so users see the helpful error rather than
  // a generic schema-validation 400.
  app.get('/api/processes', (req: any, res: any, next: any) => {
    const legacyKeys = detectLegacyMetadataParams(req.query ?? {});
    if (legacyKeys.length > 0) {
      res.status(400).json({ message: formatLegacyMetadataMessage(legacyKeys) });
      return;
    }
    next();
  });

  // SSE list stream — registered before createExpressEndpoints so that the
  // static path /api/processes/stream takes precedence over the ts-rest
  // GET /api/processes/:id parameterised route.
  app.get('/api/processes/stream', async (req: any, res: any) => {
    const rawQuery = (req.query as Record<string, unknown>) ?? {};
    try {
      checkLegacyMetadataParams(rawQuery);
    } catch (e) {
      if (e instanceof LegacyMetadataParamError) {
        res.status(400).json({ message: e.message });
        return;
      }
      throw e;
    }
    let sseOpts;
    try {
      sseOpts = parseSseOptions(rawQuery);
    } catch (e) {
      res.status(400).json({ message: (e as Error).message });
      return;
    }
    const { db, prefix } = resolveDb(dbOpts, sseOpts);

    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    });

    const sendEvent = (data: unknown) => {
      res.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    const poller = createListPoller({
      db,
      prefix,
      sendEvent,
      onError: () => res.end(),
      metadataFilter: sseOpts.metadataFilter,
    });
    poller.start();
    req.on('close', () => poller.stop());
  });

  createExpressEndpoints(apiContract.processes, {
    list: async ({ query }) => {
      const result = await handlers.listProcesses(ctx, query);
      return { status: 200 as const, body: result };
    },
    get: async ({ params, query }) => {
      const result = await handlers.getProcess(ctx, query, params.id);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getTree: async ({ params, query }) => {
      const result = await handlers.getProcessTree(ctx, query, params.id);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getLog: async ({ params, query }) => {
      const result = await handlers.getProcessLog(ctx, query, params.id);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getTreeLog: async ({ params, query }) => {
      const result = await handlers.getProcessTreeLog(ctx, query, params.id);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    launch: async ({ params, query, body }) => {
      const result = await handlers.launchProcess(ctx, query, params.id, body?.resume === true);
      return result as any;
    },
    cancel: async ({ params, query }) => {
      const result = await handlers.cancelProcess(ctx, query, params.id);
      return result as any;
    },
    dismiss: async ({ params, query }) => {
      const result = await handlers.dismissProcess(ctx, query, params.id);
      return result as any;
    },
    resync: async ({ query, body }: { query: { database?: string; prefix?: string }; body: { clean?: boolean; metadataFilter?: import('../types.js').ProcessMetadataFilter } }) => {
      const result = await handlers.resyncProcesses(ctx, query, body.clean, body.metadataFilter);
      return { status: 200 as const, body: result };
    },
  }, app);

  app.get('/api/optio/instances', async (req: any, res: any) => {
    const instances = await discoverInstances(dbOpts, redis);
    res.json({ instances });
  });

  // SSE tree stream
  app.get('/api/processes/:id/tree/stream', async (req: any, res: any) => {
    const { id } = req.params as { id: string };
    let sseOpts;
    try {
      sseOpts = parseSseOptions((req.query as Record<string, unknown>) ?? {});
    } catch (e) {
      res.status(400).json({ message: (e as Error).message });
      return;
    }
    const { db, prefix } = resolveDb(dbOpts, sseOpts);

    const col = db.collection(`${prefix}_processes`);
    const proc = await findProcessByEitherId(col, id);
    if (!proc) {
      res.status(404).json({ message: 'Process not found' });
      return;
    }

    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    });

    const sendEvent = (data: unknown) => {
      res.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    const poller = createTreePoller({
      db,
      prefix,
      sendEvent,
      onError: () => res.end(),
      rootId: proc.rootId.toString(),
      baseDepth: proc.depth,
      maxDepth: sseOpts.maxDepth,
    });
    poller.start();
    req.on('close', () => poller.stop());
  });

  if ('db' in opts && opts.db) {
    const prefix = opts.prefix ?? 'optio';
    return {
      engine: ctx.engineCache.get(opts.db.databaseName, prefix) as EngineClient,
      closeAll: () => ctx.engineCache.closeAll(),
    };
  }
  return {
    getEngine: (database: string, prefix: string) => ctx.engineCache.get(database, prefix) as EngineClient,
    closeAll: () => ctx.engineCache.closeAll(),
  };
}
