import { Alert, Button, Typography } from 'antd';
import { useProcessConversation, type WidgetProps } from 'optio-ui';
import { useSpinnerKey, useVultusComponents } from 'vultus-antd';
import { ClaudeCodeReplayView } from './claudecode/ClaudeCodeReplayView.js';

// The conversation widget's suspended variant
// (docs/2026-10-12-suspended-conversation-view-design.md, section 5): optio-ui
// renders it instead of ConversationWidget while the process is suspended
// (cancelled with saved state). It reads the conversation as it stood at the
// suspend, read-only, through optio-api's getConversation — its only request.

const OTHER_PROTOCOL_NOTE = 'Resume to view this conversation.';
const NO_RECORD_NOTE = 'No saved conversation to show; resume to continue it.';
const LOAD_FAILED = 'Could not load the saved conversation.';

function Note({ text }: { text: string }) {
  return (
    <div data-testid="optio-conversation-suspended-note" style={{ padding: 16 }}>
      <Typography.Text type="secondary">{text}</Typography.Text>
    </div>
  );
}

export function SuspendedConversationWidget(props: WidgetProps) {
  const { process } = props;
  const widgetData = process?.widgetData ?? undefined;
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
    stoppedAt: process?.status?.stoppedAt ?? null,
  });

  if (!replayable) return <Note text={OTHER_PROTOCOL_NOTE} />;

  const error = conversation.error;
  const reason: unknown = error?.body?.reason;
  // The process resumed while this view was open: render nothing; the live
  // widget takes over once the process is widget-live again.
  if (reason === 'not-suspended') return null;

  const body = conversation.data?.body;
  if (body) {
    return (
      <ClaudeCodeReplayView
        process={process}
        widgetData={widgetData}
        events={body.events}
        capturedAt={typeof body.capturedAt === 'string' ? body.capturedAt : null}
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
      <div data-testid="optio-conversation-suspended-error" style={{ padding: 16 }}>
        <Alert
          type="error"
          showIcon
          title={message}
          action={
            <Button size="small" onClick={() => void conversation.refetch()}>
              Retry
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div data-testid="optio-widget-loading">
      {Spinner && (
        <span style={{ marginInlineEnd: 8 }}><Spinner spinnerKey={spinnerKey} size="small" /></span>
      )}
      Loading…
    </div>
  );
}
