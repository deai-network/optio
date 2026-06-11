// @ts-nocheck — type inference for ts-rest router handlers requires the full
// monorepo type resolution. The adapter is tested via API integration tests.
import { initServer } from '@ts-rest/fastify';
import { initContract } from '@ts-rest/core';
import { processesContract } from 'optio-contracts';
import type { FastifyInstance } from 'fastify';
import type { Db } from 'mongodb';
import type { MongoClient } from 'mongodb';
import type { Redis } from 'ioredis';
import * as handlers from '../handlers.js';
import { findProcessByEitherId } from '../process-id-resolver.js';
import { createListPoller, createTreePoller, createMultiTreePoller, createSessionEventsPoller } from '../stream-poller.js';
import { discoverInstances } from '../discovery.js';
import { resolveDb, type DbOptions } from '../resolve.js';
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
import { forwardAgentInput } from '../agent-input.js';

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
      // Disable the upstream request timeout for widget proxying.
      // @fastify/reply-from defaults to a 10s upstream timeout
      // (lib/request.js:325-326). An embedded agent's interactive OAuth login
      // (e.g. opencode's device-code flow) holds the proxied /oauth/callback
      // POST open server-side — polling the provider, sending zero bytes — for
      // as long as the human takes to authorize, far past 10s. reply-from would
      // abort that and surface a 504 in the iframe. `0` disables the timeout,
      // and survives reply-from's `opts.timeout ?? default` (a falsy
      // http.requestOptions.timeout gets clobbered back to 10000 instead).
      //
      // Blanket (all widget upstreams), not scoped to the callback path:
      // http-proxy's replyOpts is static, so per-path scoping would need a
      // separate route. Safe because no hop in the Optio/Excavator stack
      // imposes its own idle timeout (Caddy streams an in-flight response with
      // no deadline; the host Fastify sets no requestTimeout). If a deployment
      // ever sits behind an ingress/LB with a sub-request idle timeout, move the
      // OAuth flow to client-side short-polling rather than lean on this
      // held-open connection.
      timeout: 0,
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

/**
 * `verbose`: when true, the widget reverse-proxy's per-request upstream-call
 * logs (`fetching from remote server`, `response received` at INFO) are
 * emitted. Defaults to false (quiet). Errors are logged regardless.
 *
 * Two-form options:
 *  - Sugar form (typical hosts): supply `{ db | mongoClient, redis, ... }`.
 *    registerOptioApi constructs an `OptioContext` internally, returns it,
 *    and wires fastify's onClose hook to `ctx.closeAll()`.
 *  - Explicit form (power users / shared-ctx hosts): supply `{ ctx, ... }`.
 *    Caller owns ctx lifecycle; the adapter does NOT call closeAll on it.
 *    registerOptioApi returns void in this form.
 */
export type OptioApiOptions =
  | (BaseOptioApiOptions & { ctx: OptioContext })
  | (BaseOptioApiOptions & { redis: Redis } & (
      | { db: Db; mongoClient?: never }
      | { mongoClient: MongoClient; db?: never }
    ));

interface BaseOptioApiOptions {
  prefix?: string;
  authenticate: AuthCallback<import('fastify').FastifyRequest>;
  verbose?: boolean;
}

const c = initContract();
const apiContract = c.router({ processes: processesContract }, { pathPrefix: '/api' });

