import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
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
    // optio-claudecode-ui) land in a different Map than ProcessWidget reads.
    dedupe: ['react', 'react-dom', '@tanstack/react-query', '@ts-rest/react-query', 'optio-ui'],
  },
  root: path.resolve(__dirname, 'src/app'),
  build: {
    outDir: path.resolve(__dirname, 'dist/public'),
    emptyOutDir: true,
  },
  server: {
    proxy: {
      // Object form with `ws: true` is required so WebSocket upgrades under
      // /api (e.g. the widget reverse-proxy at /api/widget/…/ws) are forwarded
      // to the backend. With the bare-string form, Vite only proxies HTTP.
      '/api': {
        target: 'http://localhost:3000',
        ws: true,
      },
    },
  },
});
