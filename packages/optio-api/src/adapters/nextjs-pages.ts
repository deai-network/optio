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
import { resolveDb, type DbOptions } from '../resolve-db.js';

export type OptioApiOptions = {
  redis: Redis;
  prefix?: string;
  authenticate?: AuthCallback<NextApiRequest>;
} & (
  | { db: Db; mongoClient?: never }
  | { mongoClient: MongoClient; db?: never }
);

const c = initContract();
const apiContract = c.router({ processes: processesContract }, { pathPrefix: '/api' });

export function createOptioHandler(opts: OptioApiOptions): (req: NextApiRequest, res: NextApiResponse) => Promise<void> {
  const { redis } = opts;
  const dbOpts: DbOptions = 'mongoClient' in opts && opts.mongoClient ? { mongoClient: opts.mongoClient } : { db: opts.db! };

  const tsRestHandler = createNextRouter(apiContract.processes, {
    list: async ({ query }) => {
      const { db, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.listProcesses(db, prefix, query);
      return { status: 200 as const, body: result };
    },
    get: async ({ params, query }) => {
      const { db, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.getProcess(db, prefix, params.id);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getTree: async ({ params, query }) => {
      const { db, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.getProcessTree(db, prefix, params.id, query.maxDepth);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getLog: async ({ params, query }) => {
      const { db, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.getProcessLog(db, prefix, params.id, query);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getTreeLog: async ({ params, query }) => {
      const { db, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.getProcessTreeLog(db, prefix, params.id, query);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    launch: async ({ params, query }) => {
      const { db, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.launchProcess(db, redis, prefix, params.id);
      return result as any;
    },
    cancel: async ({ params, query }) => {
      const { db, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.cancelProcess(db, redis, prefix, params.id);
      return result as any;
    },
    dismiss: async ({ params, query }) => {
      const { db, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.dismissProcess(db, redis, prefix, params.id);
      return result as any;
    },
    resync: async ({ query, body }: { query: { database?: string; prefix?: string }; body: { clean?: boolean } }) => {
      const { prefix } = resolveDb(dbOpts, query);
      const result = await handlers.resyncProcesses(redis, prefix, body.clean ?? false);
      return { status: 200 as const, body: result };
    },
  });

  return async (req: NextApiRequest, res: NextApiResponse) => {
    const url = req.url ?? '';
    const method = req.method ?? '';

    // Discovery endpoint: /api/optio/instances
    if (url === '/api/optio/instances' && method === 'GET') {
      const instances = await discoverInstances(dbOpts);
      res.status(200).json({ instances });
      return;
    }

    // Match tree stream: /api/processes/<id>/tree/stream
    const treeStreamMatch = url.match(/^\/api\/processes\/([^/]+)\/tree\/stream$/);
    if (treeStreamMatch && method === 'GET') {
      const id = treeStreamMatch[1];
      const { db, prefix } = resolveDb(dbOpts, {
        database: req.query.database as string | undefined,
        prefix: req.query.prefix as string | undefined,
      });
      const maxDepth = req.query.maxDepth !== undefined ? parseInt(req.query.maxDepth as string, 10) : undefined;

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
        maxDepth,
      });

      poller.start();
      req.on('close', () => poller.stop());
      return;
    }

    // Match list stream: /api/processes/stream (exact)
    if (url.match(/^\/api\/processes\/stream$/) && method === 'GET') {
      const { db, prefix } = resolveDb(dbOpts, {
        database: req.query.database as string | undefined,
        prefix: req.query.prefix as string | undefined,
      });

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
      });

      poller.start();
      req.on('close', () => poller.stop());
      return;
    }

    // Delegate to ts-rest handler for all other routes
    return tsRestHandler(req, res);
  };
}
