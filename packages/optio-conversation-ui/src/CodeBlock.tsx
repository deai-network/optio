import React, { useContext } from 'react';
import { Actions, CodeHighlighter } from '@ant-design/x';
import { ConfigProvider } from 'antd';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { useDarkTheme } from './themeMode.js';

// X's CodeHighlighter, following the antd theme. It paints Prism's oneLight
// in either theme (its own copy, with the <pre> margin removed); in dark mode
// we hand it oneDark the same way through highlightProps, so a code block is
// not a light box on a dark transcript.
const ONE_DARK = {
  ...oneDark,
  'pre[class*="language-"]': { ...oneDark['pre[class*="language-"]'], margin: 0 },
};

/** A highlighted code block. `title` replaces the language in X's header
 *  (e.g. "output" for a tool result); `maxHeight` makes the code scroll. */
export function CodeBlock({ lang, title, maxHeight, children }: {
  lang: string;
  title?: string;
  maxHeight?: number;
  children: string;
}) {
  const dark = useDarkTheme();
  const { getPrefixCls } = useContext(ConfigProvider.ConfigContext);
  const prefixCls = getPrefixCls('codeHighlighter');
  return (
    <CodeHighlighter
      lang={lang}
      highlightProps={dark ? { style: ONE_DARK } : undefined}
      // X's own header markup (its classes), with our title in place of the
      // language name.
      header={title === undefined ? undefined : (
        <div className={`${prefixCls}-header`}>
          <span className={`${prefixCls}-header-title`}>{title}</span>
          <Actions.Copy text={children} />
        </div>
      )}
      styles={maxHeight === undefined ? undefined : { code: { maxHeight, overflow: 'auto' } }}
    >
      {children}
    </CodeHighlighter>
  );
}
