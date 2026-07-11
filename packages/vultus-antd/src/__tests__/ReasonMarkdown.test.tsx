import { render, screen } from '@testing-library/react';
import { InternalLinkContext } from 'vultus-core';
import { ReasonMarkdown } from '../ReasonMarkdown.js';

test('internal link uses the injected component; external stays an anchor', () => {
  const Router = ({ href, children }: { href: string; children?: React.ReactNode }) => (
    <span data-testid="spa" data-href={href}>{children}</span>
  );
  render(
    <InternalLinkContext.Provider value={Router}>
      <ReasonMarkdown>{'[in](/x) and [out](https://e.com)'}</ReasonMarkdown>
    </InternalLinkContext.Provider>,
  );
  expect(screen.getByTestId('spa').getAttribute('data-href')).toBe('/x');
  const ext = screen.getByText('out');
  expect(ext.tagName).toBe('A');
  expect(ext.getAttribute('target')).toBe('_blank');
});
