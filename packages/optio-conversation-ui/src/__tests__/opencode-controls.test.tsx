import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { OpencodeView } from '../opencode/OpencodeView.js';

// opencode's model picker is UI-local: no /control listener, no POST. The model
// SessionControl is built from GET /config/providers and applied inline on the
// next prompt_async. These tests mirror the former model-widget test, retargeted
// to the generic `control-model` rendered by the shared ConversationView.

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

  it('uses defaultModel (validated) on a fresh session even with the control hidden', async () => {
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

describe('OpencodeView model control', () => {
  it('is hidden when showSessionControls is false', async () => {
    installFetch({ history: [], posts: [] });
    render(<OpencodeView {...makeProps({ sessionID: 'fake-session-id', directory: '/wd' })} />);
    await waitFor(() => expect(screen.getByTestId('conversation-input-box')).toBeTruthy());
    expect(screen.queryByTestId('control-model')).toBeNull();
  });

  it('is shown when showSessionControls is true', async () => {
    installFetch({ history: [], posts: [] });
    render(<OpencodeView {...makeProps({ sessionID: 'fake-session-id', directory: '/wd', showSessionControls: true })} />);
    await waitFor(() => expect(screen.getByTestId('control-model')).toBeTruthy());
  });

  it('disables models named in widgetData.disabledModels with the reason as tooltip', async () => {
    installFetch({ history: [], posts: [] });
    const reason = 'Not usable with this account (the provider rejected it)';
    // Disable the NON-default model (big-pickle is the providers default and
    // would render its label twice — in the selector box and the dropdown).
    render(<OpencodeView {...makeProps({
      sessionID: 'fake-session-id', directory: '/wd', showSessionControls: true,
      disabledModels: { 'opencode/deepseek-v4-flash': reason },
    })} />);
    await waitFor(() => expect(screen.getByTestId('control-model')).toBeTruthy());
    fireEvent.mouseDown(screen.getByTestId('control-model').querySelector('.ant-select-selector')!);
    await waitFor(() => expect(screen.getByText('DeepSeek V4 Flash')).toBeTruthy());

    const options = Array.from(document.querySelectorAll('.ant-select-item-option'));
    const bad = options.find((o) => o.textContent === 'DeepSeek V4 Flash');
    const good = options.find((o) => o.textContent === 'Big Pickle');
    // The probed-unusable model is greyed and carries the reason as tooltip.
    expect(bad?.classList.contains('ant-select-item-option-disabled')).toBe(true);
    expect(bad?.getAttribute('title')).toBe(reason);
    // The working model stays selectable.
    expect(good?.classList.contains('ant-select-item-option-disabled')).toBe(false);
  });

  it('selecting a model changes the model sent on the next prompt (UI-local, no POST)', async () => {
    const posts: { url: string; body: any }[] = [];
    installFetch({ history: [], posts });
    render(<OpencodeView {...makeProps({ sessionID: 'fake-session-id', directory: '/wd', showSessionControls: true })} />);
    await waitFor(() => expect(screen.getByTestId('control-model')).toBeTruthy());

    // Open the generic select + pick the option labelled "DeepSeek V4 Flash"
    // (value "opencode/deepseek-v4-flash"). Single provider → label unprefixed.
    fireEvent.mouseDown(screen.getByTestId('control-model').querySelector('.ant-select-selector')!);
    await waitFor(() => expect(screen.getByText('DeepSeek V4 Flash')).toBeTruthy());
    fireEvent.click(screen.getByText('DeepSeek V4 Flash'));

    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'hi' } });
    fireEvent.click(screen.getByTestId('conversation-send'));
    await waitFor(() => expect(posts.some((p) => p.url.includes('/prompt_async'))).toBe(true));

    // No /control POST is issued — the change is UI-local, carried on the prompt.
    expect(posts.some((p) => p.url.includes('/control'))).toBe(false);
    expect(posts.find((p) => p.url.includes('/prompt_async'))!.body.model)
      .toEqual({ providerID: 'opencode', modelID: 'deepseek-v4-flash' });
  });
});

