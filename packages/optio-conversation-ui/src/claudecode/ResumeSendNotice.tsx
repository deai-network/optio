import { Alert } from 'antd';
import { dismissResumeSend, type ResumeSendState } from './resumeAndSend.js';

const RESUMING = 'Resuming the session; your message will be sent once it is ready.';

// The composer's notice for a message sent to resume a suspended session
// (resumeAndSend.ts), shown by the replay and by the live view that replaces
// it: waiting, or not sent (its text goes back into the input; closing the
// notice forgets it).
export function ResumeSendNotice({ processId, state }: { processId: string; state: ResumeSendState | undefined }) {
  if (state?.phase === 'resuming') {
    return <Alert type="info" showIcon title={RESUMING} data-testid="optio-resume-send-pending" />;
  }
  if (state?.phase === 'failed') {
    return (
      <Alert
        type="warning"
        showIcon
        title={`Your message was not sent: ${state.message}`}
        closable={{ onClose: () => dismissResumeSend(processId) }}
        data-testid="optio-resume-send-failed"
      />
    );
  }
  return null;
}
