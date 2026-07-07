import { useEffect, useReducer, useRef } from 'react';
import type { WidgetProps } from 'optio-ui';
import type { ChatState, SessionControl } from '../chat.js';
import { initialChatState, reduceEvent } from './events.js';
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
    'silent' | 'description-only' | 'verbose';
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
      nativeSpinner={nativeSpinner ? <NativeSpinner engine="claudecode" /> : undefined}
      onSend={async (body, attachments) => {
        // When files are attached, upload them through the generic route first,
        // then bundle one `System:` notice line per stored file into the prompt
        // so the agent can Read them from the workdir. The optimistic echo still
        // shows the operator's text (`body`), not the System: preamble.
        let prompt = body;
        if (attachments.length > 0) {
          const uploadUrl = resolveUploadUrl(props.process.widgetData, widgetProxyUrl);
          if (!uploadUrl) return false;
          const paths = await uploadFiles(uploadUrl, attachments, maxUploadBytes);
          if (!paths) return false;
          prompt = bundleUploadNotice(paths, body);
        }
        const ok = await post('send', { text: prompt });
        if (ok) {
          // Optimistic local echo: show the message now; the wire echo (which
          // only arrives once the answer starts streaming) confirms it in place.
          // Negative seqs keep React keys unique and clear of wire seqs.
          localSeqRef.current -= 1;
          dispatch({ ev: { type: 'x-optio-local-user', text: body }, seq: localSeqRef.current });
        }
        return ok;
      }}
      onInterrupt={() => void post('interrupt', {})}
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
