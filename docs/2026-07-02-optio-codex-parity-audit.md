# optio-codex — final Appendix-A parity audit

> **⚠️ SUPERSEDED / pending refresh (Plan F, 2026-07-03).** This audit reflects
> the **pre-Plan-F** tree `3ef2142` and the **29-item** Appendix A. `main` has
> since gained a **30-item** Appendix A (adds **row 7b** resume-awareness
> pull **+** pushed `RESUME_NOTICE`, and **row 30** real-binary E2E of every
> surface), and codex has been rebased onto it. Two entries below are now
> inaccurate and are corrected by Plan F:
> - **row 7b is not audited here** — closed GREEN by Plan F Gap 1
>   (`build_resume_notice_args` + both bodies; commit `bf18471`).
> - **row 30 is not audited here** — addressed by Plan F Gap 5 (env-gated
>   real-binary breadth + tracked-open ledger).
> - **item 14 ("verify / refresh seed (host-free)") is mis-labelled GREEN.**
>   The guide defines that row as *host-free **and non-billable*** (no agent
>   process, no model inference — `writing-agent-wrappers.md` Stage 4 / Appendix
>   A row 14). The shipped `verify.py` still runs the **billable** agent probe
>   (`run_codex_probe`, `codex exec --json`); it is host-free-non-billable only
>   **after** Plan F Gap 4 (Task 4) lands the direct-OIDC path. Until then item
>   14 is GREEN-**functional** but not yet GREEN-**host-free-non-billable**.
>
> Plan F **Task 5 Step 8** regenerates this file against the current 30-item
> checklist once all five gaps land; that refresh replaces this banner. Do not
> cite this audit as current until then.

**Date:** 2026-07-03. **Tree:** branch `csillag/optio-codex` @ `3ef2142`.
**Yardstick:** `docs/writing-agent-wrappers.md` Appendix A (29 items — **stale;
`main` is now 30 items, see banner**).
**Method:** every row verified by reading the cited code/tests, not plans.
Suite state at audit time: `packages/optio-codex/tests/` → **188 passed, 4
skipped** (the 4 skips are the opt-in real-binary tests — `test_real_codex_session.py`
and the three `test_sandbox_enforce.py` cases, both env-gated, never in the
default suite). optio-conversation-ui codex widget/events covered by
`packages/optio-conversation-ui/src/__tests__/codex-{widget.test.tsx,events.test.ts}`.

All paths below are repo-relative to the worktree root
(`/home/csillag/deai/optio/.worktrees/csillag/optio-codex`).

