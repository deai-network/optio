import React, { useContext, useEffect, useRef, useState } from 'react';
import { ConfigProvider, theme as antdTheme } from 'antd';
import { registerWidget, type WidgetProps } from 'optio-ui';
import { ClaudeCodeView } from './claudecode/ClaudeCodeView.js';
import { OpencodeView } from './opencode/OpencodeView.js';
import { GrokView } from './grok/GrokView.js';
import { CodexView } from './codex/CodexView.js';
import { CursorView } from './cursor/CursorView.js';
import { KimiCodeView } from './kimicode/KimiCodeView.js';
import { AntigravityView } from './antigravity/AntigravityView.js';
import { SuspendedConversationWidget } from './SuspendedConversationWidget.js';
import { ConversationWidenessContext, type ConversationWideness } from './wideness.js';
import { ConversationWelcomeContext, welcomeOf } from './welcome.js';
import { ConversationSetBusyContext, type ConversationSetBusy } from './busy.js';

const THEME_KEY = 'optio-conversation:theme';

export interface ConversationWidgetProps extends WidgetProps {
  /** Opt-in: when true, the widget wraps its view in its own antd
   *  ConfigProvider with a persisted light/dark preference and renders the
   *  ☀/🌙 toggle. When false/absent, the view inherits the host theme. */
  ownTheme?: boolean;
  /** The conversation's reading width: 'wide' (always the full width),
   *  'narrow' (always the capped reading column) or 'switch' (the Wide switch
   *  in the conversation's header). Absent: the host's
   *  ConversationWidenessContext, which it can change live ('narrow' when the
   *  host sets none). */
  wideness?: ConversationWideness;
  /** The host app's callback for the agent's busy / idle state (see
   *  busy.ts). Absent: the host's ConversationSetBusyContext, if any. */
  setBusy?: ConversationSetBusy;
}

/** Engine-neutral conversation widget: the task declares its wire protocol
 *  via widgetData.protocol; each view speaks that protocol natively through
 *  the widget proxy. */
export function ConversationWidget({ ownTheme, wideness, setBusy, ...props }: ConversationWidgetProps) {
  const fromHost = useContext(ConversationWidenessContext);
  const setBusyFromHost = useContext(ConversationSetBusyContext);
  return (
    <ConversationWidenessContext.Provider value={wideness ?? fromHost}>
      <ConversationSetBusyContext.Provider value={setBusy ?? setBusyFromHost}>
        <ConversationWelcomeContext.Provider value={welcomeOf(props.process)}>
          <ConversationWidgetBody ownTheme={ownTheme} {...props} />
        </ConversationWelcomeContext.Provider>
      </ConversationSetBusyContext.Provider>
    </ConversationWidenessContext.Provider>
  );
}

function ConversationWidgetBody({ ownTheme, ...props }: Omit<ConversationWidgetProps, 'wideness' | 'setBusy'>) {
  const protocol = (props.process as any)?.widgetData?.protocol ?? 'claudecode';
  // The views that do not apply their backlog in one step yet are ready at
  // once (WidgetProps.onReady): a resume hands over to them immediately.
  const onReadyRef = useRef(props.onReady);
  onReadyRef.current = props.onReady;
  useEffect(() => {
    if (protocol !== 'claudecode') onReadyRef.current?.();
  }, [protocol]);

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

/** The suspended variant under the same own light/dark toggle as the live one. */
function ThemedSuspendedConversation(props: WidgetProps) {
  return (
    <ThemedConversation
      dispatchedView={(extra) => <SuspendedConversationWidget {...({ ...props, ...extra } as WidgetProps)} />}
    />
  );
}

/** Register the conversation widget for the `conversation` ui-widget protocol,
 *  with SuspendedConversationWidget as its suspended variant (a suspended
 *  task's saved conversation, read-only).
 *  Pass `{ ownTheme: true }` when the HOST has no theme of its own and wants the
 *  widget to own a light/dark toggle (standalone / a host without its own
 *  ConfigProvider). Default: inherit the host theme. `wideness` fixes the
 *  conversation's reading width ('wide' | 'narrow' | 'switch', see
 *  wideness.ts), for the suspended variant too; without it the widget follows
 *  the host's ConversationWidenessContext ('narrow' when none). `setBusy`
 *  receives the agent's busy / idle state (see busy.ts); without it the widget
 *  reports to the host's ConversationSetBusyContext, if any. The suspended
 *  variant is idle. */
export function registerConversationWidget(
  opts?: { ownTheme?: boolean; wideness?: ConversationWideness; setBusy?: ConversationSetBusy },
): void {
  const wideness = opts?.wideness;
  const Widget = (props: WidgetProps) => (
    <ConversationWidget ownTheme={opts?.ownTheme} wideness={wideness} setBusy={opts?.setBusy} {...props} />
  );
  const Suspended = opts?.ownTheme ? ThemedSuspendedConversation : SuspendedConversationWidget;
  // The suspended variant shows the task's welcome card too; without a
  // registered wideness it follows the host's ConversationWidenessContext,
  // like the live one.
  const SuspendedWithWelcome = (props: WidgetProps) => (
    <ConversationWelcomeContext.Provider value={welcomeOf(props.process)}>
      <Suspended {...props} />
    </ConversationWelcomeContext.Provider>
  );
  const SuspendedWidget = wideness === undefined ? SuspendedWithWelcome : (props: WidgetProps) => (
    <ConversationWidenessContext.Provider value={wideness}>
      <SuspendedWithWelcome {...props} />
    </ConversationWidenessContext.Provider>
  );
  registerWidget('conversation', Widget, { suspended: SuspendedWidget });
  console.info('[optio-conversation-ui] conversation widget registered');
}
