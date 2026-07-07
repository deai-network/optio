import { useEffect, useReducer, useRef, useState } from 'react';
import { isTerminalState } from 'optio-ui';
import type { WidgetProps } from 'optio-ui';
import type { ChatItem, ChatState, SessionControl } from '../chat.js';
import { initialChatState } from '../chat.js';
import {
  historyToChatItems, reduceOpencodeEvent,
  parseProviders, lastModelFromHistory,
  type OpencodeModel, type ModelGroup,
} from './events.js';
import { type Attachment } from '../attachments.js';
import { resolveUploadUrl, uploadFiles, bundleUploadNotice } from '../uploads.js';
import { blobDownload } from '../FileDownloadContext.js';
import { ConversationView } from '../ConversationView.js';
import { NativeSpinner } from '../spinners/NativeSpinner.js';

// Build the generic model SessionControl from opencode's provider catalog.
// opencode's provider options are GROUPED per provider; the engine-neutral
// `select` control is single-level, so we flatten — prefixing the label with
// the provider name only when more than one provider is present (to keep the
// common single-provider label clean). Grouped optgroup support is out of scope
// (YAGNI). The value encodes "providerID/modelID" (what prompt_async wants).
function modelControlFromGroups(
  groups: ModelGroup[], current: OpencodeModel | null,
  disabledModels: Record<string, string> = {},
): SessionControl {
  const options = groups.flatMap((g) =>
    g.models.map((m) => {
      const value = `${m.providerID}/${m.modelID}`;
      const reason = disabledModels[value];
      // A model the startup probe found unusable (the account can't run it) is
      // greyed with the reason as its hover tooltip — the picker won't let it
      // be selected. The disabled-map rides widgetData (server-side probe).
      return reason
        ? {
            value,
            label: groups.length > 1 ? `${g.providerName} / ${m.label}` : m.label,
            disabled: true, whyDisabled: reason,
          }
        : {
            value,
            label: groups.length > 1 ? `${g.providerName} / ${m.label}` : m.label,
          };
    }),
  );
  return {
    id: 'model', kind: 'select', label: 'Model', category: 'model',
    value: current ? `${current.providerID}/${current.modelID}` : '',
    options,
  };
}

// The reasoning-effort SessionControl ("thought level"). opencode grades effort
// per-prompt via a model's named `variant` (attached to prompt_async, client-
// side, exactly like the model). Its levels are the current model's variant
// keys; the slider value is the chosen level (defaults to the first). Only built
// when the model actually has variants — an unsupported model has no control.
function effortControlFromVariants(levels: string[], current: string | null): SessionControl {
  return {
    id: 'reasoning_effort', kind: 'slider', label: 'Effort', category: 'thought_level',
    value: current && levels.includes(current) ? current : (levels[0] ?? ''),
    levels,
  };
}

// The control snapshot for a given model: always the model select, plus the
// effort slider WHEN the current model exposes variants (re-derived on model
// change so effort presence/levels follow the model — the reactive path the
// x-optio-control-update snapshot drives through the shared reducer).
function buildControls(
  groups: ModelGroup[], current: OpencodeModel | null,
  disabledModels: Record<string, string>,
  modelVariants: Record<string, string[]>,
  currentEffort: string | null,
): SessionControl[] {
  const controls: SessionControl[] = [modelControlFromGroups(groups, current, disabledModels)];
  const key = current ? `${current.providerID}/${current.modelID}` : '';
  const levels = modelVariants[key];
  if (levels && levels.length > 0) {
    controls.push(effortControlFromVariants(levels, currentEffort));
  }
  return controls;
}

// The effort level to apply for a model + chosen level: the chosen level if the
// model offers it, else the model's first variant, else null (no variants).
function effortForModel(
  current: OpencodeModel | null, modelVariants: Record<string, string[]>,
  chosen: string | null,
): string | null {
  const key = current ? `${current.providerID}/${current.modelID}` : '';
  const levels = modelVariants[key] ?? [];
  if (levels.length === 0) return null;
  return chosen && levels.includes(chosen) ? chosen : levels[0];
}

