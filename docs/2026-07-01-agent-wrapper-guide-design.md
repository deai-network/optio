# Design: "Writing an optio Coding-Agent Wrapper" — porting guide

- **Date:** 2026-07-01
- **Status:** Approved (brainstorm)
- **Deliverable:** a standing guide at `docs/writing-agent-wrappers.md`
- **This document:** the design/spec for that guide — what it must contain, how it is
  organized, and the raw capability inventory the guide author works from.

## 1. Purpose

optio can already run two coding agents as tasks: `optio-claudecode` and
`optio-opencode`. We want to add more — **codex, cursor, and grok** are the
immediate targets — and we want to add them quickly and consistently.

The guide is the porting playbook and reference architecture for doing that. A
developer who wants to wrap a new agent should be able to read the guide, profile
their target agent, and build a full-parity wrapper in dependency-ordered stages,
without reverse-engineering the two existing wrappers from scratch.

## 2. Audience

A developer already familiar with optio's core concepts (`TaskInstance`, the
process/`ctx` model, the widget/dashboard model) but new to writing an agent
wrapper. The guide does **not** re-teach optio-core or optio-host; it teaches the
wrapper contract layered on top of them.

## 3. Guiding principles (these constrain the guide's content)

1. **Goals + interface + pointer, not code.** For each thing a wrapper must
   provide, the guide states (a) the *goal* — what capability it delivers and why,
   (b) the *interface to implement* — the method/callback/type the wrapper must
   satisfy, and (c) a *pointer* to where the two existing wrappers implement it
   (`package/file`, symbol names). The guide does **not** copy implementation code.
   When building codex/cursor/grok, the author reads the reference wrappers as
   needed. Concrete details live in code, not in the guide, so the guide does not
   rot as the code evolves.
2. **Full parity is the target; staged is how you get there.** A compliant wrapper
   eventually supports the whole capability surface (Appendix A). New agents start
   behind and reach parity through the staged build path (Part 3). Shipping a
   partial wrapper is fine; the guide names what is still missing so it is a
   tracked gap, not a silent one.
3. **A wrapper is a bundle of mode-adapters.** The central mental model: each
   *mode* binds one *agent capability* (headless API, own web server, TUI) to one
   *optio embedding surface* (conversation mode, iframe-web, iframe-ttyd). The
   config `mode` field selects. Both existing wrappers already ship more than one
   mode; this is a first-class concept, not an afterthought.
4. **Reference-first.** Every section ends by pointing at the real implementations
   in `optio-claudecode` and `optio-opencode`. They are the living source of truth.

## 4. The mode model (the guide's spine)

The guide is organized around mapping a target agent's capabilities onto optio
embedding surfaces:

| Agent capability | optio surface | claudecode | opencode |
|---|---|---|---|
| headless / programmatic API | **conversation mode** → drives `optio-conversation-ui` | stdio stream-json | HTTP+SSE |
| ships its own web server | **iframe** (embed the SPA) | — (TUI has no web UI) | `opencode web` SPA |
| TUI only | **iframe via ttyd** (embed the terminal) | tmux + ttyd | — |

Conversation mode is preferred (richest integration, native dashboard chat UI).
Where the agent's headless surface is limited — the canonical case being **login /
auth that cannot run headless** — the wrapper falls back to an interactive surface
(iframe-web or iframe-ttyd) for that operation, and/or pre-provisions identity via
**seeds**. A wrapper may ship several modes at once (both existing wrappers do).

## 5. Structure of the guide

### Part 0 — Orientation
- What a wrapper is: a `TaskInstance` factory that bundles mode-adapters.
- Package landscape and dependency direction: `optio-agents` (the contracts),
  `optio-host` (Local/Remote transport), `optio-core` (`TaskInstance`, `ctx`,
  `publish_result`/`launch_and_await_result`), `optio-conversation-ui` (the
  engine-neutral frontend). Pointers to both reference wrappers.

### Part 1 — Profile your target agent
- Checklist of questions about the target: Does it expose a headless/programmatic
  API? Does it ship a web server? Is it TUI-only? Can it authenticate headless, or
  does login require a browser/interactive session? Does it support resuming a
  prior session? Does it rotate credentials (single-use refresh tokens)?
