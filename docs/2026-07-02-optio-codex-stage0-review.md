# Review: optio-codex branch (`csillag/optio-codex`) vs the wrapper porting guide

**Date:** 2026-07-02
**Scope:** the three branch commits (`af13d5e`, `dcc6a69`, `d1c9234`) — the whole
`packages/optio-codex` package plus the packaging registration in the root
`Makefile` and `packages/optio-demo`.
**Yardstick:** `docs/writing-agent-wrappers.md` (Appendix A parity checklist,
Part 3 staged path, Part 5 packaging/demo requirements), with
`optio-claudecode` and `optio-opencode` as the reference implementations.
**Method:** 6-dimension multi-agent review (parity audit, design, session
implementation, host-actions implementation, prompt/types/API, tests), with
every critical/major finding independently adversarially verified against the
code. 18 agents, 0 findings survived unverified.

---

## Verdict

**This is not a full-featured codex wrapper. It is a competent Stage 0 (of 8),
with two critical implementation bugs and one required-even-at-Stage-0 guide
violation.**

What it gets right: the tmux+ttyd iframe machinery, the log-protocol driver
wiring, HOME/XDG isolation *intent*, host-abstraction discipline, the
fake-agent test pattern, and honest gap-tracking in the README are all faithful
adaptations of the claudecode reference. The test suite passes (9/9, 4.4s).

What blocks even the Stage 0 claim:

1. **Critical bug:** the isolated per-task `HOME`/`CODEX_HOME` directory is
   never created — codex launches into a nonexistent `$HOME` (empirically
   verified to break).
2. **Critical bug:** teardown `pkill`s by the *shared* codex binary path —
   tearing down one task kills every codex process on the host, including
   other optio tasks and the operator's own sessions.
