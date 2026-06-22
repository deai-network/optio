import { Markdown } from './Markdown.js';

// The one public seam for rendering an assistant answer outside the chat
// widget: markdown with embedded mermaid diagrams and all the rendering
// fixes (stable component map, list spacing, GFM tables, streaming-safe
// diagram fallback) baked in. Consumers pass the answer text and stay
// ignorant of the machinery — improvements land here and flow everywhere.
//
// ── Consumer requirements (this component is NOT fully self-contained) ──
// This package ships TypeScript *source* (package.json `main`/`types` point
// at src/), so the consuming application's bundler compiles `Markdown.tsx`
// directly. That transitively imposes these requirements on the consumer:
//
//  1. CSS-import handling. `Markdown.tsx` does `import 'katex/dist/katex.min.css'`
//     (a side-effect import) so LaTeX math renders styled. The consumer's
//     bundler MUST handle `.css` side-effect imports — Vite and webpack (with
//     a css loader) do this out of the box. A plain `tsc`/node-ESM consumer
//     with no bundler will fail to resolve the `.css` import at load time.
//
//  2. KaTeX fonts. That stylesheet references ~20 woff2 files via url(). The
//     bundler must emit them as assets and serve them on the same origin/path
//     it serves the CSS from. If the CSS loads but fonts don't, math glyphs
//     render as tofu boxes. (Both `katex` and its CSS come in as a dependency
//     of this package — nothing extra to install, only to *bundle/serve*.)
//
//  3. Mermaid runtime. ```mermaid fences render client-side via the `mermaid`
//     library (a dependency of this package). It needs a DOM — render on the
//     client; it is not SSR-safe. No CSS import is required for mermaid.
//
//  4. antd theme (optional). Colors for tables/blockquotes/code/math follow
//     the host's antd theme tokens via `theme.useToken()`. Without a
//     surrounding <ConfigProvider> the default (light) token set is used;
//     wrap the app in ConfigProvider to get dark mode / custom themes.
//
// In short: math (KaTeX CSS + fonts) is the only piece that must arrive via a
// route outside this component — through the consumer's bundler/asset pipeline,
// not through the JS import alone.
export function AnswerBlock({ text }: { text: string }) {
  return <Markdown>{text}</Markdown>;
}
