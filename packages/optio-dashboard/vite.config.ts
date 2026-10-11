import { defineConfig, searchForWorkspaceRoot } from 'vite';
import react from '@vitejs/plugin-react';
import basicSsl from '@vitejs/plugin-basic-ssl';
import path from 'path';

// Running the dev server beside another stack (e.g. on the excavator host,
// which holds :5173): OPTIO_DEV_PORT picks the port, OPTIO_DEV_HTTPS=1 serves
// it over a self-signed cert like excavator's own dev server, and
// OPTIO_DEV_HOST is the hostname the browser uses (listen on all interfaces,
// and let the HMR websocket through). All unset: unchanged defaults.
const devPort = Number(process.env.OPTIO_DEV_PORT) || undefined;
const devHttps = process.env.OPTIO_DEV_HTTPS === '1';
const devHost = process.env.OPTIO_DEV_HOST;
// OPTIO_DEV_VULTUS: a unitas checkout whose vultus source the dev server uses
// instead of the installed vultus-antd, without an install here (that
// checkout's node_modules may belong to another worktree): vultus-antd and
// vultus-core are aliased to its source, the dev server may serve it, and the
// libraries vultus shares context with resolve to the app's single copy.
// vultus-core is aliased too because optio-ui imports it directly: without the
// alias optio-ui would get this workspace's copy and vultus-antd the
// checkout's, two components contexts, and a Spinner lent through vultus-antd's
// VultusProvider would not reach optio-ui. Unset: unchanged.
const devVultus = process.env.OPTIO_DEV_VULTUS;
// OPTIO_DEV_DAEO (Ant Design X experiment): a daeo-style checkout whose
// packages the dev server takes by source, for the demo's Daeo look
// (src/app/styleMode.tsx) where they are not installed (no GitHub Packages
// token); cssinjs is deduped with antd's. Unset: unchanged.
const devDaeo = process.env.OPTIO_DEV_DAEO;
const daeoSrc = (pkg: string, file: string) => path.join(devDaeo ?? '', 'packages', pkg, 'src', file);

export default defineConfig({
  plugins: [react(), ...(devHttps ? [basicSsl()] : [])],
  // Force a single instance of these packages across the dependency graph.
  // optio-ui is consumed as source from the workspace; under pnpm its
  // @tanstack/react-query and React can resolve via a different node_modules
  // path than the app's, yielding duplicate module instances. optimizeDeps
  // can also bundle @tanstack/react-query twice (once direct, once inlined
  // inside @ts-rest/react-query whose peer-dep resolution leads through a
  // different pnpm hash). Two instances at runtime mean two React contexts,
  // and useQueryClient throws "No QueryClient set". React itself is deduped
  // here too — duplicate React breaks every context, including
  // QueryClientProvider's.
  resolve: {
    // optio-ui added: it holds the module-level widget registry; a duplicate
    // instance means widgets registered by sibling packages (e.g.
    // optio-conversation-ui) land in a different Map than ProcessWidget reads.
    dedupe: [
      'react', 'react-dom', '@tanstack/react-query', '@ts-rest/react-query', 'optio-ui',
      // vultus-core is only deduped beside its alias: the app root cannot
      // resolve it (it is not a dependency here), and Vite resolves deduped
      // packages from the root.
      ...(devVultus ? ['antd', 'react-i18next', 'i18next', 'vultus-core'] : []),
      ...(devDaeo ? ['antd', '@ant-design/cssinjs'] : []),
    ],
    alias: [
      ...(devVultus ? [
        { find: /^vultus-antd\/markdown$/, replacement: path.join(devVultus, 'packages/vultus-antd/src/Markdown.tsx') },
        { find: /^vultus-antd$/, replacement: path.join(devVultus, 'packages/vultus-antd/src/index.ts') },
        { find: /^vultus-core\/link$/, replacement: path.join(devVultus, 'packages/vultus-core/src/LinkContext.tsx') },
        { find: /^vultus-core$/, replacement: path.join(devVultus, 'packages/vultus-core/src/index.ts') },
      ] : []),
      ...(devDaeo ? [
        { find: /^@deai-network\/daeo-style-for-vultus$/, replacement: daeoSrc('daeo-style-for-vultus', 'index.ts') },
        { find: /^@deai-network\/daeo-style-antd\/provider$/, replacement: daeoSrc('daeo-style-antd', 'provider.ts') },
        { find: /^@deai-network\/daeo-style-antd\/styles\.css$/, replacement: daeoSrc('daeo-style-antd', 'styles.css') },
        { find: /^@deai-network\/daeo-style-antd$/, replacement: daeoSrc('daeo-style-antd', 'index.ts') },
        { find: /^@deai-network\/daeo-style-tokens\/theme\.css$/, replacement: daeoSrc('daeo-style-tokens', 'theme.css') },
        { find: /^@deai-network\/daeo-style-tokens\/fonts\.css$/, replacement: daeoSrc('daeo-style-tokens', 'fonts.css') },
        { find: /^@deai-network\/daeo-style-tokens$/, replacement: daeoSrc('daeo-style-tokens', 'index.ts') },
      ] : []),
    ],
  },
  root: path.resolve(__dirname, 'src/app'),
  build: {
    outDir: path.resolve(__dirname, 'dist/public'),
    emptyOutDir: true,
  },
  server: {
    ...(devPort ? { port: devPort, strictPort: true } : {}),
    ...(devHost ? { host: true, allowedHosts: [devHost] } : {}),
    ...(devVultus || devDaeo
      ? { fs: { allow: [searchForWorkspaceRoot(process.cwd()), ...[devVultus, devDaeo].filter((d): d is string => !!d)] } }
      : {}),
    proxy: {
      // Object form with `ws: true` is required so WebSocket upgrades under
      // /api (e.g. the widget reverse-proxy at /api/widget/…/ws) are forwarded
      // to the backend. With the bare-string form, Vite only proxies HTTP.
      // OPTIO_API_URL: where the dashboard API (`make run-api`, port from
      // PORT) listens, when not on :3000 (e.g. beside another stack).
      '/api': {
        target: process.env.OPTIO_API_URL ?? 'http://localhost:3000',
        ws: true,
      },
    },
  },
});