3. **Guide violation (required):** zero demo tasks. Part 5 is "required for
   every wrapper"; the iframe demo explicitly ships at Stage 0; Stage 0's own
   done-when ("a demo task launches, does work, emits DONE, tears down
   cleanly") has never been demonstrated against the real codex CLI.
4. **Release contradiction:** the package is registered in `RELEASABLE_PY` and
   as an optio-demo runtime dependency while its own README lists "PyPI
   release" as still missing and nothing in optio-demo imports it.

Parity scoreboard: roughly 8–9 of the 29 Appendix A items are genuinely
satisfied (#1 iframe, #4 log protocol, #6 partially — see teardown bug, #16
HOME/XDG intent, #17 hooks, #18 prompt SSOT partially, #26 browser handling,
#28 partially). Everything else is missing. Most gaps are *tracked* in the
README (good — the guide only demands naming them), but five second-order
items are silent gaps (#9 crash-orphan rescue, #12 pool/leases, #14
verify/refresh, #20 model switching, #24 session restore), and the two
critical bugs mean even the claimed surface isn't sound.

---

## Critical findings (verified)

### C1. Per-task `HOME`/`CODEX_HOME` never created before launch

`_isolation_env` (`packages/optio-codex/src/optio_codex/host_actions.py:99-108`)
points `HOME`, `CODEX_HOME`, `XDG_*` and the head of `PATH` at
`<workdir>/home`, but nothing ever `mkdir -p`s that tree. `_prepare`
(`session.py:47-67`) only resolves binaries and writes `AGENTS.md`;
`build_host` (`host_actions.py:88-96`) creates only taskdir/workdir; the bash
payload (`host_actions.py:139-143`) only `cd`s to the workdir.

The claudecode reference *always* creates the home before launch:
`ensure_claude_installed` runs `mkdir -p …/home/.local/{share/claude,bin}`
(`optio-claudecode/host_actions.py:328-337`) and `plant_home_files` starts with
`mkdir -p <workdir>/home/.claude` (`:530-535`). The port dropped the
precondition because codex needs no npm-install step — but the mkdir lived
inside that step.

Verification was empirical: launching codex with a nonexistent `$HOME` breaks
it. Never caught because the fake shim ignores `$HOME` and there is no real-CLI
demo task (see M1).

**Fix:** `mkdir -p` the isolation tree in `_prepare` (or in the launch
payload).

### C2. Teardown kills every codex on the host

`kill_codex_processes` / `await_codex_gone`
(`host_actions.py:409-439`) anchor `pkill -KILL -f '^<codex_path>'` on the
resolved binary path. `resolve_codex` (`:40-71`) returns a **host-shared**
path — `command -v codex` or `<install_dir>/codex` — identical for every task.
The claudecode template is only safe because its kill target is per-task:
`<workdir>/home/.local/bin/claude`
(`optio-claudecode/host_actions.py:314-317, 349, 384`), and its docstrings
state that scoping is load-bearing. The mechanism was copied without the
precondition that makes it safe.

Consequence: task A's teardown SIGKILLs task B's codex and the operator's own
interactive codex sessions. Concurrency is a first-class optio scenario (the
whole seeds/lease layer exists for it).

**Fix:** scope the kill per task — kill by tmux session/socket pane PID tree,
or install/symlink codex into the per-task home so the path is unique (which
Stage 5's binary-cache work would also want).

---

## Major findings (verified)

### M1. Demo tasks entirely missing (Appendix A #29, Part 5)

No `optio_demo/tasks/codex.py`; no aggregation change in
`optio_demo/tasks/__init__.py`. Guide: "A wrapper isn't done until it is
installable and demonstrated end-to-end. This part is required for every
wrapper" (`writing-agent-wrappers.md:353-355`); "the iframe/ttyd demo ships at
Stage 0" (`:385-387`). The gap *is* named in `README.md:49`, but Part 5 is not
gap-nameable — it's required at every stage, and the seed-setup/conversation
halves of the trio are the only parts that legitimately wait for Stages 3/6.
The only end-to-end evidence for the whole wrapper is the fake-agent test; the
commit message of `d1c9234` claims "live CLI profiling" but nothing in the
repo demonstrates it.

### M2. Premature release registration

`Makefile:139` adds optio-codex to `RELEASABLE_PY`; `optio-demo/pyproject.toml:31`
adds `optio-codex>=0.1,<0.2` as a runtime dependency — while `README.md:49`
declares "demo-task wiring and PyPI release" still missing and optio-demo
contains zero imports of the package. `make release-all` would publish 0.1.0
of a package whose own README says the release doesn't exist, and optio-demo
would depend on a package it never uses. Either finish M1 first or pull the
registration until then.

### M3. `~`-prefixed install dirs can never work

`types.py:93-99` validates `codex_install_dir`/`ttyd_install_dir` as "must
start with `/` or `~`" — but every consumer `shlex.quote`s the value, and a
quoted `'~/bin/codex'` is never tilde-expanded by the shell. So the documented
`~` form always fails `resolve_codex`'s probe (`host_actions.py:48-51`), and
`ensure_ttyd_installed` (`:216-243`) `mkdir -p`s a literal `./~` directory.
Inherited from the reference, but here the validator actively invites the
broken form. **Fix:** expand `~` against the host home (there's already
`resolve_host_home`) or reject `~` outright.

### M4. Teardown has zero behavioral test coverage

No tests for `teardown_session_tree`, `kill_codex_processes`,
`await_codex_gone`, `_codex_pgrep_pattern`, `tmux_session_alive`; no post-run
assertions in the session tests. The reference ships dedicated tests
(`optio-claudecode/tests/test_teardown_session_tree.py`,
`test_await_claude_gone.py`, `test_kill_ttyd_by_socket.py`). This is exactly
why C2 shipped undetected. Stage 0's done-when includes "tears down cleanly."

### M5. The shell-appended exit-status DONE/ERROR channel is never exercised

`host_actions.py:139-143` appends `DONE`/`ERROR: codex exited N` on process
exit. The semantic itself is inherited from claudecode (`:661`) — including
the false-DONE-on-clean-TUI-quit vector — so it is by-design, but every fake
scenario writes keywords directly and sleeps until killed
(`tests/fake_codex.py:18-44`), so the wrapper's own rc-branch never runs in
any test. Only string-level coverage exists (`test_host_actions.py:35`).

### M6. No cancellation test

`conftest.py:118` yields a `cancellation_flag` no test ever sets. The
monitoring-loop exit (`session.py:110`), `cancelled=True` (`:134-135`), and
aggressive teardown/cleanup (`:151, :157`) are all unexercised. The reference
covers interruption (fake_claude `long`/`long_then_signaled`,
`test_tmux_persistence.py`).

---

## Design assessment

**Sound / faithful to the guide:**
- **Mode choice.** iframe/ttyd-first is a defensible Stage-0 staging decision
  for a TUI agent; the conversation path (`codex exec --json` / app-server) is
  named in the README as a tracked gap. Guide prefers conversation eventually —
  that's the single biggest missing *capability* (Appendix A #2/#3 are both
  `req`).
- **Host-abstraction discipline.** Free functions over `Host` primitives; the
  only `isinstance` is the sanctioned local-vs-remote bind decision.
- **Completion semantics.** The exit-status DONE/ERROR appender replicates the
  reference exactly (verified against the driver: duplicate DONE after
  completion is harmless).

**Flawed or needs an honest statement:**
- **Authentication story (downgraded from major after verification, but real).**
  Fresh empty `CODEX_HOME`, no seeds, no planted `auth.json`/`config.toml`, no
  documented `OPENAI_API_KEY`-via-`config.env` path, and `browser="suppress"`
  swallows any login URL the agent surfaces. Codex's ChatGPT OAuth flow needs a
  loopback callback on the worker — never tunneled. A Stage-0 task is
  unauthenticatable by default unless the operator knows to pass an API key in
  `env` or log in interactively inside the ttyd iframe — neither is written
  down anywhere. The README's "seeds and OAuth provisioning" line tracks the
  *future* mechanism but not the *present* operational answer. Also,
  claudecode chose `redirect` precisely to surface login URLs; `suppress`
  deserves a stated rationale.
- **`ssh` half-support trap.** `CodexTaskConfig.ssh` is accepted and routed to
  `RemoteHost` (`host_actions.py:88-96`) while the README declares remote
  unsupported — and the launch command bakes the *engine's* `PATH`
  (`os.environ`, `host_actions.py:124-126`) into a command that would run on
  the remote host. Either guard `ssh is not None` with a clear Stage-0 error
  or finish Stage 1.
- **`install_if_missing` is a dead knob.** Both branches of `resolve_codex`
  raise; `True` (the default) installs nothing (`host_actions.py:64-71`).
  Misleading config surface until Stage 5.
- **Unattended-by-default vs the prompt's promises.** `auto_start=True` flips
  both references' default, and combined with `ask_for_approval="never"` the
  default run is unattended — while `BASE_PROMPT_POST` tells the agent a human
  "is also working on the same task and will cooperate." Divergence is
  unnamed.
- **Codex-native sandbox vs claustrum.** `sandbox="workspace-write"` likely
  denies network to tool subprocesses with no config passthrough to relax it,
  and the relationship to future Landlock/claustrum isolation (Stage 8) is
  nowhere noted.

---

## Implementation quality (beyond the criticals)

- `session.py` is a faithful adaptation of the claudecode iframe branch:
  driver call, cancellation semantics, `_SessionFailed` mapping, teardown
  ordering, tunnel-env reads all match. Suspicions about pre-launch
  `agent_sender` crashes, cancelled-flag handling, and widget wiring were
  checked and refuted (guarded by `HookContext.send_to_agent`, matching the
  reference).
- Latent risks *inherited byte-for-byte from the reference* (not codex
  regressions, but worth an upstream look): partial-launch orphan windows
  around `_require_tmux`/ttyd spawn; ttyd's stdout pipe never drained after
  the port line (backpressure risk on chatty ttyd).
- Divergences from the reference that look accidental:
  - `before_execute` fires in `_prepare` — before the driver creates
    `optio.log` and browser shims; both references fire it after
    (`session.py:66-67`).
  - `teardown_session_tree` no-ops on ttyd when `ttyd_handle=None` — the
    reference's orphan-ttyd reap branch was dropped.
  - `_require_tmux` re-resolves tmux in `_codex_body` (`session.py:101`) after
    `launch_ttyd_with_codex` already resolved it — redundant, and `tmux_path`
    stays `None` for teardown if launch failed before that line.
  - Load-bearing rationale comments from the template were stripped in the
    copy.
- **SSOT drift:** `ProtocolFeatures(browser="suppress")` is constructed twice —
  `prompt.py:37` and `get_protocol(browser="suppress")` in `session.py:36` —
  instead of threading the session protocol's `documentation` through; and
  `BASE_PROMPT_POST`/intro framing are verbatim copies rather than imports
  from the optio-agents prompt SSOT. Two places to drift apart.
- `host_protocol=False` branch of `compose_agents_md` (`prompt.py:39-40`)
  drops the keyword docs without adding the guide-required "System:" message
  explainer. Currently unreachable (types.py forbids it in iframe mode), but
  it's a trap for Stage 6.
- `__init__.py` omits the vocabulary Literals (`IframeMode`, `ApprovalPolicy`,
  `SandboxMode`) that `types.py` declares in its `__all__`.

---

## Test suite

- **Pattern: right. Depth: thin.** Fake-agent/shim architecture matches the
  guide; `test_session_local.py` genuinely drives the full pipeline (real
  tmux, shim ttyd, DONE via optio.log, widget wiring, hooks); the
  deliverable-ack path exercises `send_text_to_codex` end-to-end against a
  real tmux (initial "zero coverage" suspicion refuted). 9/9 pass in 4.4s.
- Zero coverage: teardown (M4), rc-branch DONE/ERROR (M5), cancellation (M6),
  ttyd ready-timeout, `resolve_codex` fallbacks, `ensure_ttyd_installed`
  install path, `_launch_detached_checked` failure. No remote/docker-sshd
  harness despite the shipped `ssh` branch.
- Session-test assertions weakened vs the reference (deliverable payload and
  error message not checked).
- **The suite is not wired into the repo:** root `Makefile` `PY_PACKAGES`
  omits optio-codex, so `make test` never runs it.

---

## Recommended path to "full-featured"

Ordered; items 1–4 are fix-before-anything (they block even an honest
Stage 0):

1. Fix C1 (mkdir isolation home) + C2 (per-task kill scoping), with the
   teardown/cancellation tests from M4/M6.
2. Ship the Stage-0 iframe demo task (`optio_demo/tasks/codex.py`) and prove
   the done-when against the real CLI (M1); document the interim auth answer
   (API key via `env`, or interactive login in the iframe).
3. Resolve the release contradiction (M2): either demo wiring lands or the
   `RELEASABLE_PY`/pyproject registration comes out.
4. Guard `ssh is not None` with a Stage-0 error; fix or reject `~` install
   dirs (M3); add optio-codex to `PY_PACKAGES`.
5. Then climb the staged path exactly as the guide orders: Stage 1 remote,
   Stage 2 resume/snapshots, Stage 3 seeds (the real auth answer — Codex
   `auth.json` under `CODEX_HOME`), Stage 4 leases/save-back, Stage 5 binary
   cache (also naturally fixes the C2 kill-scoping properly), Stage 6
   conversation mode over `codex exec --json`/app-server + the
   optio-conversation-ui reducer/view, Stage 7 frontend parity, Stage 8
   claustrum (reconciled with codex's native sandbox).
6. Demo trio completion: seed-setup task after Stage 3, conversation
   seed-pinned demo after Stage 6 (the guide's parity note requires the pair).

For scale: the grok wrapper reached all 8 stages + the demo trio; codex is at
Stage 0 of that same path.
