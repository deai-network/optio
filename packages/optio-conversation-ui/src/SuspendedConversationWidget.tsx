import { useRef, type ReactNode } from 'react';
import { Alert, Button, Typography, theme } from 'antd';
import { isActive, isSuspended, useProcessConversation, type WidgetProps } from 'optio-ui';
import { useSpinnerKey, useVultusComponents } from 'vultus-antd';
import { ClaudeCodeReplayView } from './claudecode/ClaudeCodeReplayView.js';

// The conversation widget's suspended variant
// (docs/2026-10-12-suspended-conversation-view-design.md, section 5): optio-ui
// renders it instead of ConversationWidget while the process is suspended
// (cancelled with saved state). It reads the conversation as it stood at the
// suspend, through optio-api's getConversation — its only request until the
// user sends a message, which resumes the session (ClaudeCodeReplayView).

const OTHER_PROTOCOL_NOTE = 'Resume to view this conversation.';
const NO_RECORD_NOTE = 'No saved conversation to show; resume to continue it.';
const LOAD_FAILED = 'Could not load the saved conversation.';

// The states other than the replay (note, error, loading) paint their own
// surface from the theme tokens, as ConversationView does: under the
// registration's ownTheme ConfigProvider (dark included) nothing else paints
// one, and they would otherwise sit on the host's page colour.
function Surface({ testId, children }: { testId: string; children: ReactNode }) {
  const { token } = theme.useToken();
  return (
    <div
      data-testid={testId}
      style={{
        width: '100%', height: '100%', boxSizing: 'border-box', padding: 16,
        background: token.colorBgLayout, color: token.colorText,
      }}
    >
      {children}
    </div>
  );
}

function Note({ text }: { text: string }) {
  return (
    <Surface testId="optio-conversation-suspended-note">
      <Typography.Text type="secondary">{text}</Typography.Text>
    </Surface>
  );
}

export function SuspendedConversationWidget(props: WidgetProps) {
  const { process } = props;
  // optio-ui keeps this variant on screen while the process resumes, until
  // its live widget is ready (useProcessWidgetState's 'resuming'). The
  // relaunch clears widgetData and stoppedAt: keep what the suspended
  // process had, so the replay neither changes nor refetches meanwhile.
  const resuming = !isSuspended(process) && isActive(process);
  const own = { widgetData: process?.widgetData ?? undefined, stoppedAt: process?.status?.stoppedAt ?? null };
  const kept = useRef(own);
  if (!resuming) kept.current = own;
  const { widgetData, stoppedAt } = resuming ? kept.current : own;
  // widgetData.protocol survives the suspend. Only claudecode writes a record
  // today; the other agents get a note and no request.
  const protocol = widgetData?.protocol ?? 'claudecode';
  const replayable = protocol === 'claudecode';

  // Hooks first, unconditionally. The app's lent Spinner (vultus-core's slot),
  // shown before "Loading…" as in OpencodeView; outside the process's
  // SpinnerScope, so the key names the process itself.
  const { Spinner } = useVultusComponents();
  const spinnerKey = useSpinnerKey(`optio:process:${process?._id}:archive`);
  // stoppedAt from props keys the query per suspend without polling the
  // process document.
  const conversation = useProcessConversation(process?._id, {
    enabled: replayable,
    stoppedAt,
  });

  if (!replayable) return <Note text={OTHER_PROTOCOL_NOTE} />;

  const error = conversation.error;
  const reason: unknown = error?.body?.reason;
  const body = conversation.data?.body;
  // The process resumed while this view was open, before the record was
  // read: render nothing; the live widget takes over once it is ready.
  if (reason === 'not-suspended' && !body) return null;
  if (body) {
    return (
      <ClaudeCodeReplayView
        process={process}
        widgetData={widgetData}
        events={body.events}
        capturedAt={typeof body.capturedAt === 'string' ? body.capturedAt : null}
        apiBaseUrl={props.apiBaseUrl}
        database={props.database}
        prefix={props.prefix}
        widgetProxyUrl={props.widgetProxyUrl}
        readOnly={(props as { readOnly?: boolean }).readOnly}
        hideEmptyRuns={props.hideEmptyRuns}
        resuming={resuming}
        themeMode={(props as any).themeMode}
        onToggleTheme={(props as any).onToggleTheme}
      />
    );
  }

  if (error) {
    if (reason === 'no-conversation' || reason === 'no-conversation-support') {
      return <Note text={NO_RECORD_NOTE} />;
    }
    // Everything else (read-failed, engine-unavailable, forbidden, not-found,
    // 401, a network failure) may pass on a second try.
    const message =
      (typeof error?.body?.message === 'string' && error.body.message) ||
      (typeof error?.message === 'string' && error.message) ||
      LOAD_FAILED;
    return (
      <Surface testId="optio-conversation-suspended-error">
        <Alert
          type="error"
          showIcon
          title={message}
          action={
            // Spins (and ignores clicks) while a refetch runs.
            <Button size="small" loading={Boolean(conversation.isFetching)} onClick={() => void conversation.refetch()}>
              Retry
            </Button>
          }
        />
      </Surface>
    );
  }

  // The host's stand-in while the record loads (WidgetProps.placeholder),
  // else the loading line.
  if (props.placeholder !== undefined) {
    return (
      <div data-testid="optio-conversation-suspended-loading" style={{ width: '100%', height: '100%' }}>
        {props.placeholder}
      </div>
    );
  }
  return (
    <Surface testId="optio-conversation-suspended-loading">
      <div data-testid="optio-widget-loading">
        {Spinner && (
          <span style={{ marginInlineEnd: 8 }}><Spinner spinnerKey={spinnerKey} size="small" /></span>
        )}
        Loading…
      </div>
    </Surface>
  );
}