// Conversation view for opencode tasks: speaks opencode's native HTTP+SSE API
// through the widget proxy (exactly like iframe mode does), reduces the wire
// events into the shared ChatState, and hands all rendering + local UI to the
// shared ConversationView. This view is a thin adapter — only the transport
// (the opencode HTTP+SSE wire) differs from ClaudeCodeView.

interface OpencodeWidgetData {
  sessionID?: string;
  directory?: string;
  toolVerbosity?: 'silent' | 'description-only' | 'verbose';
  showSessionControls?: boolean;
  defaultModel?: string; // "providerID/modelID"
  // Models the server-side startup probe found unusable, id → reason. The
  // picker greys these with the reason as a tooltip.
  disabledModels?: Record<string, string>;
  // Per-model reasoning-effort variants ("providerID/modelID" → ordered
  // variant keys). The effort slider is built from the CURRENT model's entry;
  // a model absent here (or with an empty list) gets no effort control.
  modelVariants?: Record<string, string[]>;
  // Initial effort ("thought level") — the effort slider seeds from it when the
  // current model offers it as a variant (client-side, like defaultModel).
  defaultEffort?: string;
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
      showSessionControls={widgetData.showSessionControls ?? false}
      defaultModel={widgetData.defaultModel}
      disabledModels={widgetData.disabledModels}
      modelVariants={widgetData.modelVariants}
      defaultEffort={widgetData.defaultEffort}
    />
  );
}

