# optio-codex — review carryover (tracked, not silent)

Findings surfaced by the per-plan adversarial reviews that were deliberately
NOT fixed as codex-only changes, with rationale. Revisit in the Plan-E parity
audit; several belong upstream in `optio-grok` + the shared template, not as a
codex divergence.

## Shared-reference-parity items (fix upstream, not codex-only)

1. **Snapshot `endState` vocabulary has no error state.** `run_codex_session`
   sets `cancelled` only from `not ctx.should_continue()`, so an ERROR /
   `_SessionFailed` run captures with `end_state="done"`. Field is
   informational only (restore ignores it); exact grok parity
   (`optio-grok/session.py:486,596`). If fixed, fix grok's vocabulary too.
   Evidence: `packages/optio-codex/src/optio_codex/session.py:204-205,233`.

2. **`read_latest_session_id` conflates command failure with "no rollouts".**
   The `find|sort|tail` exit code is unchecked; a transport failure records
   `sessionId=None` → next resume degrades to a fresh session. Degradation IS
   loudly logged at resume (`session.py:96-104`); plan-pinned verbatim. Cheap
   strict improvement (inspect `r.exit_code`, warn on real failure) — but
   apply to grok's equivalent in the same change to keep parity.
   Evidence: `packages/optio-codex/src/optio_codex/host_actions.py:631-649`.

3. **Local vs remote snapshot exclude engines differ.** LocalHost archives via
   `yield_workdir_archive` (anchored fnmatch); RemoteHost shells `tar
   --exclude=` (unanchored). Empirically verified NOT to matter for the shipped
   codex defaults (busybox tar 1.37 in the sshd image drops
   packages/*.sqlite*/cache, keeps sessions — matches local). Lives in
   shared `optio-host` framework code (`host.py:837` vs `archive.py:36-50`),
   consumed identically by grok. Framework-level fix if ever.

## Latent operational nits

4. **sshd harness port 22223 collides with optio-host's harness** in the same
   tree (both compose files default to project name `tests`). Sequential
   Makefile runs are fine (established pattern; grok made the same choice);
   concurrent package-suite runs would conflict. Consider a distinct port or
   explicit compose project name. Evidence:
   `packages/optio-codex/tests/docker-compose.sshd.yml:19`.

5. **`REFRESHED:AGENTS.md` never emitted** though `_prepare` rewrites AGENTS.md
   every resume. Only bites when the composed prompt changes across engine
   restarts (code upgrade / changed exclude default); byte-identical otherwise.
   Verbatim grok parity — shared-template gap. Evidence:
   `session.py:116-127`, `prompt.py:57`.

## Plan C (Stages 3-5) carryover

Two majors from the Plan-C review were fixed in-diff (commit d4a72a1, both
verified codex-specific): the binary auto-download used fixed shared
scratch/tarball paths (concurrent cold-cache starts raced) → per-invocation
pid+uuid paths + atomic `mv -f`; and `verify_and_refresh_seed`'s probe
inherited `os.environ` wholesale so an ambient `OPENAI_API_KEY` could mark a
dead ChatGPT-mode seed alive → probe now scrubs `OPENAI_API_KEY`. Residual
minors, deliberately not forked from the reference:

6. **Cred watcher saves back BEFORE renewing the lease.** On the tick where
   the lease was stolen (TTL expired, re-acquired), the stale session writes
   its auth.json into the shared seed blob once before `renew_lease` detects
   the loss. Exact grok parity (grok loop = save-back → renew). The window is
   one tick (10s) and both sides hold a valid-format auth.json; the real
   protection against concurrent-refresh stranding is the lease itself. Fix in
   grok + codex together (reorder to renew-then-save) if ever.

7. **`ensure_workdir_trusted` interpolates the workdir into TOML unescaped.**
   A workdir path containing `"` or `\` yields malformed config.toml. optio's
   `task_dir` paths are optio-controlled and quote-free, so unreachable in
   practice; the idempotency substring check itself is sound (anchored by
   `"]`). Harden with `tomllib`-safe quoting if task-dir policy ever changes.

