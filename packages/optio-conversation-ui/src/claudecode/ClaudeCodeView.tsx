import { useEffect, useReducer, useRef, useState } from 'react';
import { Select } from 'antd';
import type { WidgetProps } from 'optio-ui';
import type { ChatState } from '../chat.js';
import { initialChatState, reduceEvent } from './events.js';
import { type Attachment } from '../attachments.js';
import { blobDownload } from '../FileDownloadContext.js';
import { ConversationView } from '../ConversationView.js';

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
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const localSeqRef = useRef(0);
  const [currentModel, setCurrentModel] = useState<string | undefined>(
    (props.process.widgetData as any)?.currentModel ?? undefined,
  );
  const showModelSelector = Boolean((props.process.widgetData as any)?.showModelSelector);
  const models: { id: string; label: string; disabled?: boolean }[] =
    (props.process.widgetData as any)?.models ?? [];
  const showFileUpload = Boolean((props.process.widgetData as any)?.showFileUpload);
  const maxUploadBytes = Number((props.process.widgetData as any)?.maxUploadBytes ?? 10_000_000);
  const fileDownload = Boolean((props.process.widgetData as any)?.fileDownload);

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
      dispatch({ ev: parsed, seq: Number(ev.lastEventId) });
      // The picker shows empty when the task was launched with no --model
      // (config.model is None). Claude Code still runs on its built-in default;
      // surface the real model from the stream so the picker reflects what's
      // actually in use. The model arrives at top level on the `system`/`init`
      // event (fires immediately at launch, before any turn) and on each
      // assistant message's `message.model`. Only fill when unset — a user pick
      // wins.
      const rawModel = (parsed as any)?.model ?? (parsed as any)?.message?.model;
      if (typeof rawModel === 'string' && rawModel) {
        // The stream reports the runtime/variant id (e.g. claude-opus-4-8[1m]
        // for the 1M-context variant), but /v1/models — and our picker options
        // — use the bare catalog id (claude-opus-4-8). Strip the [..] variant
        // suffix so the value matches an option and the label resolves.
        const streamModel = rawModel.replace(/\[[^\]]*\]$/, '');
        setCurrentModel((prev) => prev ?? streamModel);
      }
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

  // Claude Code uploads attachments into the session workdir via the listener's
  // multipart POST /upload (not the JSON `post` helper). Returns the stored
  // relpaths, or null on any failure so send() can surface a retry.
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
        // When files are attached, upload them first, then bundle one `System:`
        // notice line per stored file into the prompt so the agent can Read them
        // from the workdir. The optimistic echo still shows the operator's text
        // (`body`), not the System: preamble.
        let prompt = body;
        if (attachments.length > 0) {
          const paths = await uploadFiles(attachments);
          if (!paths) return false;
          const notice = paths.map((p) => `System: upload received, stored in ${p}`).join('\n');
          prompt = `${notice}\n\n${body}`;
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
      modelSelector={
        showModelSelector ? (
          <Select
            data-testid="model-select"
            size="small"
            style={{ minWidth: 180, alignSelf: 'center' }}
            placeholder="Model"
            disabled={busy || state.closed}
            value={currentModel}
            onChange={(v: string) => {
              setCurrentModel(v); // optimistic
              void post('model', { model: v }); // engine relaunches
            }}
            options={models.map((m) => ({ label: m.label, value: m.id, disabled: m.disabled }))}
          />
        ) : undefined
      }
      themeMode={(props as any).themeMode}
      onToggleTheme={(props as any).onToggleTheme}
    />
  );
}
