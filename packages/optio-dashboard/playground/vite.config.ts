import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  root: __dirname,
  server: { port: 5174, host: 'localhost', strictPort: true },
  resolve: {
    alias: {
      'optio-ui/': path.resolve(__dirname, '../../optio-ui/src') + '/',
    },
  },
});
