import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { ConfigProvider, theme } from 'antd';
import { oneDark, oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { CodeBlock } from '../CodeBlock.js';

// A code block highlights on its first render: the common Prism languages are
// registered up front, so nothing is loaded lazily (X's CodeHighlighter
// imported each language on first use, which a Vite bundle cannot resolve, and
// suspended meanwhile). A language that is not registered renders as plain
// text in the same chrome.

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

function block(lang: string, code: string, title?: string, dark = false) {
  const { container } = render(
    <ConfigProvider theme={dark ? { algorithm: theme.darkAlgorithm } : undefined}>
      <CodeBlock lang={lang} title={title}>{code}</CodeBlock>
    </ConfigProvider>,
  );
  const root = container.querySelector('.ant-codeHighlighter');
  return {
    root,
    title: root?.querySelector('.ant-codeHighlighter-header-title')?.textContent,
    pre: root?.querySelector('.ant-codeHighlighter-code pre') ?? null,
    code: root?.querySelector('.ant-codeHighlighter-code')?.textContent,
    // Prism's tokens (the plain-text path has none); their colours are inline.
    tokens: Array.from(root?.querySelectorAll('.token') ?? []).map((t) => t.textContent),
  };
}

describe('CodeBlock highlighting', () => {
  it('highlights ts on the first render (an alias of typescript)', () => {
    const b = block('ts', 'const a: number = 1;\n');
    expect(b.pre).not.toBeNull();
    expect(b.tokens).toContain('const');
    expect(b.title).toBe('ts');
    expect(b.code).toBe('const a: number = 1;');
  });

  it('highlights python on the first render', () => {
    const b = block('python', 'def f(x):\n    return x\n');
    expect(b.pre).not.toBeNull();
    expect(b.tokens).toContain('def');
  });

  it.each([
    ['typescript', 'let x = 1', 'let'],
    ['tsx', 'const a = <b>x</b>', 'const'],
    ['javascript', 'let x = 1', 'let'],
    ['js', 'let x = 1', 'let'],
    ['jsx', 'const a = <b>x</b>', 'const'],
    ['py', 'def f(): pass', 'def'],
    ['bash', 'if true; then echo; fi', 'if'],
    ['sh', 'if true; then echo; fi', 'if'],
    ['shell', 'if true; then echo; fi', 'if'],
    ['zsh', 'if true; then echo; fi', 'if'],
    ['console', '$ ls -la', '$'],
    ['json', '{"a": 1}', '"a"'],
    ['sql', 'SELECT 1 FROM t', 'SELECT'],
    ['yaml', 'a: 1', 'a'],
    ['yml', 'a: 1', 'a'],
    ['diff', '+ added', '+'],
    ['markdown', '# Title', '#'],
    ['md', '# Title', '#'],
    ['css', 'a { color: red; }', 'color'],
    ['html', '<p>x</p>', '<'],
    ['xml', '<p>x</p>', '<'],
    ['svg', '<p>x</p>', '<'],
    ['go', 'func main() {}', 'func'],
    ['rust', 'fn main() {}', 'fn'],
    ['java', 'public class A {}', 'public'],
    ['toml', 'a = 1', 'a'],
    ['log', 'ERROR something failed', 'ERROR'],
  ])('highlights %s on the first render', (lang, code, word) => {
    const b = block(lang, code);
    expect(b.pre).not.toBeNull();
    expect(b.tokens).toContain(word);
  });

  it('renders an unknown language as plain text, without loading anything', () => {
    const warn = vi.spyOn(console, 'warn');
    const b = block('no-such-language', 'just <some> text\n');
    expect(b.pre).not.toBeNull();
    expect(b.tokens).toEqual([]);
    expect(b.code).toBe('just <some> text');
    expect(b.title).toBe('no-such-language');
    expect(warn).not.toHaveBeenCalled();
  });

  it("paints Prism's oneDark in a dark theme, oneLight otherwise, without the <pre> margin", () => {
    const bg = (style: typeof oneLight) => {
      const probe = document.createElement('div');
      probe.style.background = String(style['pre[class*="language-"]'].background);
      return probe.style.background;
    };
    const light = block('ts', 'const a = 1');
    const dark = block('ts', 'const a = 1', undefined, true);
    expect((light.pre as HTMLElement).style.background).toBe(bg(oneLight));
    expect((dark.pre as HTMLElement).style.background).toBe(bg(oneDark));
    expect((dark.pre as HTMLElement).style.margin).toBe('0px');
  });

  it('shows the title in place of the language, with a copy action', () => {
    const b = block('json', '{"a": 1}', 'output');
    expect(b.title).toBe('output');
    expect(b.root?.querySelector('.ant-codeHighlighter-header [class*="ant-actions-copy"]')).not.toBeNull();
  });
});
