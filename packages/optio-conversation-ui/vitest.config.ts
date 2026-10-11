import { defineConfig } from 'vitest/config';

export default defineConfig({
  resolve: {
    // Prevent duplicate React instances when using linked (link:) packages
    // that resolve React from their own repo's node_modules
    // Also the libraries vultus and daeo-style (linked sibling checkouts on the
    // Ant Design X experiment) share context through: one antd, one cssinjs,
    // one vultus-antd (vultus-core, the spinner slot's context, is a single
    // linked copy already: every importer reaches it by its real path).
    dedupe: ['react', 'react-dom', 'antd', '@ant-design/cssinjs', 'vultus-antd'],
  },
  // vultus-antd from the npm registry ships .tsx source without a tsconfig, so
  // esbuild would compile it with classic JSX (React.createElement) and every
  // Markdown render would fail with "React is not defined". Same runtime as
  // tsconfig's "jsx": "react-jsx".
  esbuild: { jsx: 'automatic' },
  test: {
    environment: 'jsdom',
    globals: true,
    testTimeout: 60000,
    // A wider fork pool pushes widget tests over that timeout.
    maxWorkers: 2,
    setupFiles: ['./vitest.setup.ts'],
  },
});
