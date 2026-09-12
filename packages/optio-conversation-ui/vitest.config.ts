import { defineConfig } from 'vitest/config';

export default defineConfig({
  resolve: {
    // Prevent duplicate React instances when using linked (link:) packages
    // that resolve React from their own repo's node_modules
    dedupe: ['react', 'react-dom'],
  },
  // vultus-antd from the npm registry ships .tsx source without a tsconfig, so
  // esbuild would compile it with classic JSX (React.createElement) and every
  // Markdown render would fail with "React is not defined". Same runtime as
  // tsconfig's "jsx": "react-jsx".
  esbuild: { jsx: 'automatic' },
  test: {
    environment: 'jsdom',
    globals: true,
    testTimeout: 15000,
    setupFiles: ['./vitest.setup.ts'],
  },
});
