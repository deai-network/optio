import { useEffect, useReducer, useRef } from 'react';
import type { WidgetProps } from 'optio-ui';
import type { ChatState, SessionControl } from '../chat.js';
import { initialChatState, reduceCodexEvent } from './events.js';
import { type Attachment } from '../attachments.js';
import { blobDownload } from '../FileDownloadContext.js';
import { ConversationView } from '../ConversationView.js';
import { NativeSpinner } from '../spinners/NativeSpinner.js';

// Conversation view for codex tasks: speaks the codex app-server stream
// (JSON-RPC 2.0 over stdio, threads/turns/items) through the per-task
// conversation listener (SSE from `{widgetProxyUrl}events`), reduces the raw
// app-server notifications into the shared ChatState, and hands all rendering
// + local UI to the shared ConversationView. A thin transport adapter — only
// the wire (codex's app-server over the listener) differs from ClaudeCodeView.
// Model switching is INLINE (the chosen model rides the next turn/start — no
// restart); file upload lands in the workdir and the next prompt references
// it; file download streams a sentinel-linked artifact back as a blob save.

interface ChatAction {
  ev: unknown;
  seq: number;
}

function chatReducer(state: ChatState, action: ChatAction): ChatState {
  return reduceCodexEvent(state, action.ev, action.seq);
}

export function CodexView(props: WidgetProps) {
  const wd = (props.process.widgetData as any) ?? {};
  const toolVerbosity = (wd.toolVerbosity ?? 'description-only') as
    'silent' | 'description-only' | 'verbose';
  const thinkingVerbosity = (wd.thinkingVerbosity ?? 'hidden') as 'hidden' | 'visible';
  const nativeSpinner = Boolean(wd.nativeSpinner);
  // Seed the reducer's controls from widgetData (the id="model" SessionControl
  // codex emits); live value changes fold in via x-optio-control-update.
  const initialControls = (wd.controls ?? []) as SessionControl[];
  const [state, dispatch] = useReducer(chatReducer, {
    ...initialChatState,
    controls: initialControls,
  });
  const localSeqRef = useRef(0);
  const showFileUpload = Boolean(wd.showFileUpload);
  const maxUploadBytes = Number(wd.maxUploadBytes ?? 10_000_000);
  const fileDownload = Boolean(wd.fileDownload);

  const { widgetProxyUrl } = props; // ends with '/' — trailing slash is load-bearing

  useEffect(() => {
    console.info('[optio-conversation-ui] codex conversation widget activated:', `${widgetProxyUrl}events`);
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
  // send, the turn/completed notification clears it.
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

  // Codex has no headless inline ingest, so uploads land in the session workdir
  // via the listener's multipart POST /upload; the next prompt then references
  // them by path. Returns the stored relpaths, or null on any failure.
  async function uploadFiles(atts: Attachment[]): Promise<string[] | null> {
    const fd = new FormData();
    for (const a of atts) fd.append('file', a.file, a.filename);
    try {
      const resp = await fetch(`${widgetProxyUrl}upload`, { method: 'POST', body: fd });
      if (!resp.ok) return null;
      const j = await resp.json();
      return (j.files ?? []).map((f: any) => String(f.path));
    } catch {
      return null;
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
      nativeSpinner={nativeSpinner ? <NativeSpinner engine="codex" /> : undefined}
      onSend={async (body, attachments) => {
        // With attachments, upload first, then bundle one System: notice per
        // stored file into the prompt so codex reads them from the workdir with
        // its own tools. The optimistic echo still shows the operator's text.
        let prompt = body;
        if (attachments.length > 0) {
          const paths = await uploadFiles(attachments);
          if (!paths) return false;
          const notice = paths.map((p) => `System: upload received, stored in ${p}`).join('\n');
          prompt = `${notice}\n\n${body}`;
        }
        const ok = await post('send', { text: prompt });
        if (ok) {
          // Optimistic local echo: show the message now (codex emits no user
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
      controls={state.controls}
      onControlChange={(id, value) => {
        // Optimistic local fold, then POST /control. Codex switches the model
        // INLINE (the chosen model rides the next turn/start — no restart).
        localSeqRef.current -= 1;
        dispatch({ ev: { type: 'x-optio-control-update', id, value }, seq: localSeqRef.current });
        void post('control', { id, value });
      }}
      themeMode={(props as any).themeMode}
      onToggleTheme={(props as any).onToggleTheme}
    />
  );
}
