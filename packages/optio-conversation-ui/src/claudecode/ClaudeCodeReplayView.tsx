import { useMemo, useState } from 'react';
import { Modal } from 'antd';
import { isResumable, isResurrectable, type WidgetProps } from 'optio-ui';
import { SpinnerScope } from 'vultus-antd';
import type { ChatState, CustomControl, SessionControl } from '../chat.js';
import { initialChatState, reduceEvent, type ReduceOptions } from './events.js';
import { ConversationView } from '../ConversationView.js';
import { todosFromWidgetData } from '../todosFromWidgetData.js';
import { NativeSpinner } from '../spinners/NativeSpinner.js';
import { dismissResumeSend, startResumeSend, useResumeSend } from './resumeAndSend.js';
import { ResumeSendNotice } from './ResumeSendNotice.js';
import { ResumingPanel } from './ResumingPanel.js';

// Read-only replay of a suspended claudecode task's saved conversation record
// (docs/2026-10-12-suspended-conversation-view-design.md, section 5). It folds
// the record through the same reducer ClaudeCodeView uses live and renders the
// same ConversationView, closed, with no transport: no EventSource, no POSTs.
// Given the widget's API and proxy URLs, its composer stays usable: Send asks
// to resume the session, then resumes it and sends the message once it is
// live (resumeAndSend.ts). A task with smooth_resume (widgetData.smoothResume)
// is not asked, unless the send discards unsaved work. Without the URLs the
// view is read-only.

export interface ClaudeCodeReplayViewProps {
  process: WidgetProps['process'];
  // The process's widgetData (it survives the suspend): the same settings the
  // live view reads.
  widgetData: unknown;
  // The record's `[seq, event]` pairs, as optio-api's getConversation returns them.
  events: unknown[];
  // When the record was captured (ISO, with offset), or null.
  capturedAt: string | null;
  // WidgetProps' URLs and instance: sending resumes the session through them.
  // Without apiBaseUrl / prefix / widgetProxyUrl the composer stays disabled.
  apiBaseUrl?: string;
  database?: string;
  prefix?: string;
  widgetProxyUrl?: string;
  // WidgetProps.readOnly: the host shows the process read-only, so the
  // composer must not resume it.
  readOnly?: boolean;
  // WidgetProps.hideEmptyRuns: leave out the runs a resume added where nothing
  // happened, the last one included (see ConversationView).
  hideEmptyRuns?: boolean;
  // The session is resuming (optio-ui keeps this view on screen until the
  // live one is ready): the input gives way to a spinner panel.
  resuming?: boolean;
  // theming (only set by ConversationWidget's registration when ownTheme):
  themeMode?: 'light' | 'dark';
  onToggleTheme?: () => void;
}

const SUSPENDED_CLOSED_LABEL = 'Session has been saved';
const CONFIRM_TITLE = 'Resume the session?';
const CONFIRM_TEXT = 'This session is currently suspended. Sending this message will resume it. Is this what you want?';
const CONFIRM_OK = 'Resume and send';
// A failed run left unsaved work on its host: resuming discards it, so the
// question says so, as LaunchControls' Resume does.
const DISCARD_TEXT = 'This discards the unsaved work left by the failed run.';
const DISCARD_OK = 'Discard and send';

const noSend = async (): Promise<boolean> => false;
const noop = (): void => {};

// Fold the record with one fixed clock, so the times the reducer derives from
// `now` (durations of rows without a wire time) stay put across re-renders.
// Then close it as a suspended session at maxSeq + 1: the reducer stops what
// was still running (tool rows, background jobs, an in-flight answer) and sets
// `closed`, which disables the composer. `opts` are the live view's reducer
// options (thinkingRows), so the replay shows what the live view showed.
function foldRecord(events: unknown[], seed: ChatState, now: number, opts: ReduceOptions): ChatState {
  let state = seed;
  let maxSeq = 0;
  for (const entry of Array.isArray(events) ? events : []) {
    if (!Array.isArray(entry) || entry.length < 2) continue;
    const seq = Number(entry[0]);
    if (!Number.isFinite(seq)) continue;
    state = reduceEvent(state, entry[1], seq, now, opts);
    if (seq > maxSeq) maxSeq = seq;
  }
  const closeSeq = maxSeq + 1;
  state = reduceEvent(state, { type: 'x-optio-closed', reason: 'suspended' }, closeSeq, now, opts);
  // The closing event also appends its own "conversation ended (suspended)"
  // row; the closedLabel divider already says it, so drop that row.
  return { ...state, items: state.items.filter((i) => !(i.kind === 'closed' && i.seq === closeSeq)) };
}

