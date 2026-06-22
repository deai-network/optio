import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Markdown } from '../Markdown.js';
import { FileDownloadContext } from '../FileDownloadContext.js';

describe('Markdown optio-file: sentinel rendering', () => {
  it('renders a clickable download control that calls the injected handler with (relpath, filename)', () => {
    const spy = vi.fn();
    render(
      <FileDownloadContext.Provider value={spy}>
        <Markdown>{'[r.md](optio-file:out/r.md)'}</Markdown>
      </FileDownloadContext.Provider>,
    );

    // The control surfaces the link text; it must not be a navigating anchor
    // (no href to the sentinel — clicking invokes the handler instead).
    const link = screen.getByText(/r\.md/);
    expect(link.getAttribute('href')).toBeNull();

    fireEvent.click(link);
    expect(spy).toHaveBeenCalledTimes(1);
    // relpath is the workdir-relative path; filename is its basename.
    expect(spy).toHaveBeenCalledWith('out/r.md', 'r.md');
  });

  it('degrades to plain text (no navigation) when no provider wraps the renderer', () => {
    const { container } = render(<Markdown>{'[r.md](optio-file:out/r.md)'}</Markdown>);

    // No handler → the sentinel link renders as plain text, never an anchor.
    expect(container.querySelector('a')).toBeNull();
    expect(container.querySelector('a[href^="optio-file:"]')).toBeNull();
    expect(screen.getByText(/r\.md/)).toBeTruthy();
  });

  it('still renders a normal external link as a navigating anchor', () => {
    const spy = vi.fn();
    const { container } = render(
      <FileDownloadContext.Provider value={spy}>
        <Markdown>{'[x](https://e.com)'}</Markdown>
      </FileDownloadContext.Provider>,
    );

    const anchor = container.querySelector('a');
    expect(anchor).not.toBeNull();
    expect(anchor!.getAttribute('href')).toBe('https://e.com');
    // A real link must never route through the download handler.
    fireEvent.click(anchor!);
    expect(spy).not.toHaveBeenCalled();
  });
});
