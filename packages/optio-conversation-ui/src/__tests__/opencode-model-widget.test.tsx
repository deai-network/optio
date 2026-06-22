import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { OpencodeView } from '../opencode/OpencodeView.js';

class MockEventSource {
  static last: MockEventSource | null = null;
  url: string; onmessage: ((e: MessageEvent) => void) | null = null;
  constructor(url: string) { this.url = url; MockEventSource.last = this; }
  close() {}
}

const PROVIDERS = {
  providers: [{
    id: 'opencode', name: 'OpenCode Zen',
    models: {
      'deepseek-v4-flash': { id: 'deepseek-v4-flash', providerID: 'opencode', name: 'DeepSeek V4 Flash' },
      'big-pickle': { id: 'big-pickle', providerID: 'opencode', name: 'Big Pickle' },
    },
  }],
  default: { opencode: 'big-pickle' },
};

// fetch router: history (empty unless overridden), providers, POST capture.
function installFetch(opts: { history?: any[]; posts: { url: string; body: any }[] }) {
  const fn = vi.fn(async (url: string, init?: any) => {
    if (init?.method === 'POST') {
      opts.posts.push({ url, body: JSON.parse(init.body) });
      return { ok: true, json: async () => ({}) } as any;
    }
    if (url.includes('/config/providers')) return { ok: true, json: async () => PROVIDERS } as any;
    if (url.includes('/message')) return { ok: true, json: async () => (opts.history ?? []) } as any;
    return { ok: true, json: async () => ({}) } as any;
  });
  (globalThis as any).fetch = fn;
  return fn;
}

function makeProps(widgetData: any) {
  return {
    process: { _id: 'p1', name: 'n', widgetData, status: { state: 'running' } },
    widgetProxyUrl: '/api/widget/db/gm/p1/',
  } as any;
}

beforeEach(() => {
  (globalThis as any).EventSource = MockEventSource as any;
  MockEventSource.last = null;
});

describe('OpencodeView model send', () => {
  it('sends the providers-default model on prompt_async when history is empty', async () => {
    const posts: { url: string; body: any }[] = [];
    installFetch({ history: [], posts });
    render(<OpencodeView {...makeProps({ sessionID: 'fake-session-id', directory: '/wd' })} />);

    // Wait for bootstrap (providers + history fetched, currentModel resolved).
    await waitFor(() => expect(screen.getByTestId('conversation-input-box')).toBeTruthy());
    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'hi' } });
    fireEvent.click(screen.getByTestId('conversation-send'));

    await waitFor(() => expect(posts.some((p) => p.url.includes('/prompt_async'))).toBe(true));
    const sent = posts.find((p) => p.url.includes('/prompt_async'))!;
    expect(sent.body.model).toEqual({ providerID: 'opencode', modelID: 'big-pickle' });
    expect(sent.body.parts).toEqual([{ type: 'text', text: 'hi' }]);
  });

  it('prefers the last-assistant model from history over the providers default', async () => {
    const posts: { url: string; body: any }[] = [];
    installFetch({
      history: [{ info: { role: 'assistant', providerID: 'opencode', modelID: 'deepseek-v4-flash' }, parts: [] }],
      posts,
    });
    render(<OpencodeView {...makeProps({ sessionID: 'fake-session-id', directory: '/wd' })} />);
    await waitFor(() => expect(screen.getByTestId('conversation-input-box')).toBeTruthy());
    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'hi' } });
    fireEvent.click(screen.getByTestId('conversation-send'));
    await waitFor(() => expect(posts.some((p) => p.url.includes('/prompt_async'))).toBe(true));
    expect(posts.find((p) => p.url.includes('/prompt_async'))!.body.model)
      .toEqual({ providerID: 'opencode', modelID: 'deepseek-v4-flash' });
  });

  it('uses defaultModel (validated) on a fresh session even with the picker hidden', async () => {
    const posts: { url: string; body: any }[] = [];
    installFetch({ history: [], posts });
    render(<OpencodeView {...makeProps({
      sessionID: 'fake-session-id', directory: '/wd',
      defaultModel: 'opencode/deepseek-v4-flash', // valid, not the providers default
    })} />);
    await waitFor(() => expect(screen.getByTestId('conversation-input-box')).toBeTruthy());
    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'hi' } });
    fireEvent.click(screen.getByTestId('conversation-send'));
    await waitFor(() => expect(posts.some((p) => p.url.includes('/prompt_async'))).toBe(true));
    expect(posts.find((p) => p.url.includes('/prompt_async'))!.body.model)
      .toEqual({ providerID: 'opencode', modelID: 'deepseek-v4-flash' });
  });
});

describe('OpencodeView model picker', () => {
  it('is hidden when showModelSelector is false', async () => {
    installFetch({ history: [], posts: [] });
    render(<OpencodeView {...makeProps({ sessionID: 'fake-session-id', directory: '/wd' })} />);
    await waitFor(() => expect(screen.getByTestId('conversation-input-box')).toBeTruthy());
    expect(screen.queryByTestId('model-select')).toBeNull();
  });

  it('is shown when showModelSelector is true', async () => {
    installFetch({ history: [], posts: [] });
    render(<OpencodeView {...makeProps({ sessionID: 'fake-session-id', directory: '/wd', showModelSelector: true })} />);
    await waitFor(() => expect(screen.getByTestId('model-select')).toBeTruthy());
  });

  it('selecting a model changes the model sent on the next prompt', async () => {
    const posts: { url: string; body: any }[] = [];
    installFetch({ history: [], posts });
    render(<OpencodeView {...makeProps({ sessionID: 'fake-session-id', directory: '/wd', showModelSelector: true })} />);
    await waitFor(() => expect(screen.getByTestId('model-select')).toBeTruthy());

    // antd Select renders a hidden native <select> in test env when we pass a
    // plain options model; drive it via the combobox role. Open + pick the
    // option labelled "DeepSeek V4 Flash" (value "opencode/deepseek-v4-flash").
    fireEvent.mouseDown(screen.getByTestId('model-select').querySelector('.ant-select-selector')!);
    await waitFor(() => expect(screen.getByText('DeepSeek V4 Flash')).toBeTruthy());
    fireEvent.click(screen.getByText('DeepSeek V4 Flash'));

    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'hi' } });
    fireEvent.click(screen.getByTestId('conversation-send'));
    await waitFor(() => expect(posts.some((p) => p.url.includes('/prompt_async'))).toBe(true));
    expect(posts.find((p) => p.url.includes('/prompt_async'))!.body.model)
      .toEqual({ providerID: 'opencode', modelID: 'deepseek-v4-flash' });
  });
});