8. **Teardown seed capture not mutually exclusive with consume.** A config
   setting BOTH `seed_id` (consume) and `on_seed_saved` (capture) would
   capture at teardown, duplicating the rotating-token lineage. Same capture
   gate as grok (`not resuming and on_seed_saved and launched_handle`) — shared
   parity; the demo trio never sets both. Consider a `resolved_seed_id is None`
   guard upstream.

9. **Auto-download refuses any tarball shape but exactly one entry.**
   Deliberate safe-fail (`find -mindepth 1` must yield one entry, else error —
   never guesses which member is the binary). Correct for the pinned
   `rust-v0.142.5` musl asset (single binary); revisit only if a future release
   wraps the binary in a directory.

## Plan D (Stages 6-7) carryover

One major was fixed in-diff (commit d8dae3c): the codex reducer had regressed
grok's msgId-matching into tail-position matching, so GPT-5's interleaved
reasoning `activity` rows split the answer into a second bubble + stuck pending
indicator. Fixed to match grok (verified grok's reference is correct — codex
regressed the port, so codex was fixed, not forked). Residual minors, all
codex-specific UI error-edge polish (the widget's core render path is correct;
tsc + vitest green):

10. **Reducer ignores `willRetry` on the `error` notification.** When codex
    signals a transient (overload / rate-limit) with `willRetry:true`, the
    reducer renders a permanent error row even though codex auto-retries and a
    later `turn/completed` still delivers the answer. Should render transient
    (activity) or suppress until a terminal error. Deferred: needs a live
    overload event to TDD the exact rendering honestly (the reducer was built
    from the schema, not observed retry traffic). `events.ts` error branch.

11. **`interrupt()` awaits the `turn/interrupt` ACK** and can raise
    `ConversationClosed` if the process dies mid-interrupt — a mechanism
    divergence from grok's fire-and-forget `session/cancel` notification. NOT a
    bug: the Conversation contract explicitly permits `interrupt` to raise
    `ConversationClosed` after the session ends; codex's `turn/interrupt` is a
    request by protocol design. Left as a documented, contract-conformant
    divergence.

12. **Duplicate error row when an `error` notification precedes a failed
    `turn/completed`** carrying the same message. Dedup currently matches only a
    trailing error item; a non-trailing intervening item defeats it. Rare edge;
    cosmetic double row. `events.ts` error/turn-completed branches.

## Plan F (guide-delta) real-binary coverage ledger

Guide Appendix-A row 30 (real-binary E2E of every shipped surface) + Testing
Layers 2/3. Each surface has an opt-in, skip-if-no-binary/no-auth test that
**never** runs in the default suite. "Exercised" = run once against a real
authed codex at implementation time.

| Surface | Gate env var(s) | Test | Exercised |
| --- | --- | --- | --- |
| iframe done-when (Stage 0) | `OPTIO_CODEX_REAL_SESSION_TEST=1` | `test_real_codex_session.py` | pre-existing |
| native sandbox enforcement | `OPTIO_CODEX_SANDBOX_ENFORCE_TEST=1` | `test_sandbox_enforce.py` | pre-existing |
| conversation turn + Layer-3 wire capture | `OPTIO_CODEX_CONVERSATION_TEST=1` | `test_real_codex_conversation.py` | **tracked-open** |
| seed capture → replant | `OPTIO_CODEX_SEED_RESUME_TEST=1` | `test_real_codex_seed_resume.py::test_seed_capture_then_replant` | **tracked-open** |
| resume relaunch (session-id round-trip) | `OPTIO_CODEX_SEED_RESUME_TEST=1` | `test_real_codex_seed_resume.py::test_resume_relaunch_picks_up_session` | **tracked-open** |
| remote-SSH surface end-to-end | `OPTIO_CODEX_SEED_RESUME_TEST=1` + `OPTIO_CODEX_DEMO_SSH_HOST` | `test_real_codex_seed_resume.py::test_remote_iframe_surface_end_to_end` | **tracked-open** |

