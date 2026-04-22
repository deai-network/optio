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
import httpProxy from '@fastify/http-proxy';
import type { AuthCallback } from '../auth.js';
import { checkAuth } from '../auth.js';
import { createWidgetUpstreamRegistry } from '../widget-upstream-registry.js';
import {
  resolveWidgetUpstream,
  applyInnerAuthHeaders,
  applyInnerAuthQuery,
  isWriteMethod,
} from '../widget-proxy-core.js';

const WIDGET_CACHE_TTL_MS = 5000;

// Widget URL scheme: /api/widget/<database>/<prefix>/<processId>/<subpath...>
// The database and prefix segments are required so the proxy can resolve the
// correct Mongo database/collection per request (iframe content emits
// relative URLs that would drop query params on navigation, so routing info
// must live in the path).
const WIDGET_URL_PATTERN = /^\/api\/widget\/([^/]+)\/([^/]+)\/([a-f0-9]{24})(?:\/|$|\?)/i;
const WIDGET_PREFIX_STRIP = /^\/api\/widget\/[^/]+\/[^/]+\/[a-f0-9]{24}/i;

interface WidgetProxyInternalOptions {
  dbOpts: DbOptions;
  authenticate: AuthCallback<import('fastify').FastifyRequest>;
  ttlMs?: number;
  verbose?: boolean;
}

