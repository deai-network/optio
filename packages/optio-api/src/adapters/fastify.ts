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
import { createHash } from 'node:crypto';
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

function rewriteResponseHeaders(headers: Record<string, any>): Record<string, any> {
  // The proxy's purpose is to make the upstream embeddable in an iframe under
  // optio-api's outer auth. Strip `X-Frame-Options` and any `frame-ancestors`
  // CSP directive so upstreams (marimo, jupyter, internal tools) that default
  // to anti-embedding headers don't block that. Clickjacking defense is
  // provided by optio-api's authenticate callback: the proxy is unreachable
  // without a valid session.
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
}

function widgetProxyPrefix(database: string, prefix: string, processId: string): string {
  return `/api/widget/${encodeURIComponent(database)}/${encodeURIComponent(prefix)}/${processId}/`;
}

interface HtmlInjection {
  html: string;
  stripScriptSha256: string; // base64-encoded SHA-256 of the inline script body (for CSP)
}

function injectBaseHref(html: string, proxyPrefix: string): HtmlInjection {
  // Inject two things right after <head>:
  //
  // 1. <base href="<proxyPrefix>"> — forces relative URLs in the page to
  //    resolve against the proxy root regardless of how deep the current
  //    document URL is.  Required for SPAs (like opencode) whose HTML uses
  //    relative asset paths ("./assets/x.js") but are loaded via a routing-
  //    deep URL like /api/widget/<db>/<prefix>/<pid>/<workdir>/session/.
  //    Without it, assets resolve to `.../workdir/session/assets/x.js` and
  //    hit the upstream's SPA fallback with wrong MIME types.
  //
  // 2. An inline <script> that runs before the SPA bundle and strips the
  //    proxy prefix from `location.pathname` via history.replaceState.
  //    This makes the SPA's client-side router (e.g. @solidjs/router, react
  //    -router) see the application's intended URL space (`/:dir/session/`
  //    in opencode's case) rather than the proxy-nested one
  //    (`/api/widget/<db>/<prefix>/<pid>/:dir/session/`).  `<base href>` is
  //    unaffected, so asset loading continues to work.
  //
  // Because the inline script violates `script-src 'self'` CSPs, we also
  // return its SHA-256 so the caller can append `'sha256-…'` to the
  // outgoing CSP's script-src directive.
  const baseTag = `<base href="${proxyPrefix}">`;
  // Escape regex-meta characters in the prefix for the runtime match.
  const prefixLiteralForRegex = proxyPrefix
    .replace(/\/$/, '') // the script uses the stripped form without trailing slash
    .replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const scriptBody =
    `(function(){var r=new RegExp('^' + ${JSON.stringify(prefixLiteralForRegex)});` +
    `var p=location.pathname;var m=p.match(r);` +
    `if(m){history.replaceState(null,'',(p.slice(m[0].length)||'/')+location.search+location.hash);}` +
    `})();`;
  const stripScriptSha256 = createHash('sha256').update(scriptBody, 'utf-8').digest('base64');
  const stripScript = `<script>${scriptBody}</script>`;
  const injection = `${baseTag}${stripScript}`;
  const m = html.match(/<head(\s[^>]*)?>/i);
  const transformed = m
    ? html.replace(m[0], `${m[0]}${injection}`)
    : `${injection}${html}`;
  return { html: transformed, stripScriptSha256 };
}

