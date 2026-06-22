import { memo } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { Typography, theme } from 'antd';
import { Mermaid } from './Mermaid.js';
// KaTeX renders to bare HTML that needs its stylesheet (and the fonts it
// references) to lay out; the consuming app's bundler serves both. Imported
// here so any consumer of this package gets math styling without extra wiring.
import 'katex/dist/katex.min.css';

// Markdown spacing is controlled by an injected stylesheet rather than inline
// styles: react-markdown wraps loose list items in <p>, and an inline
// margin on those paragraphs (which would win over CSS) bloats the space
// inside and around lists. Keeping the paragraph margin in CSS lets us zero it
// inside list items while preserving normal between-paragraph spacing.
const MD_STYLE_ID = 'optio-cc-md-style';
function ensureMarkdownStyle(): void {
  if (typeof document === 'undefined' || document.getElementById(MD_STYLE_ID)) return;
  const el = document.createElement('style');
  el.id = MD_STYLE_ID;
  el.textContent = `.optio-cc-md {
    /* The assistant bubble uses white-space: pre-wrap for plain text; markdown
       renders block elements with their own spacing, so preserving the source's
       blank lines around blocks (e.g. the \\n\\n around a list) would inject
       literal empty lines before/after it. Reset to normal for markdown. */
    white-space: normal;
  }
  .optio-cc-md .optio-cc-p { margin: 0 0 8px 0; }
  .optio-cc-md > .optio-cc-p:last-child { margin-bottom: 0; }
  .optio-cc-md li .optio-cc-p { margin: 0; }
  .optio-cc-md ul, .optio-cc-md ol { margin: 4px 0; padding-left: 20px; }
  .optio-cc-md li { margin: 0; }`;
  document.head.appendChild(el);
}

// react-markdown renders each mapped element via createElement(components[tag],
// …), so the functions in this map are component *types*. It MUST be a stable
// module-level constant: defining it inline in the render would hand
// react-markdown a new set of component types on every render, making it
// remount the whole subtree — which reset embedded Mermaid diagrams to their
// source on each re-render. None of these renderers depend on props or state,
// so hoisting is safe.
const COMPONENTS: Components = {
  p: ({ children }) => <Typography.Paragraph className="optio-cc-p">{children}</Typography.Paragraph>,
  code: ({ className, children }) => {
    // remark fenced blocks carry `language-<lang>` on the <code>.
    // ```mermaid renders as a diagram; everything else stays inline code.
    const text = String(children);
    const lang = /language-(\w+)/.exec(className ?? '')?.[1];
    if (lang === 'mermaid') {
      return <Mermaid chart={text.replace(/\n$/, '')} />;
    }
    // Block code (a fenced block carries a language, or spans multiple lines)
    // is owned by the <pre> container below, which paints one background for
    // the whole block. Render it as a plain <code> so it does NOT reuse antd's
    // inline-code Typography span — that span's background tiled per wrapped
    // line instead of covering the block.
    if (lang != null || text.includes('\n')) {
      return <code>{children}</code>;
    }
    return <Typography.Text code>{children}</Typography.Text>;
  },
  // Block container for fenced code: one themed background for the whole block.
  // (Mermaid fences render as diagrams, not code, so they are not boxed.)
  pre: ({ children }) => {
    const { token } = theme.useToken();
    const child = (Array.isArray(children) ? children[0] : children) as { type?: unknown };
    if (child?.type === Mermaid) return <>{children}</>;
    return (
      <pre
        style={{
          background: token.colorFillQuaternary,
          border: `1px solid ${token.colorBorderSecondary}`,
          borderRadius: token.borderRadius,
          padding: '8px 12px',
          margin: '4px 0',
          overflow: 'auto',
          fontSize: '0.85em',
        }}
      >
        {children}
      </pre>
    );
  },
  strong: ({ children }) => <Typography.Text strong>{children}</Typography.Text>,
  a: ({ href, children }) => (
    <Typography.Link href={href} target="_blank" rel="noreferrer">
      {children}
    </Typography.Link>
  ),
  // GFM tables render as bare <table> with no browser border styling;
  // give them collapsed borders + cell padding so they read as tables.
  table: ({ children }) => (
    <table style={{ borderCollapse: 'collapse', margin: '4px 0', width: '100%' }}>{children}</table>
  ),
  // Spread the incoming `style` LAST: remark-gfm passes column alignment
  // (`:--` / `:-:` / `--:`) as a `style={{textAlign}}` prop on each cell,
  // so it must override the base left default rather than be dropped.
  //
  // These renderers are component *types* (react-markdown createElement's
  // them), so theme.useToken() inside them is a legal hook call — colors
  // follow the host app's ConfigProvider light/dark algorithm.
  th: ({ children, style }) => {
    const { token } = theme.useToken();
    return (
      <th
        style={{
          border: `1px solid ${token.colorBorderSecondary}`,
          padding: '4px 8px',
          background: token.colorFillQuaternary,
          textAlign: 'left',
          ...style,
        }}
      >
        {children}
      </th>
    );
  },
  td: ({ children, style }) => {
    const { token } = theme.useToken();
    return (
      <td style={{ border: `1px solid ${token.colorBorderSecondary}`, padding: '4px 8px', ...style }}>
        {children}
      </td>
    );
  },
  blockquote: ({ children }) => {
    const { token } = theme.useToken();
    return (
      <blockquote
        style={{
          borderLeft: `3px solid ${token.colorBorderSecondary}`,
          paddingLeft: 10,
          margin: '4px 0',
          color: token.colorTextSecondary,
        }}
      >
        {children}
      </blockquote>
    );
  },
  hr: () => {
    const { token } = theme.useToken();
    return (
      <hr style={{ border: 'none', borderTop: `1px solid ${token.colorBorderSecondary}`, margin: '8px 0' }} />
    );
  },
  ul: ({ children }) => <ul style={{ margin: '4px 0', paddingLeft: 20 }}>{children}</ul>,
  ol: ({ children }) => <ol style={{ margin: '4px 0', paddingLeft: 20 }}>{children}</ol>,
};

const REMARK_PLUGINS = [remarkGfm, remarkMath];
const REHYPE_PLUGINS = [rehypeKatex];

// Markdown renderer for assistant bubbles. Matches the Excavator frontend's
// setup (react-markdown + remark-gfm, no rehype-raw → embedded HTML is ignored,
// so model output cannot inject markup) and maps block elements onto Ant Design
// Typography for visual consistency with the rest of the dashboard.
//
// Streaming-safe: react-markdown renders whatever parses from a partial string,
// so mid-stream fragments degrade gracefully. memo'd on `children` so a widget
// re-render with unchanged text (e.g. a process-status poll) does no work.
export const Markdown = memo(function Markdown({ children }: { children: string }) {
  ensureMarkdownStyle();
  return (
    <div className="optio-cc-md">
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
        components={COMPONENTS}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
});
