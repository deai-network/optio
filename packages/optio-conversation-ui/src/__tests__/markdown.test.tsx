import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { Markdown } from '../Markdown.js';

describe('Markdown code rendering', () => {
  it('renders a fenced block with a single block-level background, not per-line', () => {
    const src = '```js\nconst a = 1;\nconst b = 2;\nconst c = 3;\n```';
    const { container } = render(<Markdown>{src}</Markdown>);

    const pre = container.querySelector('pre');
    expect(pre).not.toBeNull();
    // The whole block owns the background — the <pre> must carry it as an inline
    // (themed) style. The per-line bug is the absence of this: when the inner
    // <code> reused the inline Typography style instead, each wrapped line got
    // its own box and the <pre> had no background of its own.
    const bg = (pre as HTMLElement).style.background || (pre as HTMLElement).style.backgroundColor;
    expect(bg).toBeTruthy();

    // The fenced <code> must NOT be wrapped in antd's inline-code Typography
    // span — that span.ant-typography is what painted a box per wrapped line.
    const code = pre!.querySelector('code');
    expect(code).not.toBeNull();
    expect(code!.closest('span.ant-typography')).toBeNull();
  });

  it('keeps true inline code on antd Typography', () => {
    const { container } = render(<Markdown>{'use `foo` here'}</Markdown>);
    // No fenced block → no <pre>; the inline code keeps the Typography wrapper.
    expect(container.querySelector('pre')).toBeNull();
    const code = container.querySelector('code');
    expect(code).not.toBeNull();
    expect(code!.closest('span.ant-typography')).not.toBeNull();
  });
});

describe('Markdown math rendering', () => {
  it('renders inline LaTeX via KaTeX', () => {
    const { container } = render(<Markdown>{'energy is $E = mc^2$ today'}</Markdown>);
    // remark-math + rehype-katex turn $...$ into KaTeX markup (.katex root).
    expect(container.querySelector('.katex')).not.toBeNull();
  });

  it('renders a display-math block via KaTeX', () => {
    const { container } = render(<Markdown>{'$$\n\\int_0^1 x\\,dx = \\tfrac12\n$$'}</Markdown>);
    expect(container.querySelector('.katex-display')).not.toBeNull();
  });
});
