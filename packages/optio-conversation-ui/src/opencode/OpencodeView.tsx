import { useEffect, useReducer, useRef, useState } from 'react';
import { Select } from 'antd';
import { isTerminalState } from 'optio-ui';
import type { WidgetProps } from 'optio-ui';
import type { ChatItem, ChatState } from '../chat.js';
import { initialChatState } from '../chat.js';
import {
  historyToChatItems, reduceOpencodeEvent,
  parseProviders, lastModelFromHistory,
  type OpencodeModel, type ModelGroup,
} from './events.js';
import { type Attachment, readAsDataUrl } from '../attachments.js';
import { blobDownload } from '../FileDownloadContext.js';
import { ConversationView } from '../ConversationView.js';

// Conversation view for opencode tasks: speaks opencode's native HTTP+SSE API
// through the widget proxy (exactly like iframe mode does), reduces the wire
// events into the shared ChatState, and hands all rendering + local UI to the
// shared ConversationView. This view is a thin adapter — only the transport
// (the opencode HTTP+SSE wire) differs from ClaudeCodeView.

interface OpencodeWidgetData {
  sessionID?: string;
  directory?: string;
  toolVerbosity?: 'silent' | 'description-only' | 'verbose';
  showModelSelector?: boolean;
  defaultModel?: string; // "providerID/modelID"
}

type ChatAction = { kind: 'bootstrap'; items: ChatItem[] } | { ev: unknown; seq: number };

export function OpencodeView(props: WidgetProps) {
  // Gate on widgetData like IframeWidget does — the session id and directory
  // are the transport's addressing and arrive with the widget data.
  const widgetData = (props.process?.widgetData ?? undefined) as OpencodeWidgetData | undefined;
  if (!widgetData?.sessionID) {
    return <div data-testid="optio-widget-loading">Loading…</div>;
  }
  return (
    <OpencodeChat
      {...props}
      sessionID={widgetData.sessionID}
      directory={widgetData.directory ?? ''}
      showModelSelector={widgetData.showModelSelector ?? false}
      defaultModel={widgetData.defaultModel}
    />
  );
}

