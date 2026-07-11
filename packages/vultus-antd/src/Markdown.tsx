import { memo, useMemo } from 'react';
import ReactMarkdown, { type Components, type Options, defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useInternalLink } from 'vultus-core/link';

type PluginList = Options['remarkPlugins'];

export interface MarkdownProps {
  children: string;
  /** Collapse block paragraphs to inline <span> (tooltips/labels). */
  inline?: boolean;
  /** Remark plugins appended AFTER remark-gfm. */
  remarkPlugins?: PluginList;
  rehypePlugins?: Options['rehypePlugins'];
  /** Component overrides merged over (and able to replace) the defaults. */
  components?: Components;
  /** URL sanitizer; defaults to react-markdown's defaultUrlTransform. */
  urlTransform?: (url: string) => string;
}

/**
 * Configurable markdown renderer shared across vultus consumers: common
 * react-markdown wiring (GFM, URL sanitization, injectable internal links,
 * optional inline mode) plus caller-supplied plugins/component overrides
 * (e.g. conversation-ui's KaTeX/Mermaid). Streaming-safe.
 */
export const Markdown = memo(function Markdown({
  children, inline, remarkPlugins, rehypePlugins, components,
  urlTransform = defaultUrlTransform,
}: MarkdownProps) {
  const InternalLink = useInternalLink();
  const mergedComponents = useMemo<Components>(() => ({
    a: ({ href, children }) =>
      href && href.startsWith('/')
        ? <InternalLink href={href}>{children}</InternalLink>
        : <a href={href} target="_blank" rel="noreferrer">{children}</a>,
    ...(inline ? { p: ({ children }) => <span>{children}</span> } : {}),
    ...components,
  }), [InternalLink, inline, components]);
  const remark = useMemo<PluginList>(
    () => [remarkGfm, ...(remarkPlugins ?? [])],
    [remarkPlugins],
  );
  return (
    <ReactMarkdown remarkPlugins={remark} rehypePlugins={rehypePlugins}
      urlTransform={urlTransform} components={mergedComponents}>
      {children}
    </ReactMarkdown>
  );
});

export { type Components, defaultUrlTransform };
