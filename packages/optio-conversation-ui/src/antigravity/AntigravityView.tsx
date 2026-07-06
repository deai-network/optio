import { useEffect, useReducer, useRef } from 'react';
import type { WidgetProps } from 'optio-ui';
import type { ChatState, SessionControl } from '../chat.js';
import { initialChatState, reduceAntigravityEvent } from './events.js';
import { type Attachment } from '../attachments.js';
import { blobDownload } from '../FileDownloadContext.js';
import { ConversationView } from '../ConversationView.js';

// Conversation view for antigravity tasks. Antigravity has NO live transport
// (design §1) — a conversation is SYNTHESISED from repeated one-shot `agy -p`
// turns whose events are read from the transcript file. The per-task
// conversation listener tails that transcript and re-emits each raw line over
// SSE ({widgetProxyUrl}events); this view reduces those objects into the shared
// ChatState and hands all rendering + local UI to the shared ConversationView.
// A thin transport adapter — only the wire (antigravity's transcript events
// over the listener) differs from ClaudeCodeView / GrokView.
//
// Antigravity parity notes (design §7): the model control switches by RESTART
// — agy has no inline switch, so the next `agy -p` turn simply carries the new
// --model (the conversation's set_control stores it); from this view the change
// is the same optimistic fold + POST /control every engine uses. Turns run
// --dangerously-skip-permissions, so no permission card ever appears (the
// onPermission wiring is a parity seam). Uploads/downloads mirror grok: no
// headless inline ingest, so a file lands in the workdir via /upload and the
// next prompt references it by a System: notice; deliverables map to /download.

interface ChatAction {
  ev: unknown;
  seq: number;
}

function chatReducer(state: ChatState, action: ChatAction): ChatState {
  return reduceAntigravityEvent(state, action.ev, action.seq);
}

export function AntigravityView(props: WidgetProps) {
  const wd = (props.process.widgetData as any) ?? {};
  const toolVerbosity = (wd.toolVerbosity ?? 'description-only') as
    'silent' | 'description-only' | 'verbose';
  const thinkingVerbosity = (wd.thinkingVerbosity ?? 'hidden') as 'hidden' | 'visible';
  // Seed the reducer's controls from widgetData so the session-controls bar
  // renders the model selector from the first paint; live updates fold in via
  // the reducer's x-optio-control-update case.
  const initialControls = (wd.controls ?? []) as SessionControl[];
  const showSessionControls = Boolean(wd.showSessionControls);
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
    console.info('[optio-conversation-ui] antigravity conversation widget activated:', `${widgetProxyUrl}events`);
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
  // send, the turn's `assistant` transcript line clears it.
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

  // Antigravity has no headless inline ingest, so uploads land in the session
  // workdir via the listener's multipart POST /upload; the next prompt then
  // references them by path. Returns the stored relpaths, or null on failure.
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
      onSend={async (body, attachments) => {
        // With attachments, upload first, then bundle one System: notice per
        // stored file into the prompt so agy reads them from the workdir with
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
          // Optimistic local echo: show the message now, before the transcript
          // replays its `user` line (the reducer dedupes the two). Negative seqs
          // keep React keys clear of wire seqs.
          localSeqRef.current -= 1;
          dispatch({ ev: { type: 'x-optio-local-user', text: body }, seq: localSeqRef.current });
        }
        return ok;
      }}
      onInterrupt={() => void post('interrupt', {})}
      onPermission={(requestId, behavior) => {
        // Parity seam only — antigravity turns run skip-permissions, so this
        // never fires; kept for cross-engine symmetry.
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
              // Optimistic fold via the reducer, then push the change to the
              // listener's /control route. agy switches model by RESTART (no
              // inline switch): the next `agy -p` turn carries the new --model.
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
