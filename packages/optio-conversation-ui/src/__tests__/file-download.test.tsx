import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { AnswerBlock } from '../AnswerBlock.js';
import { FileDownloadContext } from '../FileDownloadContext.js';
import { ConversationView, type ConversationViewProps } from '../ConversationView.js';

describe('Markdown optio-file: sentinel rendering', () => {
  it('renders the file name as a link that calls the injected handler with (relpath, filename)', () => {
    const spy = vi.fn();
    const { container } = render(
      <FileDownloadContext.Provider value={spy}>
        <AnswerBlock text={'Here is [the report](optio-file:out/r.md).'} />
      </FileDownloadContext.Provider>,
    );

    // Just the file name (owner, 2026-10-12): no download glyph, and not a
    // navigating anchor (no href to the sentinel; clicking calls the handler).
    const link = container.querySelector('a.optio-file-link');
    expect(link).not.toBeNull();
    expect(link!.textContent).toBe('r.md');
    expect(link!.getAttribute('href')).toBeNull();
    expect(container.textContent).not.toContain('⬇');

    fireEvent.click(link!);
    expect(spy).toHaveBeenCalledTimes(1);
    // relpath is the workdir-relative path; filename is its basename.
    expect(spy).toHaveBeenCalledWith('out/r.md', 'r.md');
  });

  it('degrades to plain text (no navigation) when no provider wraps the renderer', () => {
    const { container } = render(<AnswerBlock text={'[r.md](optio-file:out/r.md)'} />);

    // No handler → the sentinel link renders as plain text, never an anchor.
    expect(container.querySelector('a')).toBeNull();
    expect(container.querySelector('a[href^="optio-file:"]')).toBeNull();
    expect(screen.getByText(/r\.md/)).toBeTruthy();
  });

  it('still renders a normal external link as a navigating anchor', () => {
    const spy = vi.fn();
    const { container } = render(
      <FileDownloadContext.Provider value={spy}>
        <AnswerBlock text={'[x](https://e.com)'} />
      </FileDownloadContext.Provider>,
    );

    const anchor = container.querySelector('a');
    expect(anchor).not.toBeNull();
    expect(anchor!.getAttribute('href')).toBe('https://e.com');
    expect(anchor!.classList.contains('optio-file-link')).toBe(false);
    // A real link must never route through the download handler.
    fireEvent.click(anchor!);
    expect(spy).not.toHaveBeenCalled();
  });
});

// Ant Design X experiment (owner-approved 2026-10-12): the answer's files are
// listed as an X FileCard.List under the reply, inside its bubble; each card
// is named after the file (X picks the icon from its extension) and described
// by the link text when that differs, else by its folder.
describe('FileCards below the reply', () => {
  function view(text: string, onFileDownload: ConversationViewProps['onFileDownload'] = vi.fn()) {
    const props: ConversationViewProps = {
      state: { items: [{ kind: 'assistant', text, pending: false, seq: 1, msgId: 'm1' }], busy: false, closed: false, controls: [] },
      closed: false, busy: false, toolVerbosity: 'verbose', thinkingVerbosity: 'visible', showFileUpload: false,
      maxUploadBytes: 10_000_000, fileDownload: true,
      onSend: vi.fn(async () => true), onInterrupt: vi.fn(), onPermission: vi.fn(), onFileDownload,
    };
    return render(<ConversationView {...props} />);
  }
  const names = (root: ParentNode) => [...root.querySelectorAll('.ant-file-card-file-name')].map((n) => n.textContent);
  const cardFile = (container: HTMLElement, i = 0) =>
    container.querySelectorAll('.optio-cc-answer .ant-file-card-list .ant-file-card-file')[i] as HTMLElement;

  it("lists the answer's files once each, in order, under the reply; the text keeps file-name links", () => {
    const { container } = view('See [r.md](optio-file:out/r.md), [data](optio-file:d.csv) and [r.md](optio-file:out/r.md) again.');
    const list = container.querySelector('.optio-cc-answer .ant-file-card-list');
    expect(list).not.toBeNull();
    expect(names(list!)).toEqual(['r.md', 'd.csv']);
    // No card in the text itself: only the list's two.
    expect(names(container)).toEqual(['r.md', 'd.csv']);
    expect([...container.querySelectorAll('a.optio-file-link')].map((a) => a.textContent)).toEqual(['r.md', 'd.csv', 'r.md']);
  });

  it.each([
    ['[the report](optio-file:out/r.md)', 'the report'],
    ['[r.md](optio-file:out/r.md)', 'out/'],
    ['[r.md](optio-file:r.md)', ''],
  ])('%s is described as "%s"', (text, description) => {
    const { container } = view(text);
    expect(cardFile(container).querySelector('.ant-file-card-file-description')?.textContent ?? '').toBe(description);
  });

  it('clicking a listed card calls the handler with (relpath, filename)', () => {
    const spy = vi.fn();
    const { container } = view('[data](optio-file:sub/d.csv)', spy);
    fireEvent.click(cardFile(container));
    expect(spy).toHaveBeenCalledWith('sub/d.csv', 'd.csv');
  });

  it('while the download runs the card shows a spinner, and another click starts nothing', async () => {
    let finish: () => void = () => {};
    const spy = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    const { container } = view('[r.md](optio-file:out/r.md)', spy);
    expect(cardFile(container).querySelector('.ant-spin')).toBeNull();
    fireEvent.click(cardFile(container));
    expect(cardFile(container).querySelector('.ant-file-card-file-icon .ant-spin')).not.toBeNull();
    fireEvent.click(cardFile(container));
    expect(spy).toHaveBeenCalledTimes(1);
    await act(async () => { finish(); });
    expect(cardFile(container).querySelector('.ant-spin')).toBeNull();
  });

  it('an answer without file links has no list', () => {
    const { container } = view('Nothing to download.');
    expect(container.querySelector('.ant-file-card-list')).toBeNull();
  });

  it("the download links opt out of X-markdown's external-link marker, other links keep it", () => {
    view('[r.md](optio-file:out/r.md)');
    const css = [...document.querySelectorAll('style')].map((s) => s.textContent ?? '').join('\n');
    expect(css).toMatch(/\.x-markdown-light a\.optio-file-link::after[^{]*\{\s*content:\s*none/);
    expect(css).toContain('.x-markdown-dark a.optio-file-link::after');
  });
});
