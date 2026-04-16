import { defineConfig } from 'vitest/config';

export default defineConfig({
  resolve: {
    // Prevent duplicate React instances when using linked (link:) packages
    // that resolve React from their own repo's node_modules
    dedupe: ['react', 'react-dom'],
  },
  test: {
    environment: 'jsdom',
    globals: true,
  },
});
