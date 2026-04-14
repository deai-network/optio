// @ts-nocheck — type inference for ts-rest router handlers requires the full
// monorepo type resolution. The adapter is tested via API integration tests.
import { initServer } from '@ts-rest/fastify';
import { initContract } from '@ts-rest/core';
import { processesContract } from 'optio-contracts';
import type { FastifyInstance } from 'fastify';
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
  authenticate: AuthCallback<import('fastify').FastifyRequest>;
} & (
  | { db: Db; mongoClient?: never }
  | { mongoClient: MongoClient; db?: never }
);

const c = initContract();
const apiContract = c.router({ processes: processesContract }, { pathPrefix: '/api' });

export function registerOptioApi(app: FastifyInstance, opts: OptioApiOptions) {
  const { redis } = opts;
  const dbOpts: DbOptions = 'mongoClient' in opts && opts.mongoClient ? { mongoClient: opts.mongoClient } : { db: opts.db! };
  const s = initServer();

  const routes = s.router(apiContract.processes, {
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
      const { db, database, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.launchProcess(db, redis, database, prefix, params.id);
      return result as any;
    },
    cancel: async ({ params, query }) => {
      const { db, database, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.cancelProcess(db, redis, database, prefix, params.id);
      return result as any;
    },
    dismiss: async ({ params, query }) => {
      const { db, database, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.dismissProcess(db, redis, database, prefix, params.id);
      return result as any;
    },
    resync: async ({ query, body }: { query: { database?: string; prefix?: string }; body: { clean?: boolean } }) => {
      const { database, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.resyncProcesses(redis, database, prefix, body.clean ?? false);
      return { status: 200 as const, body: result };
    },
  });

  app.register(s.plugin(routes));

  app.get('/api/optio/instances', async (_request: any, reply: any) => {
    const instances = await discoverInstances(dbOpts, redis);
    reply.send({ instances });
  });

  app.get('/api/processes/:id/tree/stream', async (request: any, reply: any) => {
    const { id } = request.params as { id: string };
    const query = request.query as { database?: string; prefix?: string; maxDepth?: string };
    const { db, prefix } = resolveDb(dbOpts, query);
    const maxDepthNum = query.maxDepth !== undefined ? parseInt(query.maxDepth, 10) : undefined;

    const col = db.collection(`${prefix}_processes`);
    const proc = await col.findOne({ _id: new ObjectId(id) });
    if (!proc) {
      reply.code(404).send({ message: 'Process not found' });
      return;
    }

    reply.raw.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    });

    const sendEvent = (data: unknown) => {
      reply.raw.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    const poller = createTreePoller({
      db,
      prefix,
      sendEvent,
      onError: () => reply.raw.end(),
      rootId: proc.rootId.toString(),
      baseDepth: proc.depth,
      maxDepth: maxDepthNum,
    });

    poller.start();
    request.raw.on('close', () => poller.stop());
  });

  app.get('/api/processes/stream', async (request: any, reply: any) => {
    const query = request.query as { database?: string; prefix?: string };
    const { db, prefix } = resolveDb(dbOpts, query);

    reply.raw.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    });

    const sendEvent = (data: unknown) => {
      reply.raw.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    const poller = createListPoller({
      db,
      prefix,
      sendEvent,
      onError: () => reply.raw.end(),
    });

    poller.start();
    request.raw.on('close', () => poller.stop());
  });
}