function registerWidgetProxy(app: FastifyInstance, opts: WidgetProxyInternalOptions): void {
  const registry = createWidgetUpstreamRegistry({ ttlMs: opts.ttlMs ?? WIDGET_CACHE_TTL_MS });

  function extractRouting(url: string): { database: string; prefix: string; processId: string } | null {
    const m = url.match(WIDGET_URL_PATTERN);
    if (!m) return null;
    try {
      return {
        database: decodeURIComponent(m[1]),
        prefix: decodeURIComponent(m[2]),
        processId: m[3],
      };
    } catch {
      return null;
    }
  }

  app.register(httpProxy, {
    // Leave `upstream` unset (empty string) so that:
    //   - HTTP path: @fastify/reply-from uses replyOptions.getUpstream per-request (unaffected by base)
    //   - WS path:   WebSocketProxy.findUpstream falls through to replyOptions.getUpstream (non-empty
    //               static upstream would override getUpstream and route all WS to a bogus host)
    upstream: '',
    prefix: '/api/widget',
    rewritePrefix: '',
    websocket: true,
    // Use the Node.js http transport (not undici) so that getUpstream can override
    // the connection target per-request via opts.url.hostname/port (undici uses
    // the plugin-level baseUrl and ignores the per-request origin).
    http: {},
    // Passed through to @fastify/reply-from — suppresses its per-request
    // "fetching from remote server" / "response received" INFO lines when the
    // host app isn't running in verbose mode. Errors remain visible.
    disableRequestLogging: !opts.verbose,

    preHandler: async (req: any, reply: any) => {
      const fullUrl: string = req.raw.url ?? req.url;
      const routing = extractRouting(fullUrl);
      if (!routing) {
        reply.code(404).send({ message: 'Invalid widget URL' });
        return;
      }
      const { database, prefix, processId } = routing;

      const authResult = await checkAuth(req, opts.authenticate, isWriteMethod(req.method));
      if (authResult) {
        reply.code(authResult.status).send(authResult.body);
        return;
      }

      let db: Db;
      try {
        ({ db } = resolveDb(opts.dbOpts, { database, prefix }));
      } catch {
        reply.code(404).send({ message: 'Widget upstream not found' });
        return;
      }

      let upstream;
      try {
        upstream = await resolveWidgetUpstream(db, prefix, registry, processId);
      } catch (err) {
        req.log.error({ err }, 'widget-proxy: upstream lookup failed');
        reply.code(503).send({ message: 'Service Unavailable' });
        return;
      }
      if (!upstream) {
        reply.code(404).send({ message: 'Widget upstream not found' });
        return;
      }

      // Store upstream on raw request for getUpstream + rewriteRequestHeaders to pick up
      (req.raw as any).__optioWidget = { processId, upstream };

      // Strip /api/widget/<database>/<prefix>/<processId> from the URL, leaving the sub-path.
      // Then apply query-based inner auth if needed.
      const stripped = fullUrl.replace(WIDGET_PREFIX_STRIP, '') || '/';
      req.raw.url = applyInnerAuthQuery(upstream.innerAuth, stripped);
    },

    // Rewrite headers for outgoing WebSocket handshake (inner-auth injection).
    // The Fastify request object is passed as the second argument, which has
    // request.raw.__optioWidget set by preHandler (runs before the upgrade handshake).
    wsClientOptions: {
      rewriteRequestHeaders: (headers: Record<string, any>, req: any) => {
        const widget = (req.raw as any).__optioWidget;
        if (!widget) return headers;
        return applyInnerAuthHeaders(widget.upstream.innerAuth, headers);
      },
    },

    replyOptions: {
      getUpstream: (req: any) => {
        const widget = (req.raw as any).__optioWidget;
        return widget?.upstream.url ?? 'http://127.0.0.1/';
      },
      rewriteRequestHeaders: (req: any, headers: Record<string, any>) => {
        const widget = (req.raw as any).__optioWidget;
        if (!widget) return headers;
        return applyInnerAuthHeaders(widget.upstream.innerAuth, headers);
      },
      // The proxy's purpose is to make the upstream embeddable in an iframe under
      // optio-api's outer auth. Strip `X-Frame-Options` and any `frame-ancestors`
      // CSP directive so upstreams (marimo, jupyter, internal tools) that default
      // to anti-embedding headers don't block that. Clickjacking defense is
      // provided by optio-api's authenticate callback: the proxy is unreachable
      // without a valid session.
      rewriteHeaders: (headers: Record<string, any>) => {
        const out = { ...headers };
        delete out['x-frame-options'];
        const csp = out['content-security-policy'];
        if (typeof csp === 'string') {
          const stripped = csp
            .split(';')
            .map((d) => d.trim())
            .filter((d) => d.length > 0 && !d.toLowerCase().startsWith('frame-ancestors'))
            .join('; ');
          if (stripped) out['content-security-policy'] = stripped;
          else delete out['content-security-policy'];
        }
        return out;
      },
      // Map connection errors (ECONNREFUSED → InternalServerError/500 by default) to
      // 502 Bad Gateway so callers get a meaningful gateway error.
      // Full error detail is logged server-side; clients only see a fixed, non-leaking body.
      onError: (reply: any, error: any) => {
        reply.request.log.error({ err: error }, 'widget-proxy: upstream error');
        const code = error?.statusCode === 500 ? 502 : (error?.statusCode ?? 502);
        reply.code(code).send({ message: code === 502 ? 'Bad Gateway' : 'Upstream Error' });
      },
    },
  });
}

export type OptioApiOptions = {
  redis: Redis;
  prefix?: string;
  authenticate: AuthCallback<import('fastify').FastifyRequest>;
  /**
   * When true, the widget reverse-proxy's per-request upstream-call logs
   * (`fetching from remote server`, `response received` at INFO) are emitted.
   * Defaults to false (quiet). Errors are logged regardless.
   */
  verbose?: boolean;
} & (
  | { db: Db; mongoClient?: never }
  | { mongoClient: MongoClient; db?: never }
);

const c = initContract();
const apiContract = c.router({ processes: processesContract }, { pathPrefix: '/api' });

export function registerOptioApi(app: FastifyInstance, opts: OptioApiOptions) {
  const { redis } = opts;
  const dbOpts: DbOptions = 'mongoClient' in opts && opts.mongoClient ? { mongoClient: opts.mongoClient } : { db: opts.db! };

  // Widget reverse-proxy lives under /api/widget/<database>/<prefix>/<processId>/…
  // and is registered as an internal part of the Optio API surface so consumers
  // only need one init call.
  registerWidgetProxy(app, {
    dbOpts,
    authenticate: opts.authenticate,
    verbose: opts.verbose,
  });

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