- The capability→surface mapping and a decision tree: pick the primary mode
  (conversation preferred) and the fallback mode(s) for gaps.

### Part 2 — Interfaces to implement
For each interface: **goal**, **interface to implement** (signatures/types),
**reference pointer**, **key divergence note** (how the two engines differ so the
author sees which parts are contract vs engine-specific mechanism). No code copied.

- **A. Task / log-protocol driver.** Supply `body` (launch + manage the agent
  subprocess), `prepare` (runtime install + resume-restore), and `_agent_sender`
  (engine→agent transport); call `run_log_protocol_session`. The driver owns the
  workdir lifecycle, the `optio.log` tail/parse/dispatch loop, deliverable and
  caller-message queues, browser shims, the `HookContext`, and seed/resume
  machinery. Document the `optio.log` keyword semantics the agent emits and the
  wrapper must support (STATUS, DELIVERABLE, DONE, ERROR, BROWSER, ATTENTION,
  CLIENT_MESSAGE, CALLER_MESSAGE) and how optional keywords are feature-gated.
- **B. `Conversation` protocol.** Implement the `runtime_checkable` Protocol:
  `send`, `on_event`, `on_message`, `on_permission_request`, `is_pending`,
  `interrupt`, `close`, `closed`. Explain the two-tier event model (raw
  backend events pass through `on_event` untouched; one final answer per turn on
  `on_message`), permission gating via `PermissionRequest`/`PermissionDecision`,
  interrupt/close semantics, and `ConversationClosed`.
