import { render, screen } from '@testing-library/react';
import { InternalLinkContext } from 'vultus-core';
import { Markdown } from '../Markdown.js';

test('renders GFM by default (block paragraph)', () => {
  render(<Markdown>{'hello **world**'}</Markdown>);
  expect(screen.getByText('world').tagName).toBe('STRONG');
  expect(screen.getByText(/hello/).closest('p')).not.toBeNull();
});

test('inline mode collapses paragraphs to <span>', () => {
  render(<Markdown inline>{'plain text'}</Markdown>);
  expect(screen.getByText('plain text').closest('p')).toBeNull();
});

test('internal link honors the injected component', () => {
  const Router = ({ href, children }: { href: string; children?: React.ReactNode }) => (
    <span data-testid="spa" data-href={href}>{children}</span>
  );
  render(
    <InternalLinkContext.Provider value={Router}>
      <Markdown>{'[go](/here)'}</Markdown>
    </InternalLinkContext.Provider>,
  );
  expect(screen.getByTestId('spa').getAttribute('data-href')).toBe('/here');
});

test('caller components override defaults', () => {
  render(
    <Markdown components={{ a: ({ href, children }) => <b data-testid="b" data-href={href}>{children}</b> }}>
      {'[x](/y)'}
    </Markdown>,
  );
  expect(screen.getByTestId('b').getAttribute('data-href')).toBe('/y');
});

test('extra remark plugins are appended after gfm', () => {
  const upper = () => (tree: any) => {
    const walk = (n: any) => { if (n.type === 'text') n.value = n.value.toUpperCase(); (n.children || []).forEach(walk); };
    walk(tree);
  };
  render(<Markdown remarkPlugins={[upper]}>{'quiet'}</Markdown>);
  expect(screen.getByText('QUIET')).toBeInTheDocument();
});