export function registerOptioApi(app: FastifyInstance, opts: OptioApiOptions): OptioContext | void {
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
    app.addHook('onClose', () => ctx.closeAll());
  }

  // Global auth enforcement. Runs before route handlers (and before the
  // widget-proxy plugin's preHandler), so REST, SSE, discovery, and widget
  // routes all pass through checkAuth here. The widget-proxy preHandler's
  // own checkAuth call is left in place as defense in depth.
  app.addHook('onRequest', async (req, reply) => {
    const authResult = await checkAuth(req, opts.authenticate, isWriteMethod(req.method));
    if (authResult) {
      reply.code(authResult.status).send(authResult.body);
    }
  });

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
      const result = await handlers.launchProcess(
        ctx, query, params.id, body?.resume === true, body?.sessionId ?? null,
      );
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

  // Pre-check for legacy metadata.* query params on GET /api/processes (list endpoint).
  // Must run after onRequest (auth) but before ts-rest route handlers.
  app.addHook('preHandler', async (request: any, reply: any) => {
    if (request.method === 'GET' && request.url.split('?')[0] === '/api/processes') {
      const legacyKeys = detectLegacyMetadataParams(request.query ?? {});
      if (legacyKeys.length > 0) {
        reply.code(400).send({ message: formatLegacyMetadataMessage(legacyKeys) });
        return;
      }
    }
  });

  app.register(s.plugin(routes));

  app.get('/api/optio/instances', async (_request: any, reply: any) => {
    const instances = await discoverInstances(dbOpts, redis);
    reply.send({ instances });
  });

  app.post(
    '/api/widget-control/:database/:prefix/:processId',
    async (request: any, reply: any) => {
      const { database, prefix, processId } = request.params as {
        database: string; prefix: string; processId: string;
      };
      const body = request.body as { text?: unknown; key?: unknown };
      let payload: { text: string } | { key: string };
      if (typeof body?.text === 'string' && body.text.length > 0) {
        payload = { text: body.text };
      } else if (typeof body?.key === 'string' && body.key.length > 0) {
        // A single navigation keystroke for an empty input box; the host's
        // /input route validates it against its key allowlist.
        payload = { key: body.key };
      } else {
        reply.code(400).send({ message: 'body.text or body.key (non-empty string) required' });
        return;
      }
      let db;
      try {
        ({ db } = resolveDb(dbOpts, { database, prefix }));
      } catch {
        reply.code(404).send({ message: 'session not running' });
        return;
      }
      const result = await forwardAgentInput(db, prefix, processId, payload);
      reply.code(result.status).send(result.body);
    },
  );

  app.get('/api/processes/:id/tree/stream', async (request: any, reply: any) => {
    const { id } = request.params as { id: string };
    let sseOpts;
    try {
      sseOpts = parseSseOptions((request.query as Record<string, unknown>) ?? {});
    } catch (e) {
      reply.code(400).send({ message: (e as Error).message });
      return;
    }
    const { db, prefix } = resolveDb(dbOpts, sseOpts);

    const col = db.collection(`${prefix}_processes`);
    const proc = await findProcessByEitherId(col, id);
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
      maxDepth: sseOpts.maxDepth,
    });

    poller.start();
    request.raw.on('close', () => poller.stop());
  });

  app.get('/api/processes/tree/multi/stream', async (request: any, reply: any) => {
    const rawQuery = (request.query as Record<string, unknown>) ?? {};
    const treeIdsParam = (rawQuery.treeIds as string | undefined) ?? '';
    const flatIdsParam = (rawQuery.flatIds as string | undefined) ?? '';
    const treeInputIds = treeIdsParam ? treeIdsParam.split(',').filter(Boolean) : [];
    const flatInputIds = flatIdsParam ? flatIdsParam.split(',').filter(Boolean) : [];
    if (treeInputIds.length === 0 && flatInputIds.length === 0) {
      reply.code(400).send({ message: 'treeIds or flatIds must be non-empty' });
      return;
    }

    let sseOpts;
    try {
      sseOpts = parseSseOptions(rawQuery);
    } catch (e) {
      reply.code(400).send({ message: (e as Error).message });
      return;
    }
    const { db, prefix } = resolveDb(dbOpts, sseOpts);
    const col = db.collection(`${prefix}_processes`);

    async function resolveOne(id: string): Promise<{ id: string; proc: any | null }> {
      const proc = await findProcessByEitherId(col, id);
      return { id, proc };
    }
    const [treeResolved, flatResolved] = await Promise.all([
      Promise.all(treeInputIds.map(resolveOne)),
      Promise.all(flatInputIds.map(resolveOne)),
    ]);

    const missing: string[] = [];
    const treeRoots: { rootId: any; baseDepth: number }[] = [];
    const flatIds: any[] = [];
    for (const r of treeResolved) {
      if (!r.proc) missing.push(r.id);
      else treeRoots.push({ rootId: r.proc.rootId, baseDepth: r.proc.depth });
    }
    for (const r of flatResolved) {
      if (!r.proc) missing.push(r.id);
      else flatIds.push(r.proc._id);
    }

    reply.raw.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    });

    const sendEvent = (data: unknown) => {
      reply.raw.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    sendEvent({ type: 'resolution', missing });

    if (treeRoots.length === 0 && flatIds.length === 0) {
      return;
    }

    const poller = createMultiTreePoller({
      db,
      prefix,
      sendEvent,
      onError: () => reply.raw.end(),
      treeRoots,
      flatIds,
      maxDepth: sseOpts.maxDepth,
    });
    poller.start();
    request.raw.on('close', () => poller.stop());
  });

  app.get('/api/processes/stream', async (request: any, reply: any) => {
    const rawQuery = (request.query as Record<string, unknown>) ?? {};
    try {
      checkLegacyMetadataParams(rawQuery);
    } catch (e) {
      if (e instanceof LegacyMetadataParamError) {
        reply.code(400).send({ message: e.message });
        return;
      }
      throw e;
    }
    let sseOpts;
    try {
      sseOpts = parseSseOptions(rawQuery);
    } catch (e) {
      reply.code(400).send({ message: (e as Error).message });
      return;
    }
    const { db, prefix } = resolveDb(dbOpts, sseOpts);

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
      metadataFilter: sseOpts.metadataFilter,
    });

    poller.start();
    request.raw.on('close', () => poller.stop());
  });

  app.get('/api/session-events/stream', async (request: any, reply: any) => {
    const rawQuery = (request.query as Record<string, unknown>) ?? {};
    const sessionId = typeof rawQuery.sessionId === 'string' ? rawQuery.sessionId : '';
    let sseOpts;
    try {
      sseOpts = parseSseOptions(rawQuery);
    } catch (e) {
      reply.code(400).send({ message: (e as Error).message });
      return;
    }
    const { db, prefix } = resolveDb(dbOpts, sseOpts);

    reply.raw.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    });

    const sendEvent = (data: unknown) => {
      reply.raw.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    // No sessionId → match nothing (still keeps the connection open, emitting
    // nothing). Single-operator deployments always get per-launch routing
    // because the UI always sends its token.
    if (!sessionId) {
      request.raw.on('close', () => {});
      return;
    }

    const poller = createSessionEventsPoller({
      db,
      prefix,
      sessionId,
      sendEvent,
      onError: () => reply.raw.end(),
    });

    poller.start();
    request.raw.on('close', () => poller.stop());
  });

  return explicit ? undefined : ctx;
}