**Tracked-open** = the harness is fully wired to the as-built engine/host
patterns (Optio engine for the conversation capture; `run_codex_session` for
seed/resume; `SSHConfig` from the demo env vars for remote) but has NOT yet been
run against a real authed codex in this environment (no authed
`~/.codex/auth.json` available, and the COST GUARD forbids a billable model turn
in this run). Each must be run once against the real binary (the remote one
against a real remote) to earn its row-30 checkmark.

**Layer-3 codex wire fixture** (`packages/optio-conversation-ui/src/__tests__/
fixtures/codex-events.json`): pending a real capture. Until
`test_real_codex_conversation.py` is run once with `OPTIO_CODEX_CONVERSATION_TEST=1`
against a real authed codex, the fixture is absent and the default-suite replay
test `codex-events-fixture.test.ts` skips cleanly (green either way). Do NOT
fabricate the fixture — a hand-written stream would not exercise the real
interleaved-reasoning coalescing the test exists to guard.

## Plan F (guide-delta) carryover

Gaps 1-5 closed (resume-push, `auto_start=False`, seeded-teardown flush,
direct-OIDC verify, real-binary E2E breadth). Residual, not forked:

13. **Iframe-mode seeded teardown still SIGKILLs the agent via
    `kill_codex_processes`.** Gap 3 gates `aggressive` on seed-in-use, but
    `teardown_session_tree` calls `kill_codex_processes` with the default
    `KILL` signal regardless — the `aggressive` flag only affects the ttyd
    `terminate_subprocess`. **Exact grok parity** (`optio-grok`
    `teardown_session_tree` is byte-for-byte the same: KILL backstop after the
    tmux `kill-session` SIGHUP). In iframe mode the grace window is the
    SIGHUP-before-pkill gap; the fully-graceful SIGTERM-and-wait path is
    conversation mode (`terminate_subprocess(aggressive=False)`), which is
    where seeded rotating-token sessions run the credential watcher. Fix in
    grok + codex together if the iframe grace ever proves insufficient.
    Evidence: `host_actions.py:906-940` (both wrappers).

14. **Real-binary row-30 tests are wired + env-gated but not yet executed
    against a real authed codex** (conversation / seed-replant / resume /
    remote-SSH). They collect-and-skip cleanly (no billable turn in the default
    suite); the Layer-3 replay fixture is deliberately left absent (not
    fabricated) until a real capture materializes it. The iframe surface IS
    proven (`test_real_codex_session.py`, ran green 14.7s). The four new
    surfaces are tracked-open in the Plan-F coverage ledger above, per the
    guide's "honest gap, not faked-green" rule. Running them needs an authed
    `~/.codex` + `OPTIO_CODEX_CONVERSATION_TEST=1` / `OPTIO_CODEX_SEED_RESUME_TEST=1`.

## Recorded plan-verbatim deviations (executor drift-guard working as designed)

- Task 6 test `test_host_protocol_false_keeps_resume_section_and_explainer`:
  plan's `assert "STATUS:" not in md` is unsatisfiable because optio-agents'
  `BASE_PROMPT_POST` mentions "`STATUS:` messages explained above"
  unconditionally; shipped test asserts the log-channel *documentation* is
  absent instead. Equivalent-or-stronger. (Side note, optio-agents-owned: with
  `host_protocol=False` the composed AGENTS.md references STATUS messages
  "explained above" that are never explained — upstream prompt bug.)
- Plan F Task 5 Step 1 (`codex-events-fixture.test.ts`): plan's
  `fileURLToPath(new URL('./fixtures/...', import.meta.url))` throws
  `TypeError: The URL must be of scheme file` at module-eval time — vite inlines
  a top-level `import.meta.url` as a non-file URL under the jsdom environment.
  Shipped test resolves the fixture from the package root
  (`resolve(process.cwd(), 'src/__tests__/fixtures/codex-events.json')`, cwd is
  the package dir under vitest). Same intent (optional-fixture presence gate),
  working resolution.
- Plan B Task 8 Step 4's combined
  `pytest packages/optio-agents/tests packages/optio-host/tests` aborts on a
  pre-existing `test_download.py` basename collision (no `__init__.py`); both
  suites pass individually (153 / 48). Repo condition from commit 78cc189, not
  the Plan-B diff.