- **C. Conversation-UI.** Supply a pure reducer (native wire event → normalized
  `ChatItem`), a thin transport-adapter view (opens the event stream, wires the
  `ConversationViewProps` callbacks to the agent's endpoints), and a
  `widgetData.protocol` discriminator so `ConversationWidget` dispatches to it.
  The normalized `ChatItem` union is the target contract; the shared
  `ConversationView` renders everything once the reducer emits it.
- **D. Prompt composition.** Compose the agent's memory file (`CLAUDE.md`,
  `AGENTS.md`, or the target's equivalent) from the shared prompt SSOT
  (`build_log_channel_prompt`) + a resume-awareness section + the task framing +
  the consumer's instructions. Explain `host_protocol=False` (omit keyword docs),
  the `System:` message convention, and the `resume.log` protocol.

### Part 3 — Staged build path
Dependency-ordered. Each stage states: **goal**, **interface/config touched**,
**reference pointer**, **"done when."**

0. **MVP** — one mode running, `optio.log` DONE/ERROR, local host only.
1. **Remote / SSH** — mostly free via optio-host; select `LocalHost` vs
   `RemoteHost` by an `ssh` config field.
2. **Resume / snapshots** — `supports_resume`, workdir + session-state snapshots,
   `on_resume_refresh`, `resume.log`.
3. **Seeds** — start a fresh session already logged-in/configured; solves headless
   login; per-agent seed manifest adopting the generic `optio_agents.seeds` engine.
4. **Leases + credential save-back + verify/refresh** — pool/lease so N seeds are
   shared safely; a credential watcher that saves rotated tokens back into the
   seed; a host-free `verify_and_refresh_seed`.
5. **Binary cache + HOME/XDG isolation** — optio-owned evictable binary cache
   (never snapshotted, never pollutes host `~`); per-task HOME/XDG so each
   task/user gets its own agent identity.
6. **Conversation mode + conversation-ui** — the headless `Conversation` + the
   dashboard chat widget.
7. **Frontend parity** — permission gating, model switching, file upload/download,
   tool verbosity.
8. **Filesystem isolation** — Landlock/claustrum sandbox where the agent's launch
   model allows it (currently claudecode-only; note the pattern for new wrappers).

### Part 4 — Cross-cutting concerns
- **Host abstraction discipline.** Use only generic `optio_host.Host` primitives;
  the single sanctioned `isinstance` is the Local-vs-Remote bind-address decision.
- **The headless-login problem.** Three complementary solutions: seeds
  (pre-provision identity), an interactive fallback mode (human does login once in
  iframe/ttyd/web, captured as a seed), and OAuth-redirect rewrite (rewrite a
  loopback callback so login works remote/headless).
- **Browser handling.** The three browser modes (ignore / suppress / redirect) and
  the shim mechanism.
- **Isolation and security posture.** Fail-closed defaults; auth passwords off the
  argv; multi-container tunnel-bind knobs.
- **Testing pattern.** Fake-agent binaries / shims and a docker-sshd harness for
  remote-mode integration tests.

### Appendices
- **A. Flat parity checklist** — the full capability surface as an audit table:
  capability × required-or-optional × reference pointer. This is the completeness
  contract; §6 below is its source material.
- **B. Interface reference** — contract symbol → file map for the `optio-agents`
  surfaces (`Conversation`, `run_log_protocol_session`, `HookContext`,
  `get_protocol`/`ProtocolFeatures`, the seed/lease engine, the prompt SSOT).
- **C. Engine divergence table** — per capability, claudecode mechanism vs opencode
  mechanism, conceptual (not code), so the author sees the range of valid
  implementations.

## 6. Capability inventory (source material for Appendix A and Part 3)

The full parity surface, grouped. Each item becomes a checklist row with a
reference pointer in the guide.

**Modes & surfaces:** iframe (embed agent web UI or ttyd-served TUI); conversation
mode (headless live `Conversation`); conversation-ui (dashboard chat widget);
`host_protocol` toggle (keep/drop the `optio.log` keyword channel).

**Transport & lifecycle:** local subprocess + remote-via-SSH; readiness detection;
process monitoring; graceful vs aggressive teardown; auto_start kickoff.

**Coordination protocol (`optio.log`):** STATUS→progress, DELIVERABLE→fetch,
DONE/ERROR completion, BROWSER→open, ATTENTION→need-attention,
CLIENT_MESSAGE / CALLER_MESSAGE; feature-gated optional keywords; deliverable
path-escape guards; agent-feedback channel (`send_to_agent`).

**Resume & durability:** `supports_resume`; workdir + session-state snapshots with
retention; `workdir_exclude`; optional at-rest encryption of the session blob;
`on_resume_refresh`; `resume.log`; crash-orphan rescue; auto-resume-on-restart
(optio-core orchestration the wrapper opts into via `auto_resume`).

**Identity / seeds:** per-agent seed manifest; fresh-start seeding (logged-in);
credential-only manifest for resume overlay; pool/lease (exclusive, TTL,
crash-safe); credential watcher + save-back for rotating tokens; host-free
verify/refresh; seed CRUD wrappers (`delete`/`list`/`purge`); account/usage summary.

**Runtime provisioning:** optio-owned evictable binary cache (not snapshotted, no
host-`~` pollution); per-task HOME/XDG isolation; install-if-missing gating.

**Hooks:** `before_execute`, `after_execute`, `on_deliverable`, `on_caller_message`,
`on_seed_saved`, `on_session_saved`, `on_resume_refresh`; `HookContext` host
primitives (`run_on_host`, `copy_file`, `read_from_host`, `read_text_from_host`,
`download_file`, `send_to_agent`).

**Prompt:** memory-file composition from SSOT + resume section + task framing +
consumer instructions; `System:` prefix convention; downloadables block.

**Conversation feature parity:** permission gating; model switching (restart-based
vs inline — both valid); file upload; file download (the `optio-file:` sentinel);
tool verbosity; session restore/rebase for scripted consumers.

**Security / isolation:** filesystem isolation (Landlock/claustrum) where the
launch model allows; auth password off argv; fail-closed defaults; browser
suppress/redirect; multi-container tunnel-bind knobs.

## 7. Non-goals

- Not a tutorial on optio-core/optio-host internals.
- Not a code dump — no copied implementation from the existing wrappers.
- Not agent-specific onboarding for codex/cursor/grok — those are follow-on work
  that *uses* this guide; each gets its own spec.
- Does not mandate day-one full parity — staged delivery is expected.

## 8. Success criteria

- A developer can profile a new target agent and produce the capability→mode
  mapping from Part 1 alone.
- For every capability in Appendix A, the guide gives the interface to implement
  and a pointer to both reference implementations.
- The staged path (Part 3) lets an author ship a working wrapper at Stage 0 and
  reach parity incrementally, with each stage independently verifiable.
- The guide contains no copied implementation code and no detail that duplicates
  what the reference wrappers already encode.
