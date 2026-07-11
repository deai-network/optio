import { render, screen } from '@testing-library/react';
import { InternalLinkContext, useInternalLink } from '../LinkContext.js';

function Probe() {
  const Link = useInternalLink();
  return <Link href="/somewhere">go</Link>;
}

test('default internal link is a plain anchor', () => {
  render(<Probe />);
  const a = screen.getByText('go');
  expect(a.tagName).toBe('A');
  expect(a.getAttribute('href')).toBe('/somewhere');
});

test('provider overrides the internal link component', () => {
  const Custom = ({ href, children }: { href: string; children?: React.ReactNode }) => (
    <span data-testid="custom" data-href={href}>{children}</span>
  );
  render(
    <InternalLinkContext.Provider value={Custom}>
      <Probe />
    </InternalLinkContext.Provider>,
  );
  expect(screen.getByTestId('custom').getAttribute('data-href')).toBe('/somewhere');
});
