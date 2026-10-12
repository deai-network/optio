import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { ConfigProvider, theme } from 'antd';
import { AnswerBlock } from '../AnswerBlock.js';

// Ant Design X experiment (owner request 2026-10-08): AnswerBlock renders with
// XMarkdown, wired the way X's templates do, so code, math and diagrams reach
// X's own components and our mermaid/katex libraries are gone.

describe('AnswerBlock (XMarkdown)', () => {
  it('renders markdown through XMarkdown', () => {
    const { container } = render(<AnswerBlock text={'Some **bold** text'} />);
    expect(container.querySelector('[class*="x-markdown"]')).not.toBeNull();
    expect(container.querySelector('strong')?.textContent).toBe('bold');
  });

  it("routes a fenced code block to X's CodeHighlighter", () => {
    const { container } = render(<AnswerBlock text={'```js\nconst a = 1;\n```'} />);
    expect(container.querySelector('[class*="ant-codeHighlighter"]')).not.toBeNull();
  });

  it('leaves inline code plain', () => {
    const { container } = render(<AnswerBlock text={'use `foo` here'} />);
    expect(container.querySelector('[class*="ant-codeHighlighter"]')).toBeNull();
    expect(container.querySelector('code')?.textContent).toBe('foo');
  });

  it("routes a mermaid fence to X's Mermaid", () => {
    const { container } = render(<AnswerBlock text={'```mermaid\ngraph TD\n  A --> B\n```'} />);
    expect(container.querySelector('[class*="ant-mermaid"]')).not.toBeNull();
  });

  it('renders inline LaTeX via KaTeX', () => {
    const { container } = render(<AnswerBlock text={'Euler: $e^{i\\pi} + 1 = 0$'} />);
    expect(container.querySelector('.katex')).not.toBeNull();
  });

  it("takes XMarkdown's theme stylesheet from the antd theme", () => {
    const light = render(<AnswerBlock text="x" />);
    expect(light.container.querySelector('.x-markdown-light')).not.toBeNull();
    const dark = render(
      <ConfigProvider theme={{ algorithm: theme.darkAlgorithm }}>
        <AnswerBlock text="x" />
      </ConfigProvider>,
    );
    expect(dark.container.querySelector('.x-markdown-dark')).not.toBeNull();
  });
});