// opencode grades reasoning effort per-prompt via a model's named `variant`,
// attached to prompt_async beside `model` (client-side). The effort slider is
// built from the CURRENT model's variant keys (widgetData.modelVariants) and
// only appears when the model has variants — re-derived on model change.
describe('OpencodeView effort control', () => {
  // big-pickle (the providers default) has variants; deepseek-v4-flash does not.
  const VARIANTS = { 'opencode/big-pickle': ['low', 'medium', 'high'] };

  it('renders the effort slider for a variant-capable current model', async () => {
    installFetch({ history: [], posts: [] });
    render(<OpencodeView {...makeProps({
      sessionID: 'fake-session-id', directory: '/wd', showSessionControls: true,
      modelVariants: VARIANTS,
    })} />);
    await waitFor(() => expect(screen.getByTestId('control-reasoning_effort')).toBeTruthy());
  });

  it('omits the effort slider when the current model has no variants', async () => {
    // history pins deepseek-v4-flash (no variants) as the resolved model.
    installFetch({
      history: [{ info: { role: 'assistant', providerID: 'opencode', modelID: 'deepseek-v4-flash' }, parts: [] }],
      posts: [],
    });
    render(<OpencodeView {...makeProps({
      sessionID: 'fake-session-id', directory: '/wd', showSessionControls: true,
      modelVariants: VARIANTS,
    })} />);
    await waitFor(() => expect(screen.getByTestId('control-model')).toBeTruthy());
    expect(screen.queryByTestId('control-reasoning_effort')).toBeNull();
  });

  it('is hidden when showSessionControls is false even with variants', async () => {
    installFetch({ history: [], posts: [] });
    render(<OpencodeView {...makeProps({
      sessionID: 'fake-session-id', directory: '/wd', modelVariants: VARIANTS,
    })} />);
    await waitFor(() => expect(screen.getByTestId('conversation-input-box')).toBeTruthy());
    expect(screen.queryByTestId('control-reasoning_effort')).toBeNull();
  });

  it('attaches defaultEffort as the variant on the next prompt_async', async () => {
    const posts: { url: string; body: any }[] = [];
    installFetch({ history: [], posts });
    render(<OpencodeView {...makeProps({
      sessionID: 'fake-session-id', directory: '/wd', showSessionControls: true,
      modelVariants: VARIANTS, defaultEffort: 'medium',
    })} />);
    await waitFor(() => expect(screen.getByTestId('control-reasoning_effort')).toBeTruthy());
    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'hi' } });
    fireEvent.click(screen.getByTestId('conversation-send'));
    await waitFor(() => expect(posts.some((p) => p.url.includes('/prompt_async'))).toBe(true));
    const sent = posts.find((p) => p.url.includes('/prompt_async'))!;
    expect(sent.body.variant).toBe('medium');
    expect(sent.body.model).toEqual({ providerID: 'opencode', modelID: 'big-pickle' });
  });

  it('falls back to the first variant when no defaultEffort is set', async () => {
    const posts: { url: string; body: any }[] = [];
    installFetch({ history: [], posts });
    render(<OpencodeView {...makeProps({
      sessionID: 'fake-session-id', directory: '/wd', showSessionControls: true,
      modelVariants: VARIANTS,
    })} />);
    await waitFor(() => expect(screen.getByTestId('control-reasoning_effort')).toBeTruthy());
    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'hi' } });
    fireEvent.click(screen.getByTestId('conversation-send'));
    await waitFor(() => expect(posts.some((p) => p.url.includes('/prompt_async'))).toBe(true));
    expect(posts.find((p) => p.url.includes('/prompt_async'))!.body.variant).toBe('low');
  });

  it('drops the effort control and the variant when switching to a no-variant model', async () => {
    const posts: { url: string; body: any }[] = [];
    installFetch({ history: [], posts });
    render(<OpencodeView {...makeProps({
      sessionID: 'fake-session-id', directory: '/wd', showSessionControls: true,
      modelVariants: VARIANTS, defaultEffort: 'high',
    })} />);
    await waitFor(() => expect(screen.getByTestId('control-reasoning_effort')).toBeTruthy());

    // Switch from big-pickle (has variants) to deepseek-v4-flash (none).
    fireEvent.mouseDown(screen.getByTestId('control-model').querySelector('.ant-select-selector')!);
    await waitFor(() => expect(screen.getByText('DeepSeek V4 Flash')).toBeTruthy());
    fireEvent.click(screen.getByText('DeepSeek V4 Flash'));

    // The effort control disappears (presence follows the model).
    await waitFor(() => expect(screen.queryByTestId('control-reasoning_effort')).toBeNull());

    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'hi' } });
    fireEvent.click(screen.getByTestId('conversation-send'));
    await waitFor(() => expect(posts.some((p) => p.url.includes('/prompt_async'))).toBe(true));
    const sent = posts.find((p) => p.url.includes('/prompt_async'))!;
    expect(sent.body.model).toEqual({ providerID: 'opencode', modelID: 'deepseek-v4-flash' });
    expect(sent.body.variant).toBeUndefined();
  });
});
