import { defineConfig } from 'vite';
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
    dedupe: ['react', 'react-dom', '@tanstack/react-query', '@ts-rest/react-query', 'optio-ui'],
  },
  root: path.resolve(__dirname, 'src/app'),
  build: {
    outDir: path.resolve(__dirname, 'dist/public'),
    emptyOutDir: true,
  },
  server: {
    ...(devPort ? { port: devPort, strictPort: true } : {}),
    ...(devHost ? { host: true, allowedHosts: [devHost] } : {}),
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
