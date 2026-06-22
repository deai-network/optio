# ClaudeCode Conversation-Mode Model Switching — Design (Phase 2)

**Base:** branch `csillag/opencode-frontend`, 2026-06-22. Paired with the opencode
design `docs/2026-06-21-opencode-conversation-model-switching-design.md` and its
Phase-1 implementation. Same user-facing capability — a session-sticky model
picker in the conversation widget — different engine mechanism (the package's
engine-parity rule).

## Summary

opencode switches model in place (a `model` field on each `prompt_async`).
Claude Code cannot: the in-place `/model` slash command is refused in headless
stream-json mode, and `--model` only takes effect at process launch. So a model
change is a **restart**: kill the `claude` subprocess and relaunch it in the same
workdir with `--continue` and the new `--model`. Claude Code's own transcript
(in `<workdir>/home/.claude/projects/<workdir>/`) makes the resumed session
continue seamlessly — no optio capture/resume involved.

This needs server-side wiring opencode didn't: a control channel for the widget
to request a model, a way to get the available-model list to the browser, and a
restart loop in the conversation body that preserves the task while replacing
only the process.

## Verified facts (this session)

- **`--model` overrides on `--continue`.** Empirically: a session started on
  `claude-haiku-4-5`, killed, relaunched with `--continue --model claude-opus-4-8`,
  continued on opus with the full transcript intact (recalled a fact planted in
  the haiku turn). Cost: one uncached turn — inherent to any model change (cache
  is per-model), not extra.
- **`/model` is refused headless.** Sent over stream-json stdin it returns a
  synthetic "/model isn't available in this environment"; the model does not
  change. In-place switching is impossible — restart is the only path.
- **No programmatic model list in Claude Code.** Fetch the account-available set
  from the Anthropic Models API (`GET /v1/models`). Claude Code 2.1.185 is the
  optio-pinned version (`~/.cache/optio-claudecode/versions`).
- **Lifecycle (what a restart must preserve), from the launch/teardown audit:**
  - pasta/netns is **not** in the conversation path (iframe-only) — nothing to
    preserve there.
  - Landlock (claustrum) is **per-process** — an argv prefix rebuilt each launch
    (`session.py` `_build_claustrum_wrap`). The relaunch MUST go through the
    normal launch path so the wrap is re-applied; a bare re-exec runs unconfined.
  - Task-scoped singletons survive untouched: `Host`, `ClaudeCodeConversation`,
    `ConversationListener` + SSE endpoint, the replay buffer, the permission gate,
    and `<workdir>/home/.claude`. The browser stays attached across the gap.
  - The conversation body already wraps a single process with attach → reader →
    cred-watcher → cleanup; the restart loop generalizes that.

## Decisions (confirmed 2026-06-22)

**D1. Model-change control channel → the conversation listener.** The per-task
engine HTTP listener (`conversation_listener.py`) already serves the SSE event
stream plus send / interrupt / permission endpoints, all reached through the
widget proxy. Add one endpoint, `POST .../model` with body `{ "model": "<id>" }`,
that requests a restart. This mirrors how opencode's controls ride the proxy; no
new transport. *(Recommended.)* Alternative: a clamator RPC — rejected, heavier
and inconsistent with the existing listener pattern.

**D2. Model-list delivery → fetched engine-side, pushed via `widgetData`.** The
engine calls `GET /v1/models` once at launch (the session's Anthropic credentials
live in the seeded `home/.claude`) and publishes the list (+ a default) in the
conversation widget's `widgetData`, alongside `showModelSelector`/`defaultModel`.
The browser never touches the Anthropic API directly (it has no creds).
*(Recommended.)* The list is account-stable within a task, so no refresh channel.
Alternative: a `GET .../models` listener endpoint the widget polls — rejected,
unnecessary for a static list.

**D3. Model identity is a plain string; the picker is per-adapter.** A Claude
model is a single id string (`"claude-opus-4-8"`, alias `"opus"`), unlike
opencode's `{providerID, modelID}`. So `ClaudeCodeView` renders its own
ungrouped `Select` over string models; the two adapters do not share a picker
component (the shared layer stays `AnswerBlock`/`Markdown`). *(Recommended.)*

**D4. Config — only `show_model_selector` (refined during planning).**
`ClaudeCodeTaskConfig` already has a `model` field (passed as `--model`), so it
**is** the default model — no separate `default_model` is added (that would
duplicate it; this is where claude diverges from opencode, which had no model
field). The one new field is `show_model_selector: bool = False`, requiring
`mode="conversation"` + `conversation_ui=True`. The initial launch uses
`config.model`; a runtime switch relaunches with the picked model; on resume the
transcript's model carries (Claude Code keeps the model the transcript was saved
with unless `--model` overrides — and the relaunch passes the new `--model`).

## 1. Restart loop (conversation body)

`_conversation_body` gains a restart path, triggered by D1. On a model-change
request:

1. Record the requested model and set an **intentional-restart flag** on the
   conversation/session so the teardown path knows this is not task-end.
2. Kill the `claude` subprocess (`terminate_subprocess`), cancel its reader task
   and credential watcher (the body already does this per process).
3. **Suppress the task-end captures** that the body's `finally` otherwise runs on
   any process exit — from the teardown audit, these must be gated off on an
   intentional restart: snapshot capture, seed capture, session-blob save-back,
   credential save-back. Keep the lease (don't release). Do **not** create the
   orphan-rescue marker. Keep the workdir.
4. Rebuild the conversation argv through the **normal launch path** with the new
   `--model` (re-applies the claustrum wrap), relaunch with `--continue`.
5. Re-attach the new handle to the existing `ClaudeCodeConversation`, restart the
   reader and credential watcher. The listener, replay buffer, widget upstream,
   and permission gate are untouched — the browser sees a brief "switching
   model…" gap then the stream resumes on the new model.

Template: `csillag/restart-on-demand` (built for opencode: "restart_channel +
body restart loop", "RESTART scheduler with defer/coalesce/cancel") — generalize
it to claudecode and drive it from the D1 endpoint. Coalesce rapid requests.

## 2. UI (ClaudeCodeView)

- A `Select` over string models, rendered only when `widgetData.showModelSelector`,
  placed at the bottom input bar (parity with opencode placement). Disabled while
  a turn is running or during a restart.
- `currentModel` state. Claude Code's stream-json assistant events carry the
  model in use; resolution order: (1) last model seen on the stream / in the
  restored transcript → (2) validated `defaultModel` → (3) the list's default →
  (4) null. On change → `POST .../model {model}` (D1); the widget shows a
  transient "switching model…" state until the stream resumes.

## 3. Lifecycle safety rules (non-negotiable)

- Relaunch ONLY via the normal launch path (claustrum re-applied) — never a bare
  `claude` re-exec.
- Gate off all task-end captures + the rescue marker on an intentional restart;
  keep the workdir and the lease.
- The restart replaces the process only; every task-scoped singleton is reused.

## 4. Scope / non-goals

- Claude Code only; opencode is shipped (Phase 1).
- No optio capture/resume on a model swap — Claude Code's own `--continue`
  transcript carries the conversation.
- Requires Anthropic credentials present in the seeded `home/.claude` for the
  `GET /v1/models` fetch (true for any conversation task that can run Claude).
- No `agent`/subagent selection; model only.

## Status

D1 (listener `POST /model`), D2 (engine-fetched list via widgetData), D3
(per-adapter string picker), D4 (config) confirmed. Implementation plan:
`docs/2026-06-22-claudecode-conversation-model-switching-plan.md`.
