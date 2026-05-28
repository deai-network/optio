import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  // optio-ui is consumed as source from the workspace; under pnpm its
  // @tanstack/react-query (and React) can resolve via a different node_modules
  // path than the app's, yielding duplicate module instances → separate React
  // contexts → "No QueryClient set". Dedupe forces a single instance of each.
  resolve: {
    dedupe: ['react', 'react-dom', '@tanstack/react-query', '@ts-rest/react-query'],
  },
  root: path.resolve(__dirname, 'src/app'),
  // Force a single instance of these packages across the dependency
  // graph. Without this, optimizeDeps can bundle @tanstack/react-query
  // twice (once as a direct dep, once inlined inside @ts-rest/react-query
  // because the peer-dep resolution path leads through a different
  // pnpm hash). Two instances at runtime mean two React contexts, and
  // useQueryClient throws "No QueryClient set". React itself goes in
  // here too — duplicate React breaks every context, including
  // QueryClientProvider's.
  resolve: {
    dedupe: [
      '@tanstack/react-query',
      'react',
      'react-dom',
    ],
  },
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
