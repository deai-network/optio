import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
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
