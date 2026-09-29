import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import type { ComponentType, ReactElement } from 'react';
import type { WidgetProps } from 'optio-ui';
import { GrokView } from '../grok/GrokView.js';
import { ClaudeCodeView } from '../claudecode/ClaudeCodeView.js';
import { CodexView } from '../codex/CodexView.js';
import { CursorView } from '../cursor/CursorView.js';
import { KimiCodeView } from '../kimicode/KimiCodeView.js';
import { AntigravityView } from '../antigravity/AntigravityView.js';
import { OpencodeView } from '../opencode/OpencodeView.js';

class FakeEventSource {
  onmessage: ((ev: MessageEvent) => void) | null = null;
  constructor(public url: string) {}
  close() {}
}

beforeEach(() => {
  vi.stubGlobal('EventSource', FakeEventSource);
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true,
    json: async () => [],
    headers: { get: () => null },
  })));
});
afterEach(() => {
  vi.unstubAllGlobals();
});

const base: Omit<WidgetProps, 'process'> = {
  apiBaseUrl: 'http://localhost',
  widgetProxyUrl: 'http://localhost/api/widget/x/',
  prefix: 'optio',
};

function process(extra: Record<string, unknown> = {}) {
  return {
    _id: 'abc',
    widgetData: {
      protocol: 'x',
      uploadUrl: '/up',
      todos: [{ id: '1', text: 'Write', status: 'in_progress', active: 'Writing' }],
      ...extra,
    },
  };
}

const views: Array<[string, ComponentType<WidgetProps>, Record<string, unknown>]> = [
  ['grok', GrokView, {}],
  ['claude', ClaudeCodeView, {}],
  ['codex', CodexView, {}],
  ['cursor', CursorView, {}],
  ['kimi', KimiCodeView, {}],
  ['antigravity', AntigravityView, {}],
  ['opencode', OpencodeView, { sessionID: 's1' }],
];

describe('engine views publish widgetData.todos', () => {
  it.each(views)('%s shows the checklist above the transcript', async (_name, View, extra) => {
    await act(async () => {
      render(
        <ConfigProvider>
          {(<View {...base} process={process(extra)} />) as ReactElement}
        </ConfigProvider>,
      );
    });
    const row = screen.getByTestId('conversation-todo');
    expect(row.getAttribute('data-status')).toBe('in_progress');
    expect(row.textContent).toContain('Write');
    expect(row.textContent).not.toContain('Writing');
  });
});
