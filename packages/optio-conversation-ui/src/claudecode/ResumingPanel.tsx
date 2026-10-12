import { Spin, Typography, theme } from 'antd';
import { useSpinnerKey, useVultusComponents } from 'vultus-antd';
import type { ResumeSendState } from './resumeAndSend.js';

const RESUMING = 'Resuming the session…';
const MESSAGE_WAITS = 'Your message will be sent once it is ready.';
// A big spinner: the app's lent one (vultus-core's slot) when there is one.
const SPINNER_PX = 48;

// Shown instead of the replay's input while the session resumes (optio-ui
// keeps the suspended view on screen until the live one is ready): a big
// spinner and what is happening. Inside the replay's SpinnerScope, so the
// lent spinner's key is optio:process:<id>/resuming.
export function ResumingPanel({ pending }: { pending: ResumeSendState | undefined }) {
  const { token } = theme.useToken();
  const { Spinner } = useVultusComponents();
  const spinnerKey = useSpinnerKey('resuming');
  return (
    <div
      data-testid="optio-replay-resuming"
      role="status"
      style={{
        display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12,
        padding: '20px 8px', color: token.colorText,
      }}
    >
      {Spinner ? <Spinner spinnerKey={spinnerKey} size={SPINNER_PX} label={RESUMING} /> : <Spin size="large" />}
      <Typography.Text strong>{RESUMING}</Typography.Text>
      {pending?.phase === 'resuming' && <Typography.Text type="secondary">{MESSAGE_WAITS}</Typography.Text>}
    </div>
  );
}
