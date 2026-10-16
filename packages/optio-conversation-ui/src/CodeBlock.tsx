import { memo, useContext } from 'react';
import { Actions } from '@ant-design/x';
import { ConfigProvider, theme } from 'antd';
import { PrismLight } from 'react-syntax-highlighter';
import { oneDark, oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { prismLanguage } from './prismLanguages.js';
import { useDarkTheme } from './themeMode.js';

// A code block in X CodeHighlighter's chrome: its header (the language, or our
// title, and its copy action) over the code, its classes and its look (X's
// style rules, below, from the antd theme), and Prism's oneLight, or oneDark in
// dark mode, without the <pre> margin. Highlighted by react-syntax-highlighter's
// light Prism build, which X's CodeHighlighter wraps too, rendered directly:
// X's own loads each language lazily and suspends meanwhile, and its import
// does not resolve in a Vite bundle. Our languages are registered up front
// (prismLanguages.ts); any other is plain text.
const withoutPreMargin = (style: typeof oneLight) => ({
  ...style,
  'pre[class*="language-"]': { ...style['pre[class*="language-"]'], margin: 0 },
});
const ONE_LIGHT = withoutPreMargin(oneLight);
const ONE_DARK = withoutPreMargin(oneDark);

/** A highlighted code block. `title` replaces the language in the header
 *  (e.g. "output" for a tool result); `maxHeight` makes the code scroll. */
function CodeBlockImpl({ lang, title, maxHeight, children }: {
  lang: string;
  title?: string;
  maxHeight?: number;
  children: string;
}) {
  const dark = useDarkTheme();
  const { token } = theme.useToken();
  const { getPrefixCls, direction } = useContext(ConfigProvider.ConfigContext);
  const prefixCls = getPrefixCls('codeHighlighter');
  if (!children) return null;
  if (!lang) return <code>{children}</code>;
  // X CodeHighlighter's style rules (its genCodeHighlighterStyle), inline.
  const font = { fontSize: token.fontSize, fontFamily: token.fontFamilyCode, lineHeight: 2, borderRadius: 0, border: 'none' };
  return (
    <div
      className={prefixCls}
      style={direction === 'rtl' ? { direction: 'rtl' } : undefined}
    >
      <div
        className={`${prefixCls}-header`}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          color: token.colorText,
          background: token.colorFillContent,
          padding: token.paddingSM,
          borderStartStartRadius: token.borderRadius,
          borderStartEndRadius: token.borderRadius,
        }}
      >
        <span className={`${prefixCls}-header-title`} style={{ fontSize: token.fontSize, fontWeight: token.fontWeightStrong }}>
          {title ?? lang}
        </span>
        <Actions.Copy text={children} />
      </div>
      <div
        className={`${prefixCls}-code`}
        style={{
          borderEndEndRadius: token.borderRadius,
          borderEndStartRadius: token.borderRadius,
          borderStartStartRadius: 0,
          borderStartEndRadius: 0,
          background: token.colorBgContainer,
          border: `1px solid ${token.colorBorderSecondary}`,
          borderTop: 'none',
          overflow: 'hidden',
          ...(maxHeight === undefined ? null : { maxHeight, overflow: 'auto' }),
        }}
      >
        <PrismLight
          language={prismLanguage(lang)}
          wrapLines
          style={dark ? ONE_DARK : ONE_LIGHT}
          customStyle={{ fontSize: token.fontSize, border: 'none', minWidth: '100%' }}
          codeTagProps={{ style: { ...font, background: 'transparent' } }}
        >
          {children.replace(/\n$/, '')}
        </PrismLight>
      </div>
    </div>
  );
}

// Memoized on its (plain) props: an unchanged block is never re-highlighted.
export const CodeBlock = memo(CodeBlockImpl);
