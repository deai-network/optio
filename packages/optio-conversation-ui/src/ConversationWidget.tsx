import React, { useState } from 'react';
import { ConfigProvider, theme as antdTheme } from 'antd';
import { registerWidget, type WidgetProps } from 'optio-ui';
import { ClaudeCodeView } from './claudecode/ClaudeCodeView.js';
import { OpencodeView } from './opencode/OpencodeView.js';
import { GrokView } from './grok/GrokView.js';
import { CodexView } from './codex/CodexView.js';
import { CursorView } from './cursor/CursorView.js';
import { KimiCodeView } from './kimicode/KimiCodeView.js';
import { AntigravityView } from './antigravity/AntigravityView.js';

const THEME_KEY = 'optio-conversation:theme';

export interface ConversationWidgetProps extends WidgetProps {
  /** Opt-in: when true, the widget wraps its view in its own antd
   *  ConfigProvider with a persisted light/dark preference and renders the
   *  ☀/🌙 toggle. When false/absent, the view inherits the host theme. */
  ownTheme?: boolean;
}

/** Engine-neutral conversation widget: the task declares its wire protocol
 *  via widgetData.protocol; each view speaks that protocol natively through
 *  the widget proxy. */
export function ConversationWidget({ ownTheme, ...props }: ConversationWidgetProps) {
  const protocol = (props.process as any)?.widgetData?.protocol ?? 'claudecode';

  const dispatchedView = (extra: { themeMode?: 'light' | 'dark'; onToggleTheme?: () => void }) => {
    const viewProps = { ...props, ...extra } as WidgetProps;
    if (protocol === 'opencode') return <OpencodeView {...viewProps} />;
    if (protocol === 'grok') return <GrokView {...viewProps} />;
    if (protocol === 'codex') return <CodexView {...viewProps} />;
    if (protocol === 'cursor') return <CursorView {...viewProps} />;
    if (protocol === 'kimicode') return <KimiCodeView {...viewProps} />;
    if (protocol === 'antigravity') return <AntigravityView {...viewProps} />;
    return <ClaudeCodeView {...viewProps} />;
  };

  if (!ownTheme) return dispatchedView({});

  return <ThemedConversation dispatchedView={dispatchedView} />;
}

function ThemedConversation({
  dispatchedView,
}: {
  dispatchedView: (extra: { themeMode?: 'light' | 'dark'; onToggleTheme?: () => void }) => React.ReactElement;
}) {
  const [mode, setMode] = useState<'light' | 'dark'>(() =>
    typeof localStorage !== 'undefined' && localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light',
  );
  const toggle = () =>
    setMode((m) => {
      const next = m === 'dark' ? 'light' : 'dark';
      try {
        localStorage.setItem(THEME_KEY, next);
      } catch {
        /* ignore */
      }
      return next;
    });

  return (
    <ConfigProvider
      theme={{ algorithm: mode === 'dark' ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm }}
    >
      {dispatchedView({ themeMode: mode, onToggleTheme: toggle })}
    </ConfigProvider>
  );
}

/** Register the conversation widget for the `conversation` ui-widget protocol.
 *  Pass `{ ownTheme: true }` when the HOST has no theme of its own and wants the
 *  widget to own a light/dark toggle (standalone / a host without its own
 *  ConfigProvider). Default: inherit the host theme. */
export function registerConversationWidget(opts?: { ownTheme?: boolean }): void {
  const Widget = (props: WidgetProps) => (
    <ConversationWidget ownTheme={opts?.ownTheme} {...props} />
  );
  registerWidget('conversation', Widget);
  console.info('[optio-conversation-ui] conversation widget registered');
}
