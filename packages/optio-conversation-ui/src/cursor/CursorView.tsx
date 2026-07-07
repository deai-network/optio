import { useEffect, useReducer, useRef } from 'react';
import type { WidgetProps } from 'optio-ui';
import type { ChatState, SessionControl } from '../chat.js';
import { initialChatState, reduceCursorEvent } from './events.js';
import { resolveUploadUrl, uploadFiles, bundleUploadNotice } from '../uploads.js';
import { blobDownload } from '../FileDownloadContext.js';
import { ConversationView } from '../ConversationView.js';
import { NativeSpinner } from '../spinners/NativeSpinner.js';

// Conversation view for cursor tasks: speaks ACP (JSON-RPC 2.0) through the
// per-task conversation listener (SSE from `{widgetProxyUrl}events`), reduces
// the raw ACP objects into the shared ChatState, and hands all rendering +
// local UI to the shared ConversationView. A thin transport adapter over the
// same shared ACP reducer GrokView uses — only the wire (cursor's ACP over the
// listener) differs. Stage 7 brings file upload (System: reference — headless
// cursor has no inline ingest) and file download (workdir-confined GET
// /download for the optio-file: sentinel) to parity.

interface ChatAction {
  ev: unknown;
  seq: number;
}

function chatReducer(state: ChatState, action: ChatAction): ChatState {
  return reduceCursorEvent(state, action.ev, action.seq);
}

export function CursorView(props: WidgetProps) {
  const wd = (props.process.widgetData as any) ?? {};
  const toolVerbosity = (wd.toolVerbosity ?? 'description-only') as
    'silent' | 'description-only' | 'verbose';
  const thinkingVerbosity = (wd.thinkingVerbosity ?? 'hidden') as 'hidden' | 'visible';
  const nativeSpinner = Boolean(wd.nativeSpinner);
  // Seed the reducer with the engine-neutral session controls (the model
  // picker) from widgetData; live changes fold through the shared ACP reducer.
  const initialControls = (wd.controls ?? []) as SessionControl[];
  const [state, dispatch] = useReducer(
    chatReducer,
    initialControls,
    (controls) => ({ ...initialChatState, controls }),
  );
  const localSeqRef = useRef(0);
  const showFileUpload = Boolean(wd.showFileUpload);
  const maxUploadBytes = Number(wd.maxUploadBytes ?? 10_000_000);
  const fileDownload = Boolean(wd.fileDownload);
  const showSessionControls = Boolean(wd.showSessionControls);

  const { widgetProxyUrl } = props; // ends with '/' — trailing slash is load-bearing

  useEffect(() => {
    console.info('[optio-conversation-ui] cursor conversation widget activated:', `${widgetProxyUrl}events`);
    const es = new EventSource(`${widgetProxyUrl}events`);
    es.onmessage = (ev: MessageEvent) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(ev.data);
      } catch {
        return;
      }
      dispatch({ ev: parsed, seq: Number(ev.lastEventId) });
    };
    return () => es.close();
  }, [widgetProxyUrl]);

  // busy is purely reducer-driven: the optimistic local-user echo sets it on
  // send, the session/prompt turn-end response clears it.
  const busy = state.busy;

  async function post(path: string, body: unknown): Promise<boolean> {
    try {
      const resp = await fetch(`${widgetProxyUrl}${path}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      return resp.ok;
    } catch {
      return false;
    }
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
      nativeSpinner={nativeSpinner ? <NativeSpinner engine="cursor" /> : undefined}
      onSend={async (body, attachments) => {
        // With attachments, upload through the generic route first, then bundle
        // one System: notice per stored file into the prompt so cursor reads
        // them from the workdir with its own tools. The optimistic echo still
        // shows the operator's text.
        let prompt = body;
        if (attachments.length > 0) {
          const uploadUrl = resolveUploadUrl(props.process.widgetData, widgetProxyUrl);
          if (!uploadUrl) return false;
          const { ok: stored, failed } = await uploadFiles(uploadUrl, attachments, maxUploadBytes);
          for (const f of failed) {
            // Surface each failed upload as an immediate, transient error row.
            localSeqRef.current -= 1;
            dispatch({ ev: { type: 'x-optio-local-error', text: `Upload failed: ${f.name} — ${f.error}` }, seq: localSeqRef.current });
          }
          // Everything failed and no prompt to send → don't send an empty turn.
          if (stored.length === 0 && body.trim() === '') return false;
          prompt = bundleUploadNotice(stored, body);
        }
        const ok = await post('send', { text: prompt });
        if (ok) {
          // Optimistic local echo: show the message now (cursor emits no user
          // echo of its own). Negative seqs keep React keys clear of wire seqs.
          localSeqRef.current -= 1;
          dispatch({ ev: { type: 'x-optio-local-user', text: body }, seq: localSeqRef.current });
        }
        return ok;
      }}
      onInterrupt={() => void post('interrupt', {})}
      onPermission={(requestId, behavior) => {
        const body =
          behavior === 'deny'
            ? { request_id: requestId, behavior, message: 'Denied by the operator.' }
            : { request_id: requestId, behavior };
        void post('permission', body);
      }}
      onFileDownload={onFileDownload}
      controls={showSessionControls ? state.controls : undefined}
      onControlChange={
        showSessionControls
          ? (id, value) => {
              // Optimistic patch through the shared ACP reducer, then push the
              // change to the listener (model → INLINE session/set_model).
              localSeqRef.current -= 1;
              dispatch({ ev: { type: 'x-optio-control-update', id, value }, seq: localSeqRef.current });
              void post('control', { id, value });
            }
          : undefined
      }
      themeMode={(props as any).themeMode}
      onToggleTheme={(props as any).onToggleTheme}
    />
  );
}
