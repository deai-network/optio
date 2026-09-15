import { useEffect, useReducer, useRef } from 'react';
import type { WidgetProps } from 'optio-ui';
import type { ChatState, SessionControl } from '../chat.js';
import { queuedIdsAfter } from '../chat.js';
import { initialChatState, reduceEvent } from './events.js';
import type { Attachment } from '../attachments.js';
import { resolveUploadUrl, uploadFiles, bundleUploadNotice } from '../uploads.js';
import { blobDownload } from '../FileDownloadContext.js';
import { ConversationView } from '../ConversationView.js';
import { NativeSpinner } from '../spinners/NativeSpinner.js';

interface ChatAction {
  ev: unknown;
  seq: number;
}

function chatReducer(state: ChatState, action: ChatAction): ChatState {
  return reduceEvent(state, action.ev, action.seq);
}

export function ClaudeCodeView(props: WidgetProps) {
  const toolVerbosity = ((props.process.widgetData as any)?.toolVerbosity ?? 'description-only') as
    'silent' | 'description-while-active' | 'description-only' | 'verbose';
  const thinkingVerbosity = ((props.process.widgetData as any)?.thinkingVerbosity ?? 'hidden') as
    'hidden' | 'visible';
  const initialControls = ((props.process.widgetData as any)?.controls ?? []) as SessionControl[];
  const showSessionControls = Boolean((props.process.widgetData as any)?.showSessionControls);
  const [state, dispatch] = useReducer(chatReducer, { ...initialChatState, controls: initialControls });
  const localSeqRef = useRef(0);
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
      // The reducer sniffs the runtime model from system/init & message.model
      // and folds it into the model control (only while the control has no
      // value yet — an operator pick wins).
      dispatch({ ev: parsed, seq: Number(ev.lastEventId) });
    };
    return () => es.close();
  }, [widgetProxyUrl]);

  // The optimistic local-user echo (dispatched on a successful send) sets
  // state.busy immediately, so busy is purely reducer-driven — no separate
  // send flag that a busy-change effect could fail to clear on a mid-turn send.
  const busy = state.busy;

  // POST a JSON body. Returns the parsed response on 2xx ({} when the body is
  // not a JSON object), null on failure.
  async function postJson(path: string, body: unknown): Promise<Record<string, unknown> | null> {
    try {
      const resp = await fetch(`${widgetProxyUrl}${path}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!resp.ok) return null;
      try {
        const parsed: unknown = await resp.json();
        return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {};
      } catch {
        return {};
      }
    } catch {
      return null;
    }
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

  return (
    <ConversationView
      state={state}
      closed={state.closed}
      busy={busy}
      toolVerbosity={toolVerbosity}
      thinkingVerbosity={thinkingVerbosity}
      showFileUpload={showFileUpload}
      maxUploadBytes={maxUploadBytes}
      fileDownload={fileDownload}
      nativeSpinner={nativeSpinner ? <NativeSpinner engine="claudecode" /> : undefined}
      onSend={async (body, attachments) => {
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
        if (upTo !== undefined) {
          // Review of fix 13b, finding 4: tell the reducer, before the POST,
          // which OTHER currently-queued bubbles this "Send now up to" is
          // about to have the CLI cancel and steering.py re-send under new
          // uuids — so their command_lifecycle 'cancelled' (which arrives
          // well before x-optio-requeued restores them, up to
          // turn_end_timeout_s on the timeout path) never flashes them into
          // a muted "Not delivered" note.
          const laterIds = queuedIdsAfter(state.items, upTo);
          if (laterIds.length > 0) {
            localSeqRef.current -= 1;
            dispatch({ ev: { type: 'x-optio-pending-requeue', ids: laterIds }, seq: localSeqRef.current });
          }
        }
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
      onControlChange={(id, value) => {
        // Optimistic patch through the reducer, then POST /control; a model
        // change makes the engine relaunch claude (restart-based).
        localSeqRef.current -= 1;
        dispatch({ ev: { type: 'x-optio-control-update', id, value }, seq: localSeqRef.current });
        void post('control', { id, value });
      }}
      themeMode={(props as any).themeMode}
      onToggleTheme={(props as any).onToggleTheme}
    />
  );
}