| # | Capability | Req/Opt | Status | Evidence (file:line) | Test evidence |
|---|---|---|---|---|---|
| 1 | iframe mode (ttyd TUI) | opt | GREEN | packages/optio-codex/src/optio_codex/session.py:219 (`_codex_body`), :457 (`_codex_body` selected when `mode != "conversation"`) | packages/optio-codex/tests/test_session_local.py:19,68,101 |
| 2 | conversation mode (live `Conversation`) | req | GREEN | packages/optio-codex/src/optio_codex/conversation.py:114 (`CodexConversation`), :182 (`bootstrap`); session.py:286 (`_conversation_body`) | packages/optio-codex/tests/test_conversation.py:439; test_session_conversation.py:66 |
| 3 | conversation-ui widget | req | GREEN | packages/optio-conversation-ui/src/codex/CodexView.tsx:1; events.ts (`reduceCodexEvent`); ConversationWidget.tsx:26 (`protocol === 'codex'` dispatch); index.ts:4-5 (exports) | packages/optio-conversation-ui/src/__tests__/codex-widget.test.tsx; codex-events.test.ts |
| 4 | `optio.log` keyword protocol | req | GREEN | packages/optio-codex/src/optio_codex/session.py:459 (`run_log_protocol_session`); prompt.py:164 (`build_log_channel_prompt`) | packages/optio-codex/tests/test_prompt.py:7,13; test_session_local.py:19 |
| 5 | local + remote (SSH) | req | GREEN | packages/optio-codex/src/optio_codex/host_actions.py:360 (`build_host` → `LocalHost`/`RemoteHost`) | packages/optio-codex/tests/test_session_local.py:19; test_session_remote.py |
| 6 | readiness + monitoring + teardown | req | GREEN | packages/optio-codex/src/optio_codex/session.py:253 (`ready_timeout_s`), :496 (`teardown_session_tree`); host_actions.py:62-81 (`teardown_session_tree`), :43 (`await_codex_gone`) | packages/optio-codex/tests/test_teardown_session_tree.py; test_await_codex_gone.py; test_kill_ttyd_by_socket.py:23,34 |
| 7 | resume / snapshots | opt | GREEN | packages/optio-codex/src/optio_codex/snapshots.py:94 (`insert_snapshot`), :117 (`load_latest_snapshot`); types.py:196 (`supports_resume=True`); session.py restore path :107-142 | packages/optio-codex/tests/test_snapshots.py:31,35,60,73; test_session_resume.py |
| 8 | at-rest encryption of session blob | opt | GREEN (grok-parity: threaded, not activated) | `encrypt`/`decrypt` plumbed through packages/optio-codex/src/optio_codex/cred_watcher.py:86,113; verify.py:53,124; session save-back/plant call sites pass `encrypt=None` (session.py:275,528,568) — identical posture to optio-grok | Exercised via cred/verify suites (encrypt path is a pass-through; no separate activation test, matching grok) |
| 9 | crash-orphan rescue | opt | GREEN | packages/optio-codex/src/optio_codex/host_actions.py:833 (`_socket_pkill_pattern`), :842 (`_kill_ttyd_by_socket` — reap detached orphan ttyd), invoked from `teardown_session_tree` host_actions.py:903 | packages/optio-codex/tests/test_kill_ttyd_by_socket.py:23,34 |
| 10 | auto-resume-on-restart | opt | GREEN (via optio-core) | Enabled by packages/optio-codex/src/optio_codex/types.py:196 (`supports_resume=True`) + snapshots.py; the restart timer/scheduler lives in packages/optio-core/src/optio_core/lifecycle.py:975 (`_auto_resume_task`), models.py:80 (`auto_resume`) — wrapper contributes resume capability, core drives the restart | packages/optio-core tests/test_auto_resume.py (core-level); codex resume backing in test_session_resume.py |
| 11 | seeds (logged-in fresh start) | req* | GREEN | packages/optio-codex/src/optio_codex/seed_manifest.py:38 (`CODEX_SEED_SUFFIX`), :46 (`CODEX_CRED_MANIFEST` / `SeedManifest`) | packages/optio-codex/tests/test_seed_manifest.py:22,29,38; test_session_seed.py:61,88,109 |
| 12 | pool / leases | opt | GREEN | packages/optio-codex/src/optio_codex/cred_watcher.py:115-136 (`lease_holder`, `seeds.renew_lease`, abort on lease loss) | packages/optio-codex/tests/test_session_lease.py:105 |
| 13 | credential save-back | opt | GREEN | packages/optio-codex/src/optio_codex/cred_watcher.py:80 (`save_back_if_changed`), :107 (`run_credential_watcher`) | packages/optio-codex/tests/test_cred_watcher.py:66,70,75 |
| 14 | verify / refresh seed (host-free) | opt | GREEN (functional) — **not yet host-free-non-billable; Gap 4 pending** | packages/optio-codex/src/optio_codex/verify.py:45 (`verify_and_refresh_seed`) — currently the **billable** agent probe (`run_codex_probe`); the guide's host-free-non-billable bar is met only after Plan F Gap 4 (direct-OIDC) lands | packages/optio-codex/tests/test_verify.py:68,90,109,124,138,162 |
| 15 | binary cache (evictable, unsnapshotted) | req | GREEN | packages/optio-codex/src/optio_codex/host_actions.py:82 (`_resolve_install_dir`), :147 (`ensure_codex_installed`), :51 (cache dir env, XDG-rooted, per-task symlink into shared cache) | packages/optio-codex/tests/test_codex_cache.py:57,76,98,115 |
| 16 | HOME/XDG per-task isolation | req | GREEN | packages/optio-codex/src/optio_codex/host_actions.py:371 (`_isolation_env` — HOME/CODEX_HOME/XDG_* rooted at `<workdir>/home`), :383 (`_codex_isolation_env`) | packages/optio-codex/tests/test_workdir_trust.py:36,44,56,63 (trust config under isolated CODEX_HOME) |
| 17 | hooks (before/after execute, on_deliverable) | req | GREEN | packages/optio-codex/src/optio_codex/types.py:136-138 (`before_execute`/`after_execute`/`on_deliverable`); session.py:213-217 fires them | packages/optio-codex/tests/test_session_local.py:68 (`deliverable_callback_fired`) |
| 18 | prompt composition from SSOT | req | GREEN | packages/optio-codex/src/optio_codex/prompt.py:11 (imports shared `compose_agents_md` from optio-agents), :123 (`compose_agents_md`), :180 (delegates to host SSOT) | packages/optio-codex/tests/test_prompt.py:7,13,24,29 (`shared_framing_is_imported_not_copied`) |
| 19 | permission gating | opt | GREEN | packages/optio-codex/src/optio_codex/conversation_listener.py:73,87-100 (permission gate; parks until POST /permission); conversation.py:453 (`on_permission_request`) | packages/optio-codex/tests/test_conversation_listener.py:151,202; test_session_conversation.py:101 |
| 20 | model switching | opt | GREEN (inline) | packages/optio-codex/src/optio_codex/conversation.py:418 (`send`), :432 (inline model switch — override becomes thread default on next turn) | packages/optio-codex/tests/test_conversation.py:464,481; test_models.py |
| 21 | file upload | opt | GREEN | packages/optio-codex/src/optio_codex/conversation_listener.py:56 (`upload_writer`), :12 (POST /upload); session.py:366 wires `_write_upload` | packages/optio-codex/tests/test_file_upload.py:63,76 (incl. 413 too-large) |
| 22 | file download (`optio-file:`) | opt | GREEN | packages/optio-codex/src/optio_codex/conversation_listener.py:58 (`download_reader`), :14 (GET /download); session.py:368 wires `_read_download` | packages/optio-codex/tests/test_file_download.py:70,86 |
| 23 | tool verbosity | opt | GREEN | packages/optio-codex/src/optio_codex/types.py:169 (`tool_verbosity`); session.py:388 (`toolVerbosity` → widgetData) | packages/optio-codex/tests/test_session_conversation.py:208,229 (`toolVerbosity == "verbose"`) |
| 24 | session restore / rebase (scripted) | opt | GAP (deliberate) | No scripted transcript rebase — codex resume is snapshot + `codex resume <id>` (item 7), not claudecode's `transcript.py` reconstruction. Parity with optio-grok/optio-opencode, which also lack it | n/a (claudecode-specific mechanism) |
| 25 | filesystem isolation | opt | GREEN | packages/optio-codex/src/optio_codex/fs_allowlist.py (native-sandbox SSOT: `resolve_sandbox_settings`, `build_sandbox_cli_args`, `build_sandbox_config_overrides`); host_actions.py:563 (`build_codex_flags` seam) | packages/optio-codex/tests/test_fs_allowlist.py; test_session_sandbox.py:34,67; test_sandbox_enforce.py (real-binary, env-gated) |
| 26 | browser handling (suppress) | req | GREEN | packages/optio-codex/src/optio_codex/session.py:66 (`get_protocol(browser="suppress")`); prompt.py:135,165 (suppress docs threaded into prompt) | packages/optio-codex/tests/test_prompt.py:17,34 (suppress docs excluded from AGENTS.md) |
| 27 | headless-login strategy | req* | GREEN | Seeds supply the logged-in identity (seed_manifest.py:46; session.py plant path); conversation mode is headless (session.py:165 — app-server stdio, no ttyd login) | packages/optio-codex/tests/test_session_seed.py:61,109 (seeded fresh session plants identity before launch) |
| 28 | packaging + editable/release registration | req | GREEN | Makefile:4 (`PY_PACKAGES` includes `optio-codex`), :139 (`RELEASABLE_PY` includes `optio-codex`); packages/optio-demo/Makefile:13,36,54 (editable install); packages/optio-demo/pyproject.toml:31 (`optio-codex>=0.1,<0.2`) | Registration verified at Task 9; import smoke test_import.py |
| 29 | demo trio (seed-setup + iframe + conversation) | req | GREEN | packages/optio-demo/src/optio_demo/tasks/codex.py:185 (`codex-seed-setup`), :216 (`codex-demo-seed-<id>` iframe), :240 (`codex-conversation-seed-<id>`, `mode="conversation"`, `conversation_ui=True`) | Demo wiring exercised via optio-demo; seed lifecycle backed by test_session_seed.py |

