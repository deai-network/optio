import { useEffect, useMemo, useReducer, useRef, useState } from 'react';
import type { WidgetProps } from 'optio-ui';
import { SpinnerScope } from 'vultus-antd';
import { CUSTOM_CONTROL_PREFIX, type ChatState, type CustomControl, type SessionControl } from '../chat.js';
import { initialChatState, reduceEvent } from './events.js';
import type { Attachment } from '../attachments.js';
import { resolveUploadUrl, uploadFiles, bundleUploadNotice } from '../uploads.js';
import { blobDownload } from '../FileDownloadContext.js';
import { ConversationView } from '../ConversationView.js';
import { ControlErrorSlot, controlFailure, type ControlErrorSink } from '../CustomControls.js';
import { todosFromWidgetData } from '../todosFromWidgetData.js';
import { NativeSpinner } from '../spinners/NativeSpinner.js';
import { dismissResumeSend, useResumeSend } from './resumeAndSend.js';
import { ResumeSendNotice } from './ResumeSendNotice.js';

interface ChatAction {
  ev: unknown;
  seq: number;
}

// thinkingRows: see ReduceOptions (the task's thinkingVerbosity is "visible").
const WAIT_FOR_RESUME_SEND = 'Your earlier message is still waiting for the session; send this one once it has gone.';

function chatReducer(thinkingRows: boolean) {
  return (state: ChatState, action: ChatAction): ChatState =>
    reduceEvent(state, action.ev, action.seq, undefined, { thinkingRows });
}

