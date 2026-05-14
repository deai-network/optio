# optio-ui visual playground

Render real `optio-ui` components against fixtures, side-by-side. Useful for
brainstorming visual variants of a component before committing to a design.

## Run

```bash
pnpm --filter optio-dashboard dev:playground
```

Then open http://localhost:5174/.

## Add a new topic

1. Create a directory under `topics/`:

   ```
   playground/topics/<your-topic>/
     index.ts        # default export: { name, slug, App }
     App.tsx         # top-level layout for the topic
     fixtures.ts     # local fixtures
     variants/       # optional: side-by-side variants
   ```

2. `index.ts` exports a default with the contract:

   ```ts
   import { App } from './App.js';
   export default { name: 'Your topic', slug: 'your-topic', App };
   ```

3. Register the topic in `playground/main.tsx`:

   ```ts
   import yourTopic from './topics/your-topic/index.js';
   const TOPICS: Topic[] = [logPanel, yourTopic];
   ```

That's it. The side-nav picks it up automatically.

## Notes

- The playground is not a production artifact. The dashboard's real entry
  point is `src/app/`, which has its own `vite.config.ts` and build.
- Topics own their fixtures. Don't reach across topics; copy if needed.
- The vite config aliases `optio-ui/` to the package source so HMR works on
  optio-ui edits.