## Remaining opt gaps

- **#24 session restore / rebase (scripted):** deliberately not shipped. codex
  offers snapshot + `codex resume <id>` (item 7 GREEN) as its resume story;
  claudecode's scripted `transcript.py` rebase is engine-specific and has no
  codex analogue. optio-grok and optio-opencode also omit it — this is parity,
  not a regression. README cross-ref: Sandbox/Status section (Task 8) lists it
  under "remaining opt gaps".

All other opt items (#7, #8, #9, #10, #12, #13, #14, #19, #20, #21, #22, #23,
#25) are GREEN. #8 is grok-parity: the encrypt/decrypt seam is plumbed through
but not activated (session passes `encrypt=None`), matching every other wrapper.

## Verdict

**28/29 green; the single non-green (#24) is an `opt` item with a documented
engine-specific rationale and cross-wrapper parity.** Every `req` item
(2, 3, 4, 5, 6, 11, 15, 16, 17, 18, 26, 27, 28, 29) is GREEN with code + test
evidence. No STOP-rule trigger. Cleared to proceed to Tasks 8–9.

> **Verdict caveat (see top banner).** This 28/29 tally is against the **29-item**
> Appendix A. On the current **30-item** Appendix A, row 7b (GREEN via Gap 1) and
> row 30 (Gap 5) are additional `req`/`req-if-resume` rows not counted here, and
> item 14's GREEN is *functional-only* until Gap 4 makes it host-free-non-billable.
> Plan F Task 5 Step 8 issues the authoritative up-to-date tally.