export function ClaudeCodeReplayView({
  process,
  widgetData,
  events,
  capturedAt,
  apiBaseUrl,
  database,
  prefix,
  widgetProxyUrl,
  readOnly,
  hideEmptyRuns,
  resuming,
  themeMode,
  onToggleTheme,
}: ClaudeCodeReplayViewProps) {
  // The same widgetData reads as ClaudeCodeView, minus the transport ones.
  const toolVerbosity = ((widgetData as any)?.toolVerbosity ?? 'description-only') as
    'silent' | 'description-while-active' | 'description-only' | 'verbose';
  const thinkingVerbosity = ((widgetData as any)?.thinkingVerbosity ?? 'hidden') as
    'hidden' | 'visible';
  const initialControls = ((widgetData as any)?.controls ?? []) as SessionControl[];
  const initialCustomControls = ((widgetData as any)?.customControls ?? []) as CustomControl[];
  const showSessionControls = Boolean((widgetData as any)?.showSessionControls);
  const maxUploadBytes = Number((widgetData as any)?.maxUploadBytes ?? 10_000_000);
  const nativeSpinner = Boolean((widgetData as any)?.nativeSpinner);
  // The task's smooth_resume: sending resumes the session without asking.
  const smoothResume = (widgetData as any)?.smoothResume === true;

  // capturedAt, else one clock read at mount.
  const [mountedAt] = useState(() => Date.now());
  const captured = capturedAt ? Date.parse(capturedAt) : NaN;
  const now = Number.isFinite(captured) ? captured : mountedAt;

  // Seeded like ClaudeCodeView's reducer: `{...initialChatState, controls,
  // customControls}` from widgetData. Re-folded only when the record (events
  // array) or the thinking verbosity changes; `now` changes only with the record.
  const thinkingRows = thinkingVerbosity === 'visible';
  const state = useMemo(
    () => foldRecord(
      events,
      { ...initialChatState, controls: initialControls, customControls: initialCustomControls },
      now,
      { thinkingRows },
    ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [events, now, thinkingRows],
  );

  // Sending resumes the session (when the widget's URLs are known).
  const [modal, modalHolder] = Modal.useModal();
  const pending = useResumeSend(process._id);
  const canResume = !readOnly && apiBaseUrl !== undefined && Boolean(prefix) && Boolean(widgetProxyUrl);
  const discards = isResurrectable(process);
  // smooth_resume: a send that resumes the saved session goes straight to
  // resume-and-send. One that discards a failed run's unsaved work still asks.
  const asks = !(smoothResume && isResumable(process) && !discards);
  const confirmSend = (): Promise<boolean> =>
    new Promise((resolve) => {
      modal.confirm({
        title: CONFIRM_TITLE,
        content: discards ? `${CONFIRM_TEXT} ${DISCARD_TEXT}` : CONFIRM_TEXT,
        okText: discards ? DISCARD_OK : CONFIRM_OK,
        okButtonProps: discards ? { danger: true } : undefined,
        cancelText: 'Cancel',
        onOk: () => resolve(true),
        onCancel: () => resolve(false),
      });
    });
  const sendByResuming = async (text: string): Promise<boolean | { error: string }> => {
    dismissResumeSend(process._id);
    const outcome = await startResumeSend(
      { processId: process._id, apiBaseUrl: apiBaseUrl!, database, prefix: prefix!, widgetProxyUrl: widgetProxyUrl! },
      text,
    );
    return outcome.ok ? true : { error: outcome.message };
  };

  // The same state-derived props as ClaudeCodeView. No steer, interrupt,
  // permission, download or control-change handlers: the session controls get
  // their values (for labels) but, without onControlChange, render no inputs.
  // Likewise the custom controls: they name the modes in the switch and resume
  // bands, and without onCustomControlChange no selector renders.
  return (
    <SpinnerScope name={`optio:process:${process._id}`}>
      {modalHolder}
      <ConversationView
        todos={todosFromWidgetData(widgetData)}
        state={state}
        closed={state.closed}
        closedLabel={SUSPENDED_CLOSED_LABEL}
        busy={state.busy}
        toolVerbosity={toolVerbosity}
        hideEmptyRuns={hideEmptyRuns}
        thinkingVerbosity={thinkingVerbosity}
        showFileUpload={false}
        maxUploadBytes={maxUploadBytes}
        fileDownload={false}
        nativeSpinner={nativeSpinner ? <NativeSpinner engine="claudecode" /> : undefined}
        onSend={canResume ? sendByResuming : noSend}
        // A send still waiting for the resume locks the composer; a failed one
        // puts its text back (draft), with the reason in the notice.
        composeWhenClosed={canResume && pending?.phase !== 'resuming'}
        inputReplacement={resuming ? <ResumingPanel pending={pending} /> : undefined}
        confirmSend={asks ? confirmSend : undefined}
        draft={pending?.phase === 'failed' ? pending.text : undefined}
        // While resuming, the panel says it instead.
        notice={resuming ? undefined : <ResumeSendNotice processId={process._id} state={pending} />}
        onInterrupt={noop}
        onPermission={noop}
        onFileDownload={noop}
        controls={showSessionControls ? state.controls : undefined}
        customControls={state.customControls}
        themeMode={themeMode}
        onToggleTheme={onToggleTheme}
      />
    </SpinnerScope>
  );
}