function OpencodeChat(
  props: WidgetProps & { sessionID: string; directory: string; showModelSelector: boolean; defaultModel?: string },
) {
  const { sessionID, directory, widgetProxyUrl, showModelSelector, defaultModel } = props; // widgetProxyUrl ends with '/' — trailing slash is load-bearing
  const toolVerbosity = ((props.process.widgetData as any)?.toolVerbosity ?? 'description-only') as
    'silent' | 'description-only' | 'verbose';
  const showFileUpload = Boolean((props.process.widgetData as any)?.showFileUpload);
  const maxUploadBytes = Number((props.process.widgetData as any)?.maxUploadBytes ?? 10_000_000);
  const fileDownload = Boolean((props.process.widgetData as any)?.fileDownload);
  // opencode routes resolve their project instance from the request's
  // location context — every session-scoped call carries ?directory=.
  const q = `?directory=${encodeURIComponent(directory)}`;

  const [state, dispatch] = useReducer(
    (s: ChatState, action: ChatAction): ChatState => {
      if ('kind' in action && action.kind === 'bootstrap') {
        return { ...s, items: action.items };
      }
      const { ev, seq } = action as { ev: unknown; seq: number };
      return reduceOpencodeEvent(s, ev, seq, sessionID);
    },
    initialChatState,
  );
  const seqRef = useRef(0);
  const localSeqRef = useRef(-1);
  const [groups, setGroups] = useState<ModelGroup[]>([]);
  const [currentModel, setCurrentModel] = useState<OpencodeModel | null>(null);

  // "Session ended": the opencode server (and the proxy route to it) dies with
  // the task process, so closed derives from the process document's terminal
  // state — same predicate IframeWidget uses for its banner.
  const closed = state.closed || isTerminalState(props.process?.status?.state);

  // busy is purely reducer-driven: the optimistic local-user echo sets it on
  // send, session.status busy/idle tracks the turn from the wire.
  const busy = state.busy;

  // Bootstrap: subscribe FIRST and buffer, then fetch history and reconcile —
  // events that arrive between the two calls are replayed after the bootstrap
  // (the spec's history-then-subscribe race fix). Live events come from
  // /global/event: the per-instance /event?directory=… stream ends right
  // after server.connected (observed on 1.14.45) and is unusable.
  useEffect(() => {
    console.info('[optio-conversation-ui] opencode conversation view activated:', `${widgetProxyUrl}global/event`);
    let bootstrapped = false;
    const buffer: any[] = [];
    const es = new EventSource(`${widgetProxyUrl}global/event`);
    es.onmessage = (msg) => {
      let ev: any;
      try { ev = JSON.parse(msg.data); } catch { return; }
      if (!bootstrapped) { buffer.push(ev); return; }
      dispatch({ ev, seq: seqRef.current++ });
    };
    void (async () => {
      try {
        const resp = await fetch(`${widgetProxyUrl}session/${sessionID}/message${q}`);
        const history = resp.ok ? await resp.json() : [];
        dispatch({ kind: 'bootstrap', items: historyToChatItems(history, sessionID) });
      } finally {
        bootstrapped = true;
        for (const ev of buffer) dispatch({ ev, seq: seqRef.current++ });
      }
    })();
    return () => es.close();
  }, [widgetProxyUrl, sessionID]);

  // Discover models and resolve the initial sticky model once. Runs alongside
  // the bootstrap; its failure never blocks the chat (groups stays empty,
  // currentModel may stay null → sends omit `model`, opencode uses its default).
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      let parsed: { groups: ModelGroup[]; defaultModel: OpencodeModel | null } = { groups: [], defaultModel: null };
      let history: any[] = [];
      try {
        const r = await fetch(`${widgetProxyUrl}config/providers${q}`);
        if (r.ok) parsed = parseProviders(await r.json());
      } catch { /* non-fatal */ }
      try {
        const r = await fetch(`${widgetProxyUrl}session/${sessionID}/message${q}`);
        if (r.ok) history = await r.json();
      } catch { /* non-fatal */ }
      if (cancelled) return;
      setGroups(parsed.groups);
      const inList = (m: OpencodeModel) =>
        parsed.groups.some((g) => g.models.some((x) => x.providerID === m.providerID && x.modelID === m.modelID));
      // (1) history-last → (2) validated defaultModel → (3) providers default → (4) null
      const fromHistory = lastModelFromHistory(history);
      let resolved: OpencodeModel | null = fromHistory;
      if (!resolved && defaultModel) {
        const [providerID, modelID] = defaultModel.split('/');
        const cand = { providerID, modelID };
        if (providerID && modelID && inList(cand)) resolved = cand;
      }
      if (!resolved) resolved = parsed.defaultModel;
      setCurrentModel(resolved);
    })();
    return () => { cancelled = true; };
  }, [widgetProxyUrl, sessionID]);

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

  async function onSend(body: string, attachments: Attachment[]): Promise<boolean> {
    const fileParts = await Promise.all(
      attachments.map(async (a) => ({
        type: 'file' as const, mime: a.mime, filename: a.filename, url: await readAsDataUrl(a.file),
      })),
    );
    const promptBody: any = { parts: [...fileParts, { type: 'text', text: body }] };
    if (currentModel) promptBody.model = currentModel;
    const ok = await post(`session/${sessionID}/prompt_async${q}`, promptBody);
    if (ok) {
      // Optimistic local echo: show the message now; the wire echo
      // (message.updated role=user) confirms it in place. Negative seqs keep
      // React keys unique and clear of wire seqs.
      dispatch({ ev: { type: 'x-optio-local-user', properties: { text: body } }, seq: localSeqRef.current-- });
    }
    return ok;
  }

  function onInterrupt() {
    void post(`session/${sessionID}/abort${q}`, {});
  }

  function onPermission(requestId: string, behavior: 'allow' | 'deny') {
    // Spec mapping: allow → reply "once"; deny → reply "reject" with a
    // human-readable message. The permission.replied wire event flips the
    // card's answered state.
    const body =
      behavior === 'deny'
        ? { reply: 'reject', message: 'Denied by the operator.' }
        : { reply: 'once' };
    void post(`permission/${requestId}/reply${q}`, body);
  }

  async function onFileDownload(relpath: string, filename: string) {
    const r = await fetch(`${widgetProxyUrl}file/content?path=${encodeURIComponent(relpath)}${q.slice(1) ? '&' + q.slice(1) : ''}`);
    if (!r.ok) return;
    const fc = await r.json();                       // FileContent {type, content}
    const mime = 'application/octet-stream';
    const bytes = fc.type === 'binary'
      ? Uint8Array.from(atob(fc.content), (c) => c.charCodeAt(0))
      : new TextEncoder().encode(fc.content ?? '');
    blobDownload(bytes, mime, filename);
  }

  return (
    <ConversationView
      state={state}
      closed={closed}
      busy={busy}
      toolVerbosity={toolVerbosity}
      showFileUpload={showFileUpload}
      maxUploadBytes={maxUploadBytes}
      fileDownload={fileDownload}
      onSend={onSend}
      onInterrupt={onInterrupt}
      onPermission={onPermission}
      onFileDownload={onFileDownload}
      modelSelector={showModelSelector ? (
        <Select
          data-testid="model-select"
          size="small"
          style={{ minWidth: 180, alignSelf: 'center' }}
          placeholder="Model"
          disabled={busy || closed}
          value={currentModel ? `${currentModel.providerID}/${currentModel.modelID}` : undefined}
          onChange={(v: string) => {
            const [providerID, modelID] = v.split('/');
            setCurrentModel({ providerID, modelID });
          }}
          options={groups.map((g) => ({
            label: g.providerName,
            options: g.models.map((m) => ({
              label: m.label,
              value: `${m.providerID}/${m.modelID}`,
            })),
          }))}
        />
      ) : undefined}
      themeMode={(props as any).themeMode}
      onToggleTheme={(props as any).onToggleTheme}
    />
  );
}