// Extend the upstream CSP's script-src (or add one) with the SHA-256 of our
// injected inline script.  Called only when HTML body rewriting has happened.
function appendScriptHashToCsp(csp: string, scriptHashBase64: string): string {
  const hashToken = `'sha256-${scriptHashBase64}'`;
  const parts = csp
    .split(';')
    .map((d) => d.trim())
    .filter((d) => d.length > 0);
  let found = false;
  const updated = parts.map((directive) => {
    const lower = directive.toLowerCase();
    if (lower.startsWith('script-src ') || lower === 'script-src') {
      found = true;
      return `${directive} ${hashToken}`;
    }
    return directive;
  });
  if (!found) updated.push(`script-src ${hashToken}`);
  return updated.join('; ');
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
      (req.raw as any).__optioWidget = {
        processId,
        upstream,
        proxyPrefix: widgetProxyPrefix(database, prefix, processId),
      };

      // Strip /api/widget/<database>/<prefix>/<processId> from the URL, leaving the sub-path.
      // Then apply query-based inner auth if needed.
      const stripped = fullUrl.replace(WIDGET_PREFIX_STRIP, '') || '/';
      req.raw.url = applyInnerAuthQuery(upstream.innerAuth, stripped);

      // Strip Accept-Encoding so the upstream returns uncompressed bodies.
      // We need to rewrite text/html bodies in onResponse to inject <base href>,
      // and handling gzip/br variants per upstream is more friction than the
      // bandwidth savings are worth for a dev-tool reverse proxy.
      delete (req.raw.headers as Record<string, any>)['accept-encoding'];
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
      rewriteHeaders: rewriteResponseHeaders,
      // onResponse takes over the response forwarding so we can rewrite
      // text/html bodies (inject <base href> — see injectBaseHref).
      // Non-HTML responses stream through unchanged.
      //
      // We use reply.hijack() + reply.raw directly because fastify 5's
      // reply.send() does not accept Node IncomingMessage streams, and the
      // header/body coordination for a transformed HTML body is simpler on
      // the raw ServerResponse anyway.  reply-from does NOT auto-apply
      // rewriteHeaders when onResponse is defined, so apply them here
      // explicitly to the outgoing response.
      onResponse: (request: any, reply: any, res: any) => {
        // @fastify/reply-from passes a wrapper: res.stream is the Readable
        // upstream body; res.headers / res.statusCode are the top-level fields.
        const stream: NodeJS.ReadableStream = res.stream;
        reply.hijack();
        const rawRes: import('http').ServerResponse = reply.raw;
        const statusCode: number = res.statusCode ?? 200;
        const incomingHeaders = res.headers as Record<string, any>;
        const contentType = String(incomingHeaders['content-type'] ?? '').toLowerCase();

        if (!contentType.includes('text/html')) {
          const outHeaders = rewriteResponseHeaders(incomingHeaders);
          rawRes.writeHead(statusCode, outHeaders as any);
          stream.pipe(rawRes);
          stream.on('error', (err: any) => {
            request.log.error({ err }, 'widget-proxy: upstream body read error');
            if (!rawRes.headersSent) rawRes.writeHead(502);
            rawRes.end();
          });
          return;
        }

        const widget = (request.raw as any).__optioWidget;
        const proxyPrefix: string = widget?.proxyPrefix ?? '/';
        const chunks: Buffer[] = [];
        stream.on('data', (c: Buffer) => {
          chunks.push(c);
        });
        stream.on('end', () => {
          const html = Buffer.concat(chunks).toString('utf-8');
          const { html: transformed, stripScriptSha256 } = injectBaseHref(html, proxyPrefix);
          const body = Buffer.from(transformed, 'utf-8');
          // Rebuild headers: copy upstream, then override transform-affected ones.
          const outHeaders: Record<string, any> = rewriteResponseHeaders(incomingHeaders);
          outHeaders['content-length'] = String(body.byteLength);
          // Drop content-encoding — we already decoded if upstream gzipped.
          // (We also strip Accept-Encoding on the way out in preHandler so in
          // practice upstream should send identity, but belt-and-braces.)
          delete outHeaders['content-encoding'];
          // If a CSP is present, allowlist our injected inline script by hash
          // so it isn't blocked by `script-src 'self'`.
          const csp = outHeaders['content-security-policy'];
          if (typeof csp === 'string' && csp.length > 0) {
            outHeaders['content-security-policy'] = appendScriptHashToCsp(csp, stripScriptSha256);
          }
          rawRes.writeHead(statusCode, outHeaders as any);
          rawRes.end(body);
        });
        stream.on('error', (err: any) => {
          request.log.error({ err }, 'widget-proxy: upstream body read error');
          if (!rawRes.headersSent) rawRes.writeHead(502);
          rawRes.end();
        });
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
