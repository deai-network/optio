import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { IframeInputWidget } from '../widgets/IframeInputWidget.js';

function makeProps(over: any = {}) {
  return {
    process: { _id: 'pid123', name: 'n', widgetData: { iframeSrc: '{widgetProxyUrl}/' }, status: { state: 'running' } },
    apiBaseUrl: '',
    widgetProxyUrl: '/api/widget/db/gm/pid123/',
    prefix: 'gm',
    database: 'db',
    ...over,
  };
}

describe('IframeInputWidget', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('renders the terminal iframe and the input box', () => {
    render(<IframeInputWidget {...makeProps()} />);
    expect(screen.getByTestId('optio-widget-iframe')).toBeTruthy();
    expect(screen.getByTestId('agent-input-box')).toBeTruthy();
  });

  it('Enter posts the text to the widget-control URL and clears on ok', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<IframeInputWidget {...makeProps()} />);
    const box = screen.getByTestId('agent-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'hello' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/widget-control/db/gm/pid123');
    expect(JSON.parse((init as any).body)).toEqual({ text: 'hello' });
    await waitFor(() => expect(box.value).toBe(''));
  });

  it('never disables the input while sending, so focus is not dropped (no mouse click needed)', async () => {
    // The browser blurs a focused element when it becomes `disabled`; that was
    // why Enter dropped focus. Gate the root cause: the textarea must stay
    // enabled (and focused) throughout the in-flight send. jsdom does not
    // emulate the disable→blur, so we assert on `disabled` + activeElement.
    let resolveFetch!: (r: Response) => void;
    const fetchMock = vi.fn(() => new Promise<Response>((r) => { resolveFetch = r; }));
    vi.stubGlobal('fetch', fetchMock);
    render(<IframeInputWidget {...makeProps()} />);
    const box = screen.getByTestId('agent-input-box') as HTMLTextAreaElement;
    box.focus();
    fireEvent.change(box, { target: { value: 'hello' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    // In flight: the textarea must NOT be disabled, and must keep focus.
    expect(box.disabled).toBe(false);
    expect(document.activeElement).toBe(box);
    resolveFetch(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    await waitFor(() => expect(box.value).toBe(''));
    expect(box.disabled).toBe(false);
    expect(document.activeElement).toBe(box);
  });

  it('Shift+Enter does not submit', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<IframeInputWidget {...makeProps()} />);
    const box = screen.getByTestId('agent-input-box');
    fireEvent.change(box, { target: { value: 'line1' } });
    fireEvent.keyDown(box, { key: 'Enter', shiftKey: true });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('keeps the text and shows an error when the session is not running', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ message: 'session not running' }), { status: 404 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<IframeInputWidget {...makeProps()} />);
    const box = screen.getByTestId('agent-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'hi' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(screen.getByTestId('agent-input-error')).toBeTruthy());
    expect(box.value).toBe('hi');
  });
});
