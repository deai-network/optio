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

## Recorded plan-verbatim deviations (executor drift-guard working as designed)

- Task 6 test `test_host_protocol_false_keeps_resume_section_and_explainer`:
  plan's `assert "STATUS:" not in md` is unsatisfiable because optio-agents'
  `BASE_PROMPT_POST` mentions "`STATUS:` messages explained above"
  unconditionally; shipped test asserts the log-channel *documentation* is
  absent instead. Equivalent-or-stronger. (Side note, optio-agents-owned: with
  `host_protocol=False` the composed AGENTS.md references STATUS messages
  "explained above" that are never explained — upstream prompt bug.)
- Plan B Task 8 Step 4's combined
  `pytest packages/optio-agents/tests packages/optio-host/tests` aborts on a
  pre-existing `test_download.py` basename collision (no `__init__.py`); both
  suites pass individually (153 / 48). Repo condition from commit 78cc189, not
  the Plan-B diff.
