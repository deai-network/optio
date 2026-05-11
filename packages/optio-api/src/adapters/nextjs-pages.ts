// @ts-nocheck — type inference for ts-rest router handlers requires the full
// monorepo type resolution. The adapter is tested via API integration tests.
import { createNextRouter } from '@ts-rest/next';
import { initContract } from '@ts-rest/core';
import { processesContract } from 'optio-contracts';
import type { NextApiRequest, NextApiResponse } from 'next';
import type { Db } from 'mongodb';
import type { MongoClient } from 'mongodb';
import type { Redis } from 'ioredis';
import { ObjectId } from 'mongodb';
import * as handlers from '../handlers.js';
import { createListPoller, createTreePoller } from '../stream-poller.js';
import { discoverInstances } from '../discovery.js';
import { resolveDb, type DbOptions } from '../resolve.js';
import type { AuthCallback } from '../auth.js';
import { checkAuth } from '../auth.js';
import { isWriteMethod } from '../widget-proxy-core.js';
import {
  detectLegacyMetadataParams,
  formatLegacyMetadataMessage,
} from '../metadata-filter-query.js';
import {
  parseSseOptions,
  checkLegacyMetadataParams,
  LegacyMetadataParamError,
} from '../sse-options.js';
import { createOptioContext, type OptioContext } from '../context.js';

export interface OptioPagesHandler {
  handler: (req: NextApiRequest, res: NextApiResponse) => Promise<void>;
  /** Present only when called with sugar-form options (caller did not supply ctx). */
  ctx?: OptioContext;
}

export type OptioApiOptions =
  | (BaseOptioApiOptions & { ctx: OptioContext })
  | (BaseOptioApiOptions & { redis: Redis } & (
      | { db: Db; mongoClient?: never }
      | { mongoClient: MongoClient; db?: never }
    ));

interface BaseOptioApiOptions {
  prefix?: string;
  authenticate: AuthCallback<NextApiRequest>;
}

const c = initContract();
const apiContract = c.router({ processes: processesContract }, { pathPrefix: '/api' });

export function createOptioHandler(opts: OptioApiOptions): OptioPagesHandler {
  const explicit = 'ctx' in opts;
  let ctx: OptioContext;
  let redis: Redis;
  let dbOpts: DbOptions;
  if (explicit) {
    ctx = opts.ctx;
    dbOpts = ctx.dbOpts;
    redis = ctx.redis;
  } else {
    redis = opts.redis;
    dbOpts = 'mongoClient' in opts && opts.mongoClient ? { mongoClient: opts.mongoClient } : { db: opts.db! };
    ctx = createOptioContext({ dbOpts, redis });
  }

  const tsRestHandler = createNextRouter(apiContract.processes, {
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
      return { status: 202 as const, body: result };
    },
  });

  const handler = async (req: NextApiRequest, res: NextApiResponse) => {
    const authResult = await checkAuth(req, opts.authenticate, isWriteMethod(req.method ?? 'GET'));
    if (authResult) {
      res.status(authResult.status).json(authResult.body);
      return;
    }

    const url = req.url ?? '';
    const method = req.method ?? '';
    // Strip query string for path comparisons — req.url includes it on Pages Router.
    const path = url.split('?')[0];

    // Reject legacy metadata.* query params on REST list with explicit migration message.
    if (path === '/api/processes' && method === 'GET') {
      const legacyKeys = detectLegacyMetadataParams(req.query as Record<string, unknown>);
      if (legacyKeys.length > 0) {
        res.status(400).json({ message: formatLegacyMetadataMessage(legacyKeys) });
        return;
      }
    }

    // Discovery endpoint: /api/optio/instances
    if (url === '/api/optio/instances' && method === 'GET') {
      const instances = await discoverInstances(dbOpts, redis);
      res.status(200).json({ instances });
      return;
    }

    // Match tree stream: /api/processes/<id>/tree/stream
    const treeStreamMatch = path.match(/^\/api\/processes\/([^/]+)\/tree\/stream$/);
    if (treeStreamMatch && method === 'GET') {
      const id = treeStreamMatch[1];
      let sseOpts;
      try {
        sseOpts = parseSseOptions(req.query as Record<string, unknown>);
      } catch (e) {
        res.status(400).json({ message: (e as Error).message });
        return;
      }
      const { db, prefix } = resolveDb(dbOpts, sseOpts);

      const col = db.collection(`${prefix}_processes`);
      const proc = await col.findOne({ _id: new ObjectId(id) });
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
      return;
    }

    // Match list stream: /api/processes/stream (path only; req.url includes query string)
    if (path === '/api/processes/stream' && method === 'GET') {
      const rawQuery = req.query as Record<string, unknown>;
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
      return;
    }

    // Delegate to ts-rest handler for all other routes
    return tsRestHandler(req, res);
  };

  return explicit ? { handler } : { handler, ctx };
}