function OpencodeChat(
  props: WidgetProps & { sessionID: string; directory: string; showSessionControls: boolean; defaultModel?: string; disabledModels?: Record<string, string>; modelVariants?: Record<string, string[]>; defaultEffort?: string },
) {
  const { sessionID, directory, widgetProxyUrl, showSessionControls, defaultModel, disabledModels, modelVariants, defaultEffort } = props; // widgetProxyUrl ends with '/' — trailing slash is load-bearing
  const variants = modelVariants ?? {};
  const disabled = disabledModels ?? {};
  const toolVerbosity = ((props.process.widgetData as any)?.toolVerbosity ?? 'description-only') as
    'silent' | 'description-only' | 'verbose';
  const thinkingVerbosity = ((props.process.widgetData as any)?.thinkingVerbosity ?? 'hidden') as
    'hidden' | 'visible';
  const showFileUpload = Boolean((props.process.widgetData as any)?.showFileUpload);
  const maxUploadBytes = Number((props.process.widgetData as any)?.maxUploadBytes ?? 10_000_000);
  const fileDownload = Boolean((props.process.widgetData as any)?.fileDownload);
  const nativeSpinner = Boolean((props.process.widgetData as any)?.nativeSpinner);
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
  // currentModel is the send-path source of truth (attached inline to each
  // prompt_async); the equivalent select value also lives in state.controls for
  // display. onControlChange keeps the two in sync (UI-local, no round-trip).
  const [currentModel, setCurrentModel] = useState<OpencodeModel | null>(null);
  // currentEffort is the send-path source of truth for the reasoning `variant`
  // (attached inline to prompt_async, beside `model`); the equivalent slider
  // value also lives in state.controls for display. null ⇒ no variant attached
  // (the current model has none, or none chosen). groupsRef stashes the resolved
  // provider catalog so onControlChange can rebuild the control snapshot (and
  // re-derive effort presence) when the model changes.
  const [currentEffort, setCurrentEffort] = useState<string | null>(null);
  const groupsRef = useRef<ModelGroup[]>([]);

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
      groupsRef.current = parsed.groups;
      // Seed the initial effort for the resolved model: defaultEffort if it is
      // one of that model's variants, else the model's first variant, else null
      // (no variants → no effort control).
      const eff = effortForModel(resolved, variants, defaultEffort ?? null);
      setCurrentEffort(eff);
      // Seed the model (+ effort, when the model supports it) controls into
      // state.controls (snapshot). The shared ConversationView renders them
      // generically; they only surface to the user when showSessionControls
      // gates the prop pass-through below.
      dispatch({
        ev: { type: 'x-optio-control-update', controls: buildControls(parsed.groups, resolved, disabled, variants, eff) },
        seq: localSeqRef.current--,
      });
    })();
    return () => { cancelled = true; };
  }, [widgetProxyUrl, sessionID]);

  // Control changes are UI-local: opencode applies both the model and the
  // reasoning `variant` inline on the next prompt_async (no /control listener,
  // no POST). Update the send-path source and fold the change so the controls
  // reflect the choice immediately.
  function onControlChange(id: string, value: string | boolean) {
    if (typeof value !== 'string') return;
    if (id === 'model') {
      const [providerID, modelID] = value.split('/');
      const next = { providerID, modelID };
      setCurrentModel(next);
      // Re-derive effort for the NEW model (its variants may differ / vanish):
      // keep the chosen level if the new model offers it, else its first
      // variant, else drop the control. A fresh control snapshot rebuilds both
      // the model select and the effort slider (presence follows the model).
      const eff = effortForModel(next, variants, currentEffort);
      setCurrentEffort(eff);
      dispatch({
        ev: { type: 'x-optio-control-update', controls: buildControls(groupsRef.current, next, disabled, variants, eff) },
        seq: localSeqRef.current--,
      });
      return;
    }
    if (id === 'reasoning_effort') {
      setCurrentEffort(value);
      dispatch({ ev: { type: 'x-optio-control-update', id, value }, seq: localSeqRef.current-- });
    }
  }

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
    // With attachments, upload through the generic route first (the bytes land
    // in <workdir>/uploads/<name>), then bundle one System: notice per stored
    // file into the prompt so opencode reads them from the workdir with its own
    // tools. No inline data-URL file part — every engine shares one path now.
    let prompt = body;
    if (attachments.length > 0) {
      const uploadUrl = resolveUploadUrl(props.process.widgetData, widgetProxyUrl);
      if (!uploadUrl) return false;
      const { ok: stored, failed } = await uploadFiles(uploadUrl, attachments, maxUploadBytes);
      for (const f of failed) {
        // Surface each failed upload as an immediate, transient error row.
        dispatch({ ev: { type: 'x-optio-local-error', properties: { text: `Upload failed: ${f.name} — ${f.error}` } }, seq: localSeqRef.current-- });
      }
      // Everything failed and no prompt to send → don't send an empty turn.
      if (stored.length === 0 && body.trim() === '') return false;
      prompt = bundleUploadNotice(stored, body);
    }
    const promptBody: any = { parts: [{ type: 'text', text: prompt }] };
    if (currentModel) promptBody.model = currentModel;
    // Attach the chosen reasoning variant (effort) beside the model — only when
    // the current model actually offers it (client-side, no round-trip).
    if (currentModel && currentEffort) {
      const key = `${currentModel.providerID}/${currentModel.modelID}`;
      if ((variants[key] ?? []).includes(currentEffort)) promptBody.variant = currentEffort;
    }
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
      thinkingVerbosity={thinkingVerbosity}
      showFileUpload={showFileUpload}
      maxUploadBytes={maxUploadBytes}
      fileDownload={fileDownload}
      nativeSpinner={nativeSpinner ? <NativeSpinner engine="opencode" /> : undefined}
      onSend={onSend}
      onInterrupt={onInterrupt}
      onPermission={onPermission}
      onFileDownload={onFileDownload}
      controls={showSessionControls ? state.controls : undefined}
      onControlChange={showSessionControls ? onControlChange : undefined}
      themeMode={(props as any).themeMode}
      onToggleTheme={(props as any).onToggleTheme}
    />
  );
}
