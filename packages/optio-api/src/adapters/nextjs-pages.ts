// @ts-nocheck — type inference for ts-rest router handlers requires the full
// monorepo type resolution. The adapter is tested via API integration tests.
import { createNextRouter } from '@ts-rest/next';
import { initContract } from '@ts-rest/core';
import { processesContract } from 'optio-contracts';
import type { NextApiRequest, NextApiResponse } from 'next';
import type { Db } from 'mongodb';
import type { Redis } from 'ioredis';
import { ObjectId } from 'mongodb';
import * as handlers from '../handlers.js';
import { createListPoller, createTreePoller } from '../stream-poller.js';
import { discoverPrefixes } from '../discovery.js';
import { checkAuth, type AuthCallback } from '../auth.js';

export interface OptioApiOptions {
  db: Db;
  redis: Redis;
  prefix?: string;
  authenticate: AuthCallback<NextApiRequest>;
}

const c = initContract();
const apiContract = c.router({ processes: processesContract }, { pathPrefix: '/api' });

export function createOptioHandler(opts: OptioApiOptions): (req: NextApiRequest, res: NextApiResponse) => Promise<void> {
  const { db, redis, authenticate } = opts;

  if (!authenticate) throw new Error('authenticate option is required');

  const tsRestHandler = createNextRouter(apiContract.processes, {
    list: async ({ params, query }) => {
      const result = await handlers.listProcesses(db, params.prefix, query);
      return { status: 200 as const, body: result };
    },
    get: async ({ params }) => {
      const result = await handlers.getProcess(db, params.prefix, params.id);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getTree: async ({ params, query }) => {
      const result = await handlers.getProcessTree(db, params.prefix, params.id, query.maxDepth);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getLog: async ({ params, query }) => {
      const result = await handlers.getProcessLog(db, params.prefix, params.id, query);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getTreeLog: async ({ params, query }) => {
      const result = await handlers.getProcessTreeLog(db, params.prefix, params.id, query);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    launch: async ({ params }) => {
      const result = await handlers.launchProcess(db, redis, params.prefix, params.id);
      return result as any;
    },
    cancel: async ({ params }) => {
      const result = await handlers.cancelProcess(db, redis, params.prefix, params.id);
      return result as any;
    },
    dismiss: async ({ params }) => {
      const result = await handlers.dismissProcess(db, redis, params.prefix, params.id);
      return result as any;
    },
    resync: async ({ params, body }: { params: { prefix: string }; body: { clean?: boolean } }) => {
      const result = await handlers.resyncProcesses(redis, params.prefix, body.clean ?? false);
      return { status: 200 as const, body: result };
    },
  });

  return async (req: NextApiRequest, res: NextApiResponse) => {
    const isWrite = req.method === 'POST';
    const authError = await checkAuth(req, authenticate, isWrite);
    if (authError) {
      res.status(authError.status).json(authError.body);
      return;
    }

    const url = req.url ?? '';
    const method = req.method ?? '';

    if (req.url?.endsWith('/api/optio/prefixes') && req.method === 'GET') {
      const prefixes = await discoverPrefixes(db);
      res.status(200).json({ prefixes });
      return;
    }

    // Match tree stream: /api/processes/<prefix>/<id>/tree/stream
    const treeStreamMatch = url.match(/^\/api\/processes\/([^/]+)\/([^/]+)\/tree\/stream$/);
    if (treeStreamMatch && method === 'GET') {
      const urlPrefix = treeStreamMatch[1];
      const id = treeStreamMatch[2];
      const maxDepth = req.query.maxDepth !== undefined ? parseInt(req.query.maxDepth as string, 10) : undefined;

      const col = db.collection(`${urlPrefix}_processes`);
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
        prefix: urlPrefix,
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

    // Match list stream: /api/processes/<prefix>/stream
    const listStreamMatch = url.match(/^\/api\/processes\/([^/]+)\/stream$/);
    if (listStreamMatch && method === 'GET') {
      const urlPrefix = listStreamMatch[1];

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
        prefix: urlPrefix,
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