export function ClaudeCodeView(props: WidgetProps) {
  const toolVerbosity = ((props.process.widgetData as any)?.toolVerbosity ?? 'description-only') as
    'silent' | 'description-while-active' | 'description-only' | 'verbose';
  const thinkingVerbosity = ((props.process.widgetData as any)?.thinkingVerbosity ?? 'hidden') as
    'hidden' | 'visible';
  const initialControls = ((props.process.widgetData as any)?.controls ?? []) as SessionControl[];
  const showSessionControls = Boolean((props.process.widgetData as any)?.showSessionControls);
  const initialCustomControls = ((props.process.widgetData as any)?.customControls ?? []) as CustomControl[];
  const reducer = useMemo(() => chatReducer(thinkingVerbosity === 'visible'), [thinkingVerbosity]);
  const [state, dispatch] = useReducer(reducer, {
    ...initialChatState, controls: initialControls, customControls: initialCustomControls,
  });
  const localSeqRef = useRef(0);
  // The custom controls' values as the engine last sent them (widgetData,
  // then every x-optio-control-update carrying customControls), and how many
  // such updates named each control: a failed pick puts its control back to
  // the engine's value, unless an update came since the pick started.
  const [engineCustom] = useState(() => ({
    values: new Map(initialCustomControls.map((c) => [c.id, c.value ?? null] as const)),
    updates: new Map<string, number>(),
  }));
  // The input bar's error slot, which the built-in controls' failures are
  // shown in too (see ControlErrorSlot).
  const controlErrorSink = useRef<ControlErrorSink['current']>(null);
  const showFileUpload = Boolean((props.process.widgetData as any)?.showFileUpload);
  const maxUploadBytes = Number((props.process.widgetData as any)?.maxUploadBytes ?? 10_000_000);
  const fileDownload = Boolean((props.process.widgetData as any)?.fileDownload);
  const nativeSpinner = Boolean((props.process.widgetData as any)?.nativeSpinner);

  const { widgetProxyUrl } = props; // ends with '/' — trailing slash is load-bearing

  useEffect(() => {
    console.info('[optio-conversation-ui] claudecode conversation widget activated:', `${widgetProxyUrl}events`);
    const es = new EventSource(`${widgetProxyUrl}events`);
    es.onmessage = (ev: MessageEvent) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(ev.data);
      } catch {
        return;
      }
      const update = parsed as { type?: unknown; customControls?: unknown } | null;
      if (update?.type === 'x-optio-control-update' && Array.isArray(update.customControls)) {
        for (const c of update.customControls as CustomControl[]) {
          if (typeof c?.id !== 'string') continue;
          engineCustom.values.set(c.id, c.value ?? null);
          engineCustom.updates.set(c.id, (engineCustom.updates.get(c.id) ?? 0) + 1);
        }
      }
      // The reducer sniffs the runtime model from system/init & message.model
      // and folds it into the model control (only while the control has no
      // value yet — an operator pick wins).
      dispatch({ ev: parsed, seq: Number(ev.lastEventId) });
    };
    return () => es.close();
  }, [widgetProxyUrl, engineCustom]);

  // The optimistic local-user echo (dispatched on a successful send) sets
  // state.busy immediately, so busy is purely reducer-driven — no separate
  // send flag that a busy-change effect could fail to clear on a mid-turn send.
  const busy = state.busy;

  // POST a JSON body. Resolves to whether the answer was 2xx, with its body
  // ({} when the body is not a JSON object); null when the request failed.
  async function postForResult(
    path: string, body: unknown,
  ): Promise<{ ok: boolean; body: Record<string, unknown> } | null> {
    try {
      const resp = await fetch(`${widgetProxyUrl}${path}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      let parsed: unknown = null;
      try {
        parsed = await resp.json();
      } catch {
        /* no JSON body */
      }
      return { ok: resp.ok, body: parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {} };
    } catch {
      return null;
    }
  }

  // POST a JSON body. Returns the parsed response on 2xx ({} when the body is
  // not a JSON object), null on failure.
  async function postJson(path: string, body: unknown): Promise<Record<string, unknown> | null> {
    const result = await postForResult(path, body);
    return result?.ok ? result.body : null;
  }

  async function post(path: string, body: unknown): Promise<boolean> {
    return (await postJson(path, body)) !== null;
  }

  // When files are attached, upload them through the generic route first, then
  // bundle one `System:` notice line per stored file into the prompt so the
  // agent can Read them from the workdir. null: nothing left to send.
  async function preparePrompt(body: string, attachments: Attachment[]): Promise<string | null> {
    if (attachments.length === 0) return body;
    const uploadUrl = resolveUploadUrl(props.process.widgetData, widgetProxyUrl);
    if (!uploadUrl) return null;
    const { ok: stored, failed } = await uploadFiles(uploadUrl, attachments, maxUploadBytes);
    for (const f of failed) {
      // Surface each failed upload as an immediate, transient error row.
      localSeqRef.current -= 1;
      dispatch({
        ev: { type: 'x-optio-local-error', text: `Upload failed: ${f.name} — ${f.error}`, time: Date.now() },
        seq: localSeqRef.current,
      });
    }
    // Everything failed and no prompt to send → don't send an empty turn.
    if (stored.length === 0 && body.trim() === '') return null;
    return bundleUploadNotice(stored, body);
  }

  // Optimistic local echo: show the operator's text (not the System: preamble)
  // now. It carries the listener's id for the message, so the listener's
  // x-optio-queued for the same id does not add a second bubble; `queued`
  // makes it the Queued bubble until Claude takes it. The wire echo confirms
  // it in place (or moves a queued one to where Claude took it). Negative
  // seqs keep React keys unique and clear of wire seqs.
  function localEcho(text: string, resp: Record<string, unknown>, queued: boolean) {
    localSeqRef.current -= 1;
    dispatch({
      // `time`: the send moment, read here (the view), not by the reducer —
      // shown live under the bubble until the wire echo supplies its own
      // timestamp (Fix 4).
      ev: { type: 'x-optio-local-user', text, id: typeof resp.id === 'string' ? resp.id : undefined, queued, time: Date.now() },
      seq: localSeqRef.current,
    });
  }

  async function onFileDownload(relpath: string, filename: string) {
    try {
      const r = await fetch(`${widgetProxyUrl}download?path=${encodeURIComponent(relpath)}`);
      if (!r.ok) return;
      const mime = r.headers.get('content-type') || 'application/octet-stream';
      const bytes = new Uint8Array(await r.arrayBuffer());
      blobDownload(bytes, mime, filename);
    } catch {
      /* ignore — surfaced to the operator as a non-download */
    }
  }

  // A message sent from the suspended replay waits here for the resumed
  // session (resumeAndSend.ts); one that could not be sent comes back.
  const resumeSend = useResumeSend(props.process._id);

  return (
    <SpinnerScope name={`optio:process:${props.process._id}`}>
      <ControlErrorSlot.Provider value={controlErrorSink}>
        <ConversationView
          todos={todosFromWidgetData(props.process.widgetData)}
          state={state}
          closed={state.closed}
          busy={busy}
          toolVerbosity={toolVerbosity}
          hideEmptyRuns={props.hideEmptyRuns}
          thinkingVerbosity={thinkingVerbosity}
          showFileUpload={showFileUpload}
          maxUploadBytes={maxUploadBytes}
          fileDownload={fileDownload}
          nativeSpinner={nativeSpinner ? <NativeSpinner engine="claudecode" /> : undefined}
          onSend={async (body, attachments) => {
            // The message sent from the replay goes first.
            if (resumeSend?.phase === 'resuming') return { error: WAIT_FOR_RESUME_SEND };
            dismissResumeSend(props.process._id);
            // Send (idle) or Send when ready (busy): the listener answers
            // {id, queued}; queued means Claude holds it until the next tool result.
            const prompt = await preparePrompt(body, attachments);
            if (prompt === null) return false;
            const resp = await postJson('send', { text: prompt });
            if (resp === null) return false;
            localEcho(body, resp, resp.queued === true);
            return true;
          }}
          onSteer={async (body, attachments, upTo) => {
            // Interrupt and send (body), or Send now on a queued bubble (no
            // body, `upTo` the clicked bubble's own id — Fix 13b): POST /steer
            // stops the running step, then Claude runs what it holds up to
            // (and including) `upTo`, and the new text follows.
            const prompt = body === '' && attachments.length === 0 ? '' : await preparePrompt(body, attachments);
            if (prompt === null) return false;
            const resp = await postJson('steer', upTo !== undefined ? { text: prompt, upTo } : { text: prompt });
            if (resp === null) return false;
            localEcho(body, resp, false);
            return true;
          }}
          onInterrupt={() => post('interrupt', {})}
          onPermission={(requestId, behavior) => {
            // Claude Code's can_use_tool schema wants a human-readable reason on
            // deny; send a default so a bare click satisfies it. (The wire also
            // carries a free-form message if a reason field is added later.)
            const body =
              behavior === 'deny'
                ? { request_id: requestId, behavior, message: 'Denied by the operator.' }
                : { request_id: requestId, behavior };
            void post('permission', body);
          }}
          onFileDownload={onFileDownload}
          controls={showSessionControls ? state.controls : undefined}
          onControlChange={async (id, value) => {
            // Optimistic patch through the reducer, then POST /control; the
            // engine switches the running claude live and sends the running
            // values back (after a refusal too). A failure is shown in the input
            // bar's error slot, as for the custom controls.
            localSeqRef.current -= 1;
            dispatch({ ev: { type: 'x-optio-control-update', id, value }, seq: localSeqRef.current });
            controlErrorSink.current?.(null);
            const result = await postForResult('control', { id, value });
            if (!result?.ok) controlErrorSink.current?.(controlFailure(result?.body ?? null));
          }}
          customControls={state.customControls}
          onCustomControlChange={async (id, value) => {
            // As for the built-ins: an optimistic patch through the reducer, then
            // POST /control (id `custom:<id>`); the engine's control update is
            // the source of truth. A failed pick is put back here to the value
            // the engine last sent (a refusal for a closed conversation, a bad
            // id or a lost answer is followed by no update), unless the engine
            // sent one since the pick started: that one stands.
            const wireId = `${CUSTOM_CONTROL_PREFIX}${id}`;
            const updatesBefore = engineCustom.updates.get(id) ?? 0;
            localSeqRef.current -= 1;
            dispatch({ ev: { type: 'x-optio-control-update', id: wireId, value }, seq: localSeqRef.current });
            const result = await postForResult('control', { id: wireId, value });
            if (result?.ok) return null;
            if ((engineCustom.updates.get(id) ?? 0) === updatesBefore) {
              localSeqRef.current -= 1;
              dispatch({
                ev: { type: 'x-optio-control-update', id: wireId, value: engineCustom.values.get(id) ?? null },
                seq: localSeqRef.current,
              });
            }
            return controlFailure(result?.body ?? null);
          }}
          notice={<ResumeSendNotice processId={props.process._id} state={resumeSend} />}
          draft={resumeSend?.phase === 'failed' ? resumeSend.text : undefined}
          themeMode={(props as any).themeMode}
          onToggleTheme={(props as any).onToggleTheme}
        />
      </ControlErrorSlot.Provider>
    </SpinnerScope>
  );
}
