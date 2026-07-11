import { defineConfig } from 'vitest/config';

export default defineConfig({
  // Pin single react instances when consumed via workspace links.
  resolve: { dedupe: ['react', 'react-dom'] },
  test: {
    environment: 'jsdom',
    globals: true,
    testTimeout: 15000,
    setupFiles: ['./vitest.setup.ts'],
  },
});
