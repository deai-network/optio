import { useContext } from 'react';
import { Typography, theme } from 'antd';
import { Mermaid } from '@ant-design/x';
import XMarkdown, { type ComponentProps } from '@ant-design/x-markdown';
import Latex from '@ant-design/x-markdown/plugins/Latex';
import '@ant-design/x-markdown/themes/light.css';
import '@ant-design/x-markdown/themes/dark.css';
import { CodeBlock } from './CodeBlock.js';
import { FileDownloadContext } from './FileDownloadContext.js';

// The one public seam for rendering an assistant answer (also the compaction
// summary and task descriptions): pass the markdown text, get it rendered.
//
// Ant Design X experiment (owner request 2026-10-08): rendered by XMarkdown,
// X's streaming markdown engine, wired the way X's own templates and demos
// do: LaTeX through its Latex plugin, code blocks through X's
// CodeHighlighter, mermaid fences through X's Mermaid, and its light/dark
// theme stylesheets picked from the antd theme. Mermaid, KaTeX and the code
// highlighter come with @ant-design/x and @ant-design/x-markdown.
//
// ── Consumer requirements ──
// This package ships TypeScript source, so the consumer's bundler compiles
// this file: it must handle `.css` side-effect imports (the x-markdown themes,
// and the KaTeX stylesheet the Latex plugin imports) and serve the KaTeX
// fonts that stylesheet references. Mermaid needs a DOM (client-side only).

const CONFIG = { extensions: Latex() };

// DOMPurify's default URI allow-list plus our optio-file: download sentinel,
// so XMarkdown's sanitizer keeps those links' href.
const PURIFY = {
  ALLOWED_URI_REGEXP:
    /^(?:(?:(?:f|ht)tps?|mailto|tel|callto|sms|cid|xmpp|matrix|optio-file):|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i,
};

function Code({ className, children, block, lang }: ComponentProps) {
  const language = lang ?? className?.match(/language-(\w+)/)?.[1] ?? '';
  if (!block || typeof children !== 'string') return <code className={className}>{children}</code>;
  if (language === 'mermaid') return <Mermaid>{children}</Mermaid>;
  if (!language) return <code className={className}>{children}</code>;
  return <CodeBlock lang={language}>{children}</CodeBlock>;
}

// An agent's `[name](optio-file:relpath)` downloads that workdir file through
// the host's FileDownloadContext handler (plain text when there is none);
// any other link opens in a new tab.
function Link({ href, children }: ComponentProps<{ href?: string }>) {
  const onDownload = useContext(FileDownloadContext);
  if (typeof href === 'string' && href.startsWith('optio-file:')) {
    const relpath = href.slice('optio-file:'.length);
    const filename = relpath.split('/').pop() || relpath;
    return onDownload ? (
      <Typography.Link onClick={() => onDownload(relpath, filename)} style={{ cursor: 'pointer' }}>
        ⬇ {children}
      </Typography.Link>
    ) : (
      <Typography.Text>{children}</Typography.Text>
    );
  }
  return (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  );
}

const COMPONENTS = { code: Code, a: Link };

/** `pending`: the text is still streaming in (XMarkdown animates it and shows
 *  its tail cursor). */
export function AnswerBlock({ text, pending = false }: { text: string; pending?: boolean }) {
  const { theme: antdTheme } = theme.useToken();
  return (
    <XMarkdown
      className={antdTheme.id === 0 ? 'x-markdown-light' : 'x-markdown-dark'}
      paragraphTag="div"
      content={text}
      config={CONFIG}
      components={COMPONENTS}
      dompurifyConfig={PURIFY}
      streaming={{ hasNextChunk: pending, enableAnimation: true, tail: true }}
    />
  );
}
