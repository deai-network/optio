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
