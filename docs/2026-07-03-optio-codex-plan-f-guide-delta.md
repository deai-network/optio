# optio-codex Plan F — guide-delta parity (5 gaps ported from optio-grok)

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the five parity gaps optio-codex acquired when it was rebased onto a `main` that carries the newer `docs/writing-agent-wrappers.md` (seven wrapper-guide updates landed after codex forked). optio-grok already implements all five on `main`, each in a dedicated commit; this plan **ports grok's implementation to codex**, adapting only where codex's facts differ (native sandbox vs grok's custom Landlock profile; codex `app-server`/`resume` vs grok ACP/`-c`; OpenAI `auth.json` vs grok OIDC `key`). The five, in execution order:

1. **Resume PUSH** (`RESUME_NOTICE`) — guide Appendix-A row 7b (`req if resume`). Grok `ba739c2`.
2. **`auto_start` default → `False`** — guide Appendix D. Grok `87342cb`.
3. **Seeded-teardown graceful flush** — guide Stage 4 finding #1 (data-loss on a spent rotating token). Grok `3f604c7` (+ orphan-risk cross-check vs `fc1e5ef`).
4. **Direct-endpoint (OIDC) verify/refresh** — guide Stage 4 finding #2 (cost/reliability). Grok `dd17f6d`.
5. **Real-binary E2E breadth + Layer-3 capture/replay** — guide Appendix-A row 30, Testing Layers 2/3. Grok env-gated tests.

**Architecture.** Each gap is a self-contained port that leaves both suites green:
- **Gap 1** adds `host_actions.build_resume_notice_args(*, resuming)` (a single trailing positional, mutually exclusive with the auto-start kickoff), consumed by the iframe body's `codex_flags` and mirrored by a first-turn `conversation.send(...)` in the conversation body. Codex teaches the `System:` convention in **both** protocol modes (keyword docs when `host_protocol=True`; `_SYSTEM_PREFIX_EXPLAINER` when `False`; plus the resume section's own `System:` note whenever `supports_resume=True`) — so, like grok, **no `host_protocol` gate is needed**.
- **Gap 2** flips one default and fixes every caller that relied on it (two tests + one demo comment); task-execution surfaces opt in with an explicit `auto_start=True`, chat/conversation surfaces correctly inherit `False`.
- **Gap 3** extracts the teardown-aggressiveness decision to a pure `_teardown_aggressive(*, cancelled, seeded)` and routes both teardown paths through it; a seeded session is torn down gracefully (SIGTERM-and-wait) even on cancel so codex flushes its rotated `auth.json` before the backstop save-back reads it. Grok's sibling fix `fc1e5ef` (tty-wrapper `setsid` orphans the agent under `killpg`) is **N/A for codex** — codex uses its *native* sandbox and has no controlling-tty wrapper (`grep` confirms: no `setsid`/`TIOCSCTTY`/`exec`-prefix in the codex conversation launch). Task 3 records this as a verified non-issue, not a silent omission.
- **Gap 4** rewrites `verify.py` to talk straight to OpenAI's OIDC token endpoint (host-free, non-billable): parse `auth.json`, API-key seeds are alive-by-presence, ChatGPT-mode seeds refresh via the standard `refresh_token` grant (public CLI client), rotated tokens written back, fail-closed status. **Divergence from grok:** codex **keeps** the existing agent probe (`run_codex_probe`) as the documented **fallback** when OIDC discovery yields no usable `token_endpoint`.
- **Gap 5** adds env-gated real-binary E2E for every shipped surface (conversation turn, seed capture→replant, resume relaunch, remote-SSH) plus a Layer-3 capture-real-wire→replay-through-`reduceCodexEvent` regression fixture — mirroring opencode's committed-fixture pattern (`src/__tests__/fixtures/opencode-events.json`).

**Tech Stack.** Python ≥3.11, pytest + pytest-asyncio (`asyncio_mode=auto`), codex-cli 0.142.5 (all codex facts live-probed against that version — re-verify if the pin moved), MongoDB via the existing test fixtures; conversation-ui is TypeScript (Vitest + `tsc`).

## Global Constraints

- **Worktree:** `/home/csillag/deai/optio/.worktrees/csillag/optio-codex` — branch `csillag/optio-codex` (HEAD `ab3e679` at plan time). All paths relative to the worktree root unless absolute.
- **Python env:** the worktree venv **only** — `.venv/bin/python` / `.venv/bin/pip`. NEVER `pip install` against the global interpreter. If `import optio_codex` fails at baseline: `.venv/bin/pip install -e packages/optio-codex`.
- **Python test command:** `.venv/bin/python -m pytest packages/optio-codex/tests/ -q` (MongoDB on `localhost:27017`; if down: `cd packages/optio-demo && make deps-up`). Real-binary tests are env-gated (`OPTIO_CODEX_*_TEST=1`) and **never** run in the default suite.
- **UI env:** pnpm workspace. Typecheck/test the UI with the package-local binaries — `packages/optio-conversation-ui/node_modules/.bin/tsc --noEmit` and `packages/optio-conversation-ui/node_modules/.bin/vitest run` — **never `npx`**.
- **Commit style:** conventional commits (`feat(optio-codex): …` / `fix(optio-codex): …` / `test(optio-codex): …`), one commit per task step marked "Commit". **NO `Co-Authored-By` lines** (user rule).
- **SSOT / never-duplicate:** every gap ports the grok *pattern*, not grok files. No file is copied between packages; codex-specific facts are pinned in the codex module docstrings. No "kept in sync manually" duplication.
- **Reference (read directly, same tree, rebased onto main):** `packages/optio-grok/src/optio_grok/{host_actions,session,verify,types}.py` and `packages/optio-grok/tests/`. The five source commits: `git show <sha> -- packages/optio-grok/...` for `ba739c2` `87342cb` `3f604c7` `fc1e5ef` `dd17f6d`.
- **Every task leaves the whole codex pytest suite green (and the conversation-ui Vitest+tsc green where the UI is touched) before its commit.**

---

### Task 0: Baseline + pin the OpenAI OIDC facts (investigation) + orphan-risk cross-check

Establish a green baseline, pin the exact OpenAI token-endpoint facts Gap 4 depends on (a wrong guess must fail *closed*, but pinning avoids needless inconclusive results), and record the codex-vs-grok orphan-risk verdict Gap 3 depends on. No source changes except the design-doc/module-docstring fact pin performed in Task 4 (this task only *gathers* the facts).

**Files:**
- No source changes in this task. Produces pinned facts consumed by Tasks 3 and 4.

**Interfaces:**
- Consumes: the codex pytest suite; `curl`; a real `codex` binary if present (optional here — only the OIDC discovery doc is fetched, which needs no auth).
- Produces: (a) a recorded baseline pass count; (b) the pinned OpenAI OIDC discovery/token-endpoint/client-id facts; (c) the Gap-3 orphan-risk verdict.

- [ ] **Step 1: Baseline the Python suite.**

```bash
cd /home/csillag/deai/optio/.worktrees/csillag/optio-codex
.venv/bin/python -m pytest packages/optio-codex/tests/ -q 2>&1 | tail -15
```

Expected: all pass, 4 skipped (the opt-in real-binary tests — `test_real_codex_session.py` + the three `test_sandbox_enforce.py` cases). Record the exact pass/skip numbers; every later task compares against this.

- [ ] **Step 2: Baseline the conversation-ui suite (Gap 5 touches it).**

```bash
cd /home/csillag/deai/optio/.worktrees/csillag/optio-codex/packages/optio-conversation-ui
node_modules/.bin/vitest run 2>&1 | tail -12
node_modules/.bin/tsc --noEmit 2>&1 | tail -5
```

Record the Vitest pass count (codex reducer suite is `src/__tests__/codex-events.test.ts`, 18 tests).

- [ ] **Step 3: Pin the OpenAI OIDC facts (host-free, no auth).** The OIDC discovery document is public:

```bash
curl -s https://auth.openai.com/.well-known/openid-configuration | python3 -m json.tool | grep -Ei 'issuer|token_endpoint|userinfo_endpoint|authorization_endpoint'
```

Record verbatim: `issuer`, `token_endpoint`, `userinfo_endpoint` (if any), `authorization_endpoint`. If the discovery URL 404s or returns non-JSON, record that (Gap 4's discovery path will fail *inconclusive* and fall back to the agent probe — still correct, but note the exact behavior). **Pin the codex CLI `client_id`:** the login OAuth URL embeds `client_id=app_EMoamEEZ73f0CkXaXp7hrann` (from prior codex profiling) — confirm it is still the value by inspecting `codex login --help` output or the login URL if a real binary is available; otherwise carry the pinned value and mark it "unconfirmed against this binary".

- [ ] **Step 4: Confirm the codex `auth.json` shape for the refresh mapping.** From prior profiling (design doc §Part-1 #6): ChatGPT mode is `{"OPENAI_API_KEY": null, "tokens": {"id_token", "access_token", "refresh_token", "account_id"}, "last_refresh": "<ISO/epoch>"}`; API-key mode is `{"OPENAI_API_KEY": "sk-…", "tokens": null}`. If a real authed `~/.codex/auth.json` is available, confirm the key names verbatim (esp. `last_refresh` presence/format and whether `tokens` is nested vs top-level):

```bash
python3 -c "import json,pathlib; d=json.loads(pathlib.Path.home().joinpath('.codex/auth.json').read_text()); print(sorted(d.keys())); print(sorted((d.get('tokens') or {}).keys())); print('last_refresh=',d.get('last_refresh'))" 2>&1 || echo "no local authed auth.json — carry the design-doc shape"
```

Record the confirmed field names; Task 4's refresh-mapping code uses them verbatim.

- [ ] **Step 5: Gap-3 orphan-risk cross-check.** Confirm codex's conversation launch has none of the tty-wrapper `setsid`-escapes-`killpg` machinery that forced grok's `fc1e5ef`:

```bash
grep -rn "setsid\|TIOCSCTTY\|/dev/tty\|controlling.tty\|^ *cmd = \"exec \|exec \" +" packages/optio-codex/src/optio_codex/ || echo "NONE — no tty-wrapper in codex"
grep -n "shlex.quote(a) for a in argv" packages/optio-codex/src/optio_codex/session.py
```

Expected: **NONE** (codex uses its native sandbox, which needs no controlling `/dev/tty`; the conversation launch is a plain `codex app-server` command with no `exec`/`setsid` wrapper). Record the verdict: `fc1e5ef` is N/A for codex — the launched process *is* the app-server in the launched process group, so `killpg` reaches it. This verdict is written into the Task-3 commit body (not a separate doc).

- [ ] **Step 6:** No commit (investigation only). Carry the recorded facts into Tasks 3–4.

---

### Task 1 (Gap 1): Resume PUSH — `RESUME_NOTICE` on every relaunch, both surfaces

Port grok `ba739c2`. Codex ships only the *pull* half of resume awareness (the `resume.log` doc in `prompt.py` + `_append_resume_log_entry`). Add the *push* half: on **every** resume, deliver a `System: you have been resumed` turn — a trailing positional for the iframe TUI (mutually exclusive with the auto-start kickoff, which is already suppressed on resume), the first `conversation.send(...)` for conversation mode.

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/host_actions.py` (import + new `build_resume_notice_args`).
- Modify: `packages/optio-codex/src/optio_codex/session.py` (import; iframe `codex_flags`; conversation body).
- Test: `packages/optio-codex/tests/test_host_actions.py` (unit); `packages/optio-codex/tests/test_session_resume.py` (iframe argv assertions).

**Interfaces:**
- Consumes: `RESUME_NOTICE`, `SYSTEM_MESSAGE_PREFIX` (both already exported from `optio_agents` — confirmed `packages/optio-agents/src/optio_agents/__init__.py`).
- Produces: `host_actions.build_resume_notice_args(*, resuming: bool) -> list[str]` → `[f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}"]` when resuming, else `[]`.

- [ ] **Step 1: Write the failing unit test.** Append to `packages/optio-codex/tests/test_host_actions.py`:

```python
def test_build_resume_notice_args():
    from optio_codex.host_actions import build_resume_notice_args
    # Fresh launch → no notice.
    assert build_resume_notice_args(resuming=False) == []
    # Resume → a single System:-prefixed "you have been resumed" positional.
    notice = build_resume_notice_args(resuming=True)
    assert len(notice) == 1
    assert "you have been resumed" in notice[0]
    assert notice[0].startswith("System:")
```

- [ ] **Step 2: Add the iframe argv assertions to the resume E2E test.** In `packages/optio-codex/tests/test_session_resume.py`, inside `test_resume_via_recorded_session_id` (the test that asserts `launches[0]`/`launches[1]`), append after the existing `AUTO_START_PROMPT not in launches[1]` assertion (currently line ~162):

```python
    # PUSH resume awareness (Gap 1): only the RESUMED launch carries the
    # System: notice positional, so the resumed session gets a "you have
    # been resumed" turn. The fresh launch never does (it got the kickoff).
    assert not any("you have been resumed" in str(a) for a in launches[0]), launches[0]
    assert any("you have been resumed" in str(a) for a in launches[1]), launches[1]
```

- [ ] **Step 3: Run** `.venv/bin/python -m pytest packages/optio-codex/tests/test_host_actions.py packages/optio-codex/tests/test_session_resume.py -q` → the unit test FAILS (ImportError: `build_resume_notice_args`), the E2E assertion FAILS (`launches[1]` carries no notice).

- [ ] **Step 4: Implement `build_resume_notice_args`.** In `host_actions.py`, add the import near the top (alongside the existing `from optio_host.host import proc_wait`):

```python
from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX
```

and add the function directly after `build_auto_start_args` (after line ~594):

```python
def build_resume_notice_args(*, resuming: bool) -> list[str]:
    """Trailing positional that notifies a resumed codex TUI session.

    Returns ``[f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}"]`` on resume (codex
    relaunches with the ``resume <id>`` subcommand, so a trailing positional is
    processed as the resumed session's first turn — mirrors claudecode's
    ``claude --continue '<text>'`` and grok's ``grok -c '<text>'``). Empty on a
    fresh launch. This is the PUSH half of resume awareness — it makes codex
    notice the resume promptly; ``resume.log`` remains the pull-based source of
    truth. Codex is taught the ``System:`` convention in BOTH protocol modes
    (the keyword docs when ``host_protocol=True``; ``_SYSTEM_PREFIX_EXPLAINER``
    when ``False``; plus the resume section's own ``System:`` note whenever
    ``supports_resume=True``), so — like grok — no ``host_protocol`` gate is
    needed. Mutually exclusive with :func:`build_auto_start_args` (auto_start
    fires only on a FRESH launch; the notice only on a RESUME).
    """
    return [f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}"] if resuming else []
```

- [ ] **Step 5: Wire the iframe body.** In `session.py` `_codex_body`, in the `codex_flags` list, append the notice args immediately after the `build_auto_start_args(...)` block (after line ~239):

```python
            *host_actions.build_auto_start_args(
                auto_start=config.auto_start, resuming=resuming,
            ),
            # PUSH resume awareness (Gap 1): a System: notice positional appended
            # after `resume <id>` + flags so the resumed TUI session gets a "you
            # have been resumed" turn (mutually exclusive with the fresh-launch
            # kickoff above). Parity with claudecode/opencode/grok; resume.log
            # stays the pull-based backstop.
            *host_actions.build_resume_notice_args(resuming=resuming),
```

- [ ] **Step 6: Wire the conversation body.** In `session.py`, add to the top import:

```python
from optio_agents import HookContext, RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX, get_protocol
```

and in `_conversation_body`, replace the kickoff block (currently lines ~400–403):

```python
        # Kickoff prompt as the first turn (headless: no positional prompt
        # path). Suppressed on resume — re-kicking would duplicate the task.
        # On resume, PUSH a System: resume notice instead so the resumed thread
        # notices promptly (parity; resume.log stays the pull-based backstop).
        if config.auto_start and not resuming:
            await conversation.send(host_actions.AUTO_START_PROMPT)
        elif resuming:
            await conversation.send(f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}")
```

- [ ] **Step 7: Run** the two files green, then the full suite:

```bash
.venv/bin/python -m pytest packages/optio-codex/tests/test_host_actions.py packages/optio-codex/tests/test_session_resume.py -q
.venv/bin/python -m pytest packages/optio-codex/tests/ -q 2>&1 | tail -4
```

Both green (same skip count as Task 0 baseline).

- [ ] **Step 8: Commit** `feat(optio-codex): push a 'you have been resumed' System notice on resume (parity)`. Body: notes the conversation-mode push path is additionally exercised by the Gap-5 real-binary resume relaunch test (Task 5), matching grok's coverage shape (unit + iframe argv here; conversation push via the real path).

---

### Task 2 (Gap 2): `auto_start` default → `False` + caller audit

Port grok `87342cb`. `CodexTaskConfig.auto_start` defaults to `True`; grok/claudecode/opencode all default to `False`. A conversation/chat task that does not set `auto_start` inherits `True` and, on launch, fires the `AUTO_START_PROMPT` kickoff — starting an agentic loop that blocks the operator's first real chat message. Flip the default; make every task-execution surface opt in explicitly.

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/types.py` (the default + its comment).
- Modify: `packages/optio-demo/src/optio_demo/tasks/codex.py` (iframe demo comment truth-up; conversation demo already inherits — now correctly `False`).
- Test: `packages/optio-codex/tests/test_config.py` (default assertion); `packages/optio-codex/tests/test_session_resume.py` (`_cfg` now needs explicit `auto_start=True`).

**Interfaces:**
- Produces: `CodexTaskConfig.auto_start: bool = False`.
- **Caller audit (every reference, already enumerated):** `types.py:141` (the default — flip); `test_config.py:9` (`assert … c.auto_start is True` → `False`); `test_session_resume.py:71` `_cfg` (relies on the default for its `launches[0][-1] == AUTO_START_PROMPT` assertion → add explicit `auto_start=True`); `test_session_conversation.py` (already passes `auto_start=` explicitly, both values — no change); `demo/tasks/codex.py:234` (iframe demo already `auto_start=True` — value stays, comment is now wrong). No other references.

- [ ] **Step 1: Update the failing config test.** In `packages/optio-codex/tests/test_config.py`, change the assertion at line ~9:

```python
    assert c.mode == "iframe" and c.host_protocol is True and c.auto_start is False
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest packages/optio-codex/tests/test_config.py -q` → `test_defaults_and_validation` FAILS (still `True`).

- [ ] **Step 3: Flip the default + fix its comment.** In `types.py`, replace line ~141:

```python
    # When True, a fresh launch kicks off the first turn itself — iframe mode
    # types a trailing positional prompt, conversation mode sends the
    # AUTO_START_PROMPT ("Read AGENTS.md and execute the task it describes").
    # This is for UNATTENDED task execution; a task must opt in. Defaults to
    # False (parity with claudecode/grok/opencode): a conversation/chat task
    # must NOT auto-fire a kickoff, or codex starts an agentic loop on launch
    # and blocks the operator's first real prompt (queued behind it).
    auto_start: bool = False
```

- [ ] **Step 4: Fix the caller that relied on the default.** In `test_session_resume.py`, `_cfg` (line ~71) must now opt in — its E2E asserts the kickoff positional on the fresh launch:

```python
def _cfg(shim_install_dir: pathlib.Path) -> CodexTaskConfig:
    return CodexTaskConfig(
        consumer_instructions="do the thing",
        codex_install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        supports_resume=True,
        # Task-execution (iframe) demo shape: opt into the unattended kickoff
        # (the default is now False — Gap 2). Without this the fresh launch
        # carries no AUTO_START_PROMPT positional and the resume E2E fails.
        auto_start=True,
    )
```

- [ ] **Step 5: Truth-up the demo comment.** In `packages/optio-demo/src/optio_demo/tasks/codex.py`, the iframe demo already sets `auto_start=True` (line ~234) but the comment claims it is "the CodexTaskConfig default, spelled out" — now false. Replace those lines:

```python
                    # Kick the agent off unattended (reads AGENTS.md +
                    # executes). auto_start now defaults to False (Gap 2) — a
                    # task-execution surface must opt in explicitly; the
                    # seed-pinned CONVERSATION demo below correctly omits it.
                    auto_start=True,
```

The conversation demo (`codex-conversation-seed-*`) and the seed-setup task already omit `auto_start`, so they now correctly inherit `False` — no code change, but note in the commit body that this is the fix's intended effect (chat waits for the operator; login capture doesn't run a task).

- [ ] **Step 6: Run** the codex suite + a demo smoke import:

```bash
.venv/bin/python -m pytest packages/optio-codex/tests/ -q 2>&1 | tail -4
.venv/bin/python -c "import optio_demo.tasks.codex"   # demo module imports clean
```

Both green.

- [ ] **Step 7: Commit** `fix(optio-codex): auto_start must default to False (parity) — chat task auto-fired a kickoff`. Body enumerates the audited callers and the demo effect (conversation + seed-setup no longer auto-execute; iframe demo opts in explicitly).

---

### Task 3 (Gap 3): Seeded-teardown graceful flush

Port grok `3f604c7`. Codex uses `aggressive=cancelled` uniformly across all three teardown paths (`session.py` ~487, ~504, ~604). Codex's ChatGPT-mode refresh token is single-use/rotating (openai/codex#15410) and codex's `auth.json` write is best-effort; on a **cancelled** seeded session the aggressive SIGKILL can beat codex's flush of a just-rotated token, so the backstop save-back persists the *spent* token and the next launch of that seed demands re-auth. Gate teardown aggressiveness on seed-in-use: SIGTERM-and-wait (graceful) for a seeded session even on cancel, fast aggressive kill only for non-seeded.

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/session.py` (new `_teardown_aggressive`; route the two subprocess-teardown paths through it).
- Test: `packages/optio-codex/tests/test_host_actions.py` (pure-function unit test — sits with the other `session`/`host_actions` pure helpers, mirroring grok).

**Interfaces:**
- Produces: `session._teardown_aggressive(*, cancelled: bool, seeded: bool) -> bool` → `cancelled and not seeded`.
- **Scope note (from Task 0 Step 5):** grok's sibling fix `fc1e5ef` (tty-wrapper `setsid` orphans the agent so `killpg` misses it) is **N/A for codex** — codex has no controlling-tty wrapper (native sandbox needs no `/dev/tty`); the conversation launch is a plain `codex app-server` command and the launched pid *is* the app-server in the launched process group. No `exec`-prefix change is ported. This is recorded in the commit body.
- **`cleanup_taskdir` path unchanged:** the final `host.cleanup_taskdir(aggressive=cancelled)` (line ~604) is a workdir wipe *after* codex already terminated and after the backstop save-back already read `auth.json` — it does not race the flush, so it keeps `aggressive=cancelled` (matches grok, which only re-gated the two *subprocess* teardowns, not workdir cleanup). Only the two subprocess terminations are re-gated.

- [ ] **Step 1: Write the failing unit test.** Append to `packages/optio-codex/tests/test_host_actions.py`:

```python
def test_teardown_aggressive_grace_for_seeded_sessions():
    """A seeded session must tear codex down gracefully even on cancel so it
    can flush a rotated (single-use) auth.json before the backstop save-back
    reads it — an aggressive SIGKILL would strand the rotation and kill the
    seed. A non-seeded session keeps the fast aggressive kill on cancel."""
    from optio_codex.session import _teardown_aggressive
    assert _teardown_aggressive(cancelled=True, seeded=True) is False    # grace
    assert _teardown_aggressive(cancelled=True, seeded=False) is True    # fast kill
    assert _teardown_aggressive(cancelled=False, seeded=True) is False
    assert _teardown_aggressive(cancelled=False, seeded=False) is False
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest packages/optio-codex/tests/test_host_actions.py -q` → FAILS (ImportError: `_teardown_aggressive`).

- [ ] **Step 3: Add the pure decision function.** In `session.py`, add next to `_call_maybe_async` (after line ~61):

```python
def _teardown_aggressive(*, cancelled: bool, seeded: bool) -> bool:
    """Whether to SIGKILL codex immediately on teardown vs SIGTERM-and-wait.

    A **seeded** session is torn down GRACEFULLY even on cancel: codex's
    single-use ChatGPT refresh token may have rotated this session, and codex's
    auth.json write is best-effort — an aggressive SIGKILL can beat the flush,
    stranding the rotation so the credential save-back persists the now-spent
    token and the next launch demands re-auth. SIGTERM-and-wait lets codex
    flush first. A non-seeded session keeps the fast aggressive kill on cancel.
    """
    return cancelled and not seeded
```

- [ ] **Step 4: Route the two subprocess teardowns through it.** In `run_codex_session`'s `finally` block, after `cancelled = True` is (possibly) set (line ~473–474), compute the gated value once:

```python
        if not ctx.should_continue():
            cancelled = True
        # Codex authenticates (ChatGPT mode) with a SINGLE-USE rotating refresh
        # token. If codex rotated it this session, the new auth.json must reach
        # the seed via the backstop below — but an aggressive SIGKILL can beat
        # codex's flush, stranding the rotation (the seed keeps the now-spent
        # token → the next launch demands re-auth). So when a SEED is in use,
        # tear codex down GRACEFULLY (SIGTERM + wait) even on cancel, giving it
        # time to persist auth.json before the final save-back reads it. Only a
        # non-seeded session keeps the fast aggressive kill on cancel.
        codex_aggressive = _teardown_aggressive(
            cancelled=cancelled, seeded=resolved_seed_id is not None,
        )
```

Then change the conversation-subprocess teardown (line ~486–487) from `aggressive=cancelled` to `aggressive=codex_aggressive`:

```python
                await host.terminate_subprocess(
                    launched_handle, aggressive=codex_aggressive)
```

and the `teardown_session_tree(...)` call (line ~504) from `aggressive=cancelled` to `aggressive=codex_aggressive`:

```python
                    ttyd_handle=launched_handle,
                    aggressive=codex_aggressive,
```

Leave `host.cleanup_taskdir(aggressive=cancelled)` (line ~604) unchanged (see the Interfaces scope note).

- [ ] **Step 5: Run** the suite:

```bash
.venv/bin/python -m pytest packages/optio-codex/tests/ -q 2>&1 | tail -4
```

Green (same skip count as baseline).

- [ ] **Step 6: Commit** `fix(optio-codex): graceful teardown for seeded sessions so a rotated auth.json is flushed before save-back`. Body records the Task-0 orphan-risk verdict (grok `fc1e5ef` N/A for codex: native sandbox, no tty-wrapper, no `setsid`-escapes-`killpg` path).

---

### Task 4 (Gap 4): Direct-endpoint (OIDC) verify/refresh — non-billable, agent-probe as fallback

Port grok `dd17f6d`, adapted to OpenAI/codex facts. Codex `verify.py` currently plants the seed and runs a **billable** agent probe (`codex exec --json … '<capital-of-France>'`). Rewrite it to refresh straight against OpenAI's token endpoint (host-free, no model call): parse `auth.json`; an API-key seed is alive-by-presence; a ChatGPT-mode seed is refreshed via the standard `refresh_token` grant (public client), rotated tokens written back, status fail-closed and precise. **Endpoint caveat (Task 0):** unlike grok — whose xAI OIDC discovery `token_endpoint` *is* its refresh endpoint — codex's discovery `token_endpoint` (`…/api/accounts/oauth/token`) is a different surface; codex hardcodes its refresh URL to `https://auth.openai.com/oauth/token` (`CODEX_REFRESH_TOKEN_URL_OVERRIDE`), so Task 4 refreshes against that hardcoded URL and uses discovery only as a reachability gate (see the Interfaces endpoint note). **Divergence from grok:** codex **keeps** the agent probe as the documented **fallback** when OIDC discovery is unreachable.

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/verify.py` (rewrite `verify_and_refresh_seed`; add sync HTTP helpers, `_read_auth`, `_parse_last_refresh`; keep the probe-fallback branch calling the existing `run_codex_probe`).
- Keep: `host_actions.run_codex_probe` + `_codex_isolation_env` (unlike grok, which *removed* `run_grok_probe`) — they remain the fallback path.
- Test: `packages/optio-codex/tests/test_verify.py` (rewrite to stub the sync HTTP helpers; add a probe-fallback test that keeps the existing fake-probe harness).

**Interfaces:**
- Signature is **unchanged** (keeps `ssh`, `install_dir` — the fallback probe needs a host, unlike grok which dropped them): `verify_and_refresh_seed(db, *, prefix, suffix=CODEX_SEED_SUFFIX, seed_id, ssh=None, install_dir=None, encrypt=None, decrypt=None) -> bool`.
- New module-private sync helpers (run in an executor, mirror grok): `_discover_sync(issuer) -> dict | None` (a **reachability gate only** — see the endpoint note below; its `token_endpoint` is NOT the refresh URL for codex), `_refresh_sync(refresh_url, refresh_token, client_id) -> dict | str | None` (`_DEAD` sentinel on 4xx, `None` on transport), `_read_auth(blob_plain) -> dict | None`, `_parse_last_refresh(value) -> datetime | None`.
- **Pinned facts (from Task 0 Step 3/4 — see `docs/2026-07-03-optio-codex-task0-oidc-facts.md`, written into the module docstring verbatim):** issuer `https://auth.openai.com`; discovery `…/.well-known/openid-configuration`; public CLI `client_id = app_EMoamEEZ73f0CkXaXp7hrann`; `auth.json` ChatGPT shape `{"OPENAI_API_KEY": null, "tokens": {"id_token","access_token","refresh_token","account_id"}, "last_refresh": …}` (a top-level `auth_mode` is also present and is preserved automatically); refresh rotates `tokens.access_token` + `tokens.refresh_token` (+ `tokens.id_token` if returned) and stamps `last_refresh`. Refresh-freshness gate: codex refreshes proactively after 8 days (`TOKEN_REFRESH_INTERVAL`), so `need_refresh = last_refresh age ≥ 8 days` (or unparseable/absent).
- **⚠️ REFRESH ENDPOINT (Task 0 divergence — do NOT use discovery's `token_endpoint`):** the OIDC discovery `token_endpoint` is `https://auth.openai.com/api/accounts/oauth/token` — a **different** OAuth surface (account-management / rmcp-MCP), NOT what codex uses to rotate its own credential. The codex binary hardcodes its refresh URL as **`https://auth.openai.com/oauth/token`** (env override `CODEX_REFRESH_TOKEN_URL_OVERRIDE`). Task 4 therefore refreshes against the **codex-hardcoded URL** (`_REFRESH_URL = os.environ.get("CODEX_REFRESH_TOKEN_URL_OVERRIDE", "https://auth.openai.com/oauth/token")`), and uses `_discover_sync` **only** as a reachability signal (discovery down/unreachable → probe fallback). Fail-closed semantics unchanged (4xx → dead; transport/discovery error → inconclusive).

- [ ] **Step 1: Rewrite the tests (RED).** Replace `packages/optio-codex/tests/test_verify.py` with a mocked-HTTP suite plus one probe-fallback test that retains the existing fake-probe harness. Full file:

```python
"""verify_and_refresh_seed unit tests — direct-OIDC path (mocked HTTP) + the
agent-probe fallback (fake codex).

The refresh talks straight to OpenAI's OIDC token endpoint (no codex process,
no model inference). These tests stub the two sync HTTP helpers
(_discover_sync / _refresh_sync) so the verify/refresh/save-back/status logic
runs against a real Mongo seed with zero network. One test forces the
discovery-unavailable path and asserts codex falls back to the (billable)
agent probe — codex KEEPS that path (unlike grok), so it is covered here.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from optio_core.context import ProcessContext
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_codex import verify
from optio_codex.seed_manifest import CODEX_SEED_MANIFEST, CODEX_SEED_SUFFIX
from optio_codex.verify import verify_and_refresh_seed

_ISSUER = "https://auth.openai.com"
_CLIENT = "app_EMoamEEZ73f0CkXaXp7hrann"
# Discovery's token_endpoint is the REAL discovered value — an account-management
# surface, NOT codex's refresh URL. Production must IGNORE this and refresh
# against the hardcoded _REFRESH_URL (https://auth.openai.com/oauth/token). The
# stale-refresh test below asserts the refresh call used /oauth/token, so it
# fails if anyone regresses to disco["token_endpoint"].
_DISCO = {
    "issuer": _ISSUER,
    "token_endpoint": "https://auth.openai.com/api/accounts/oauth/token",
}


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


async def _make_seed(
    mongo_db, tmp_path, *, last_refresh, refresh_token="ORIGINAL",
    api_key=None, tokens=True,
) -> str:
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    ctx = ProcessContext(
        process_id="p", process_oid=oid,
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )
    src = LocalHost(taskdir=str(tmp_path / f"seedsrc-{oid}"))
    await src.setup_workdir()
    d = os.path.join(src.workdir, "home", ".codex")
    os.makedirs(d, exist_ok=True)
    auth: dict = {"OPENAI_API_KEY": api_key, "last_refresh": last_refresh}
    if tokens:
        tok = {
            "id_token": "OLD_ID",
            "access_token": "OLD_ACCESS",
            "account_id": "acct-1",
        }
        if refresh_token is not None:
            tok["refresh_token"] = refresh_token
        auth["tokens"] = tok
    else:
        auth["tokens"] = None
    with open(os.path.join(d, "auth.json"), "w") as fh:
        fh.write(json.dumps(auth))
    with open(os.path.join(d, "config.toml"), "w") as fh:
        fh.write('model = "gpt-5.5"\n')
    return await seeds.capture_seed(
        ctx, src, manifest=CODEX_SEED_MANIFEST, suffix=CODEX_SEED_SUFFIX,
        encrypt=None,
    )


async def _seed_auth(mongo_db, seed_id: str) -> dict:
    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX, seed_id=seed_id)
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(mongo_db).download_to_stream(doc["blobId"], buf)
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:gz") as tar:
        return json.loads(tar.extractfile(".codex/auth.json").read().decode("utf-8"))


async def _doc(mongo_db, seed_id: str) -> dict:
    return await seeds.load_seed(
        mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX, seed_id=seed_id)


async def test_stale_chatgpt_refreshes_and_writes_back(mongo_db, tmp_path, monkeypatch):
    old = _iso(datetime.now(timezone.utc) - timedelta(days=9))  # >8d → refresh
    seed_id = await _make_seed(mongo_db, tmp_path, last_refresh=old)

    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    seen = {}

    def fake_refresh(refresh_url, refresh_token, client_id):
        seen["call"] = (refresh_url, refresh_token, client_id)
        return {
            "access_token": "NEW_ACCESS", "refresh_token": "ROTATED",
            "id_token": "NEW_ID", "expires_in": 864000,
        }

    monkeypatch.setattr(verify, "_refresh_sync", fake_refresh)

    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is True
    assert seen["call"] == ("https://auth.openai.com/oauth/token", "ORIGINAL", _CLIENT)

    auth = await _seed_auth(mongo_db, seed_id)
    assert auth["tokens"]["access_token"] == "NEW_ACCESS"
    assert auth["tokens"]["refresh_token"] == "ROTATED"
    assert auth["tokens"]["id_token"] == "NEW_ID"
    assert auth["tokens"]["account_id"] == "acct-1"        # identity preserved
    assert auth["last_refresh"] != old                     # stamped
    assert (await _doc(mongo_db, seed_id))["status"] == "alive"


async def test_fresh_chatgpt_does_not_refresh(mongo_db, tmp_path, monkeypatch):
    recent = _iso(datetime.now(timezone.utc) - timedelta(days=1))  # <8d
    seed_id = await _make_seed(mongo_db, tmp_path, last_refresh=recent)
    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)

    def boom(*a):
        raise AssertionError("must not refresh a fresh token")

    monkeypatch.setattr(verify, "_refresh_sync", boom)
    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is True
    auth = await _seed_auth(mongo_db, seed_id)
    assert auth["tokens"]["refresh_token"] == "ORIGINAL"   # untouched


async def test_api_key_seed_is_alive_by_presence(mongo_db, tmp_path, monkeypatch):
    # API-key seeds have no rotating token — no refresh, alive by presence.
    seed_id = await _make_seed(
        mongo_db, tmp_path, last_refresh=None, api_key="sk-abc", tokens=False)

    def boom(*a):
        raise AssertionError("API-key seed must not hit the token endpoint")

    monkeypatch.setattr(verify, "_discover_sync", boom)
    monkeypatch.setattr(verify, "_refresh_sync", boom)
    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is True
    assert (await _doc(mongo_db, seed_id))["status"] == "alive"


async def test_refresh_4xx_marks_dead(mongo_db, tmp_path, monkeypatch):
    old = _iso(datetime.now(timezone.utc) - timedelta(days=9))
    seed_id = await _make_seed(mongo_db, tmp_path, last_refresh=old)
    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    monkeypatch.setattr(verify, "_refresh_sync", lambda *a: verify._DEAD)
    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is False
    assert (await _doc(mongo_db, seed_id))["status"] == "dead"


async def test_transport_failure_is_inconclusive_not_dead(mongo_db, tmp_path, monkeypatch):
    old = _iso(datetime.now(timezone.utc) - timedelta(days=9))
    seed_id = await _make_seed(mongo_db, tmp_path, last_refresh=old)
    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    monkeypatch.setattr(verify, "_refresh_sync", lambda *a: None)  # network err
    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is False
    assert (await _doc(mongo_db, seed_id)).get("status") != "dead"


async def test_no_refresh_token_is_dead(mongo_db, tmp_path):
    old = _iso(datetime.now(timezone.utc) - timedelta(days=9))
    seed_id = await _make_seed(
        mongo_db, tmp_path, last_refresh=old, refresh_token=None)
    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is False
    assert (await _doc(mongo_db, seed_id))["status"] == "dead"


async def test_discovery_unavailable_falls_back_to_agent_probe(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    # Discovery down → codex KEEPS the (billable) agent probe as the fallback
    # (divergence from grok). The fake codex probe answers "paris" → alive, and
    # rotates the auth.json ("ROTATED-BY-PROBE") which is saved back.
    monkeypatch.setenv("FAKE_CODEX_PROBE", "alive")
    old = _iso(datetime.now(timezone.utc) - timedelta(days=9))
    seed_id = await _make_seed(mongo_db, tmp_path, last_refresh=old)
    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: None)  # no endpoint

    alive = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=seed_id, install_dir=str(shim_install_dir),
    )
    assert alive is True
    assert (await _doc(mongo_db, seed_id))["status"] == "alive"


async def test_unknown_seed(mongo_db):
    alive = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=str(ObjectId()))
    assert alive is False
```

> **Adaptation note.** The final test reuses the existing fake-codex probe harness. Confirm the fake's probe-mode env var name at implementation time (`grep -rn "PROBE" packages/optio-codex/tests/fake_codex.py packages/optio-codex/tests/conftest.py`) and the `shim_install_dir`/`task_root` fixtures; if the fake signals probe-alive by a different mechanism (e.g. `FAKE_CODEX_SCENARIO=probe_alive`), use the as-built name — keep the *intent* (discovery-down → probe-alive → save-back → status alive), rename to match.

- [ ] **Step 2: Run** `.venv/bin/python -m pytest packages/optio-codex/tests/test_verify.py -q` → all new tests FAIL (the old billable-probe implementation ignores the stubs).

- [ ] **Step 3: Rewrite `verify.py`.** Full module (adapt the pinned facts from Task 0 into the docstring; keep the probe fallback + its imports):

```python
"""Codex seed verify + refresh via OpenAI's OIDC token endpoint (host-free,
non-billable) — with the agent probe kept as a documented fallback.

Primary path: read the seed's ``auth.json`` and, for a ChatGPT-mode seed whose
token is stale (codex refreshes proactively after 8 days — TOKEN_REFRESH_INTERVAL),
perform a standard OIDC ``refresh_token`` grant against codex's hardcoded
refresh URL (_REFRESH_URL; NOT the OIDC discovery token_endpoint — see the facts
block below), writing the rotated tokens back into the seed. No codex process,
no model inference — mirrors optio-claudecode's direct-endpoint ``oauth.py`` and
optio-grok's ``verify.py`` (grok's discovery token_endpoint IS its refresh URL;
codex's is not — the one divergence in this path).

Fallback (codex-specific divergence from grok, which removed its probe): when
OIDC discovery is unreachable (no usable ``token_endpoint`` in the response —
used only to confirm reachability), fall back to the billable
agent probe (``codex exec --json … '<challenge>'``) — the previous behavior —
so a seed is still verifiable if the endpoint is unreachable.

OpenAI OIDC facts (pinned Task 0, 2026-07-03, codex-cli 0.142.5):
  issuer            = https://auth.openai.com
  discovery         = <issuer>/.well-known/openid-configuration
  discovery.token_endpoint = https://auth.openai.com/api/accounts/oauth/token
                      -- an account-management surface; NOT codex's refresh URL.
                      Used here ONLY as a reachability signal (discovery down
                      -> agent-probe fallback), never as the refresh endpoint.
  refresh_url       = https://auth.openai.com/oauth/token   (codex hardcodes
                      this; env override CODEX_REFRESH_TOKEN_URL_OVERRIDE)
  public client_id  = app_EMoamEEZ73f0CkXaXp7hrann   (login OAuth URL; no secret)
  auth.json shape   = {"OPENAI_API_KEY": null|str,
                       "tokens": {"id_token","access_token","refresh_token",
                                  "account_id"} | null,
                       "last_refresh": <ISO/epoch>}
A refresh rotates tokens.access_token + tokens.refresh_token (+ tokens.id_token
if returned) and stamps last_refresh; account_id and OPENAI_API_KEY are
preserved. API-key seeds (OPENAI_API_KEY set, tokens null) carry no rotating
token — alive-by-presence, no refresh.

NOTE: endpoint/grant/public-client/shape are pinned above; the exact request
headers want one confirmation against a live seed (a wrong guess fails CLOSED:
a 4xx marks the seed dead; a network/discovery error is inconclusive and never
retires a healthy seed).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import tarfile
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.error import HTTPError, URLError

from optio_host.paths import task_dir

from optio_agents import seeds
from optio_codex import host_actions
from optio_codex.seed_manifest import CODEX_SEED_MANIFEST, CODEX_SEED_SUFFIX

_LOG = logging.getLogger(__name__)

_ISSUER = "https://auth.openai.com"
_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
# Codex hardcodes its ChatGPT refresh URL (manager.rs) — it is NOT the OIDC
# discovery `token_endpoint` (…/api/accounts/oauth/token), which is a separate
# account-management surface. Honor codex's own env override.
_REFRESH_URL = os.environ.get(
    "CODEX_REFRESH_TOKEN_URL_OVERRIDE", "https://auth.openai.com/oauth/token"
)
_AUTH_RELPATH = "home/.codex/auth.json"
_AUTH_MEMBER = ".codex/auth.json"
_HTTP_TIMEOUT_S = 20
_USER_AGENT = "optio-codex-seed-verify/1"
# codex refreshes proactively after 8 days (manager.rs TOKEN_REFRESH_INTERVAL).
_REFRESH_AFTER = timedelta(days=8)

# Sentinel: the refresh endpoint returned a 4xx (invalid_grant) — the refresh
# token lineage is definitively spent/revoked → mark the seed dead. Distinct
# from ``None`` (a network/transport failure → inconclusive, never mark dead).
_DEAD = "__dead__"

# Agent-probe fallback (discovery unavailable) — the previous behavior.
PROBE_PROMPT = "What is the capital of France? Answer with the city name."
PROBE_ANSWER_RE = re.compile(r"paris", re.IGNORECASE)


# --- synchronous HTTP (run in an executor; no host, no codex) ----------------

def _discover_sync(issuer: str) -> "dict | None":
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, ValueError):
        return None


def _refresh_sync(refresh_url: str, refresh_token: str, client_id: str) -> "dict | str | None":
    """OIDC refresh_token grant against codex's hardcoded refresh URL (NOT the
    discovery token_endpoint — see module docstring). Returns the token response
    dict on success, ``_DEAD`` on a 4xx (dead lineage), or ``None`` on a
    transport error."""
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }).encode("utf-8")
    req = urllib.request.Request(
        refresh_url, data=body, method="POST",
        headers={
            "User-Agent": _USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError:
        return _DEAD  # invalid_grant / 4xx → the refresh token is spent
    except (URLError, OSError, ValueError):
        return None  # network/transport → inconclusive


async def _in_executor(fn, *args):
    return await asyncio.get_event_loop().run_in_executor(None, fn, *args)


# --- helpers -----------------------------------------------------------------

def _parse_last_refresh(value) -> "datetime | None":
    """Parse codex's ``last_refresh`` — an RFC3339 string (possibly ``Z`` /
    sub-second) or an epoch number. None when unparseable/absent (→ treated as
    stale, i.e. refresh)."""
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 1e12 else value
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str) and value.strip():
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        s = re.sub(r"\.(\d{6})\d+", r".\1", s)  # nanoseconds → microseconds
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _read_auth(blob_plain: bytes) -> "dict | None":
    """The codex auth.json dict from the seed tar, or None if absent/malformed."""
    try:
        with tarfile.open(fileobj=io.BytesIO(blob_plain), mode="r:gz") as tar:
            f = tar.extractfile(_AUTH_MEMBER)
            if f is None:
                return None
            auth = json.loads(f.read().decode("utf-8"))
    except (tarfile.TarError, KeyError, ValueError, UnicodeDecodeError):
        return None
    return auth if isinstance(auth, dict) else None


# --- public API --------------------------------------------------------------

async def verify_and_refresh_seed(
    db,
    *,
    prefix: str,
    suffix: str = CODEX_SEED_SUFFIX,
    seed_id: str,
    ssh=None,
    install_dir: str | None = None,
    encrypt: "Callable[[bytes], bytes] | None" = None,
    decrypt: "Callable[[bytes], bytes] | None" = None,
) -> bool:
    """Verify a codex seed host-free via OpenAI's OIDC token endpoint; refresh
    the rotating token in place. Falls back to the billable agent probe only
    when OIDC discovery is unavailable.

    Returns True iff the seed is alive. Never raises for a dead seed. Marks pool
    status ``dead`` ONLY on a definitive dead signal (no refresh token,
    malformed auth, or a 4xx invalid_grant); a transport/discovery failure is
    inconclusive and leaves status untouched. Call only on a FREE seed or one
    whose lease the caller holds (a refresh rotates the single-use token).
    """
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket

    doc = await seeds.load_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        return False

    async def _finish(alive: bool, *, mark_dead: bool) -> bool:
        await seeds.declare_metadata(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id,
            metadata={"verify": {"alive": alive, "checkedAt": datetime.now(timezone.utc)}},
        )
        if alive:
            await seeds.mark_seed_status(db, prefix=prefix, suffix=suffix, seed_id=seed_id, status="alive")
        elif mark_dead:
            await seeds.mark_seed_status(db, prefix=prefix, suffix=suffix, seed_id=seed_id, status="dead")
        return alive

    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(db).download_to_stream(doc["blobId"], buf)
    dec = decrypt or (lambda b: b)
    auth = _read_auth(dec(buf.getvalue()))
    if auth is None:
        return await _finish(False, mark_dead=True)

    tokens = auth.get("tokens")
    # API-key seed: no rotating token → alive by presence.
    if not tokens:
        if auth.get("OPENAI_API_KEY"):
            return await _finish(True, mark_dead=False)
        return await _finish(False, mark_dead=True)  # neither tokens nor key

    refresh_token = tokens.get("refresh_token") if isinstance(tokens, dict) else None
    if not refresh_token:
        return await _finish(False, mark_dead=True)

    # Discovery is a REACHABILITY gate only: if OpenAI's OIDC surface is
    # unreachable we fall back to the agent probe. We deliberately do NOT use
    # disco["token_endpoint"] as the refresh URL — for codex that is a different
    # (account-management) surface; codex refreshes against the hardcoded
    # _REFRESH_URL (see module docstring / Task 0 facts).
    disco = await _in_executor(_discover_sync, _ISSUER)
    if not isinstance(disco, dict) or not disco.get("token_endpoint"):
        _LOG.warning(
            "seed %s: OIDC discovery unavailable — falling back to the agent probe",
            seed_id,
        )
        return await _verify_via_probe(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id, ssh=ssh,
            install_dir=install_dir, encrypt=encrypt, decrypt=decrypt,
            finish=_finish,
        )

    now = datetime.now(timezone.utc)
    last = _parse_last_refresh(auth.get("last_refresh"))
    need_refresh = last is None or (now - last) >= _REFRESH_AFTER
    if not need_refresh:
        # Fresh (codex hasn't hit its proactive-refresh window) → trust alive,
        # do not rotate. (Codex tokens carry no cheap userinfo scope like grok;
        # freshness is the liveness signal — documented divergence.)
        return await _finish(True, mark_dead=False)

    resp = await _in_executor(_refresh_sync, _REFRESH_URL, refresh_token, _CLIENT_ID)
    if resp is _DEAD:
        return await _finish(False, mark_dead=True)
    if not isinstance(resp, dict) or not resp.get("access_token"):
        return await _finish(False, mark_dead=False)  # transport → inconclusive

    tokens["access_token"] = resp["access_token"]
    if resp.get("refresh_token"):
        tokens["refresh_token"] = resp["refresh_token"]
    if resp.get("id_token"):
        tokens["id_token"] = resp["id_token"]
    auth["tokens"] = tokens
    auth["last_refresh"] = now.isoformat().replace("+00:00", "Z")
    try:
        await seeds.overwrite_seed_member(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id,
            member_path=_AUTH_MEMBER, content=json.dumps(auth).encode("utf-8"),
            encrypt=encrypt, decrypt=decrypt,
        )
    except Exception:  # noqa: BLE001 — save-back failed; the refresh still rotated
        _LOG.exception("seed %s: refreshed auth save-back failed", seed_id)
    return await _finish(True, mark_dead=False)


async def _verify_via_probe(
    db, *, prefix, suffix, seed_id, ssh, install_dir, encrypt, decrypt, finish,
) -> bool:
    """Fallback: plant the seed and run one billable ``codex exec`` challenge
    probe; verdict from stdout, rotated auth.json saved back. The previous
    behavior, retained for when OIDC discovery is unavailable."""
    taskdir = task_dir(
        ssh=ssh, process_id=f"seed-verify-{uuid.uuid4().hex[:12]}",
        consumer_name="optio-codex",
    )
    host = host_actions.build_host(ssh, taskdir)
    await host.connect()
    try:
        await host.setup_workdir()
        codex_exec = await host_actions.resolve_codex(
            host, install_dir=install_dir, install_if_missing=False,
        )
        await seeds.plant_seed(
            db, host, prefix=prefix, seed_id=seed_id,
            manifest=CODEX_SEED_MANIFEST, suffix=suffix, decrypt=decrypt,
        )
        stdout, exit_code = await host_actions.run_codex_probe(
            host, codex_executable=codex_exec, prompt=PROBE_PROMPT,
        )
        alive = PROBE_ANSWER_RE.search(stdout) is not None
        if not alive:
            _LOG.info("seed %s: probe dead (exit=%s, stdout[:200]=%r)", seed_id, exit_code, stdout[:200])
        workdir = host.workdir.rstrip("/")
        try:
            auth_raw = await host.fetch_bytes_from_host(f"{workdir}/{_AUTH_RELPATH}")
            auth = json.loads(auth_raw.decode("utf-8"))
            if isinstance(auth, dict) and (
                auth.get("tokens") is not None or auth.get("OPENAI_API_KEY") is not None
            ):
                await seeds.overwrite_seed_member(
                    db, prefix=prefix, suffix=suffix, seed_id=seed_id,
                    member_path=_AUTH_MEMBER, content=auth_raw,
                    encrypt=encrypt, decrypt=decrypt,
                )
        except (FileNotFoundError, ValueError, UnicodeDecodeError):
            _LOG.warning("seed %s: no valid auth.json after probe; skipping write-back", seed_id)
        # Probe failure is a definitive dead signal (the seed's own creds were
        # exercised end-to-end), so mark_dead=True here (unlike a transport error).
        return await finish(alive, mark_dead=not alive)
    finally:
        try:
            await host.cleanup_taskdir(aggressive=True)
        except Exception:  # noqa: BLE001
            _LOG.exception("verify: cleanup_taskdir failed")
        try:
            await host.disconnect()
        except Exception:  # noqa: BLE001
            _LOG.exception("verify: host.disconnect failed")
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest packages/optio-codex/tests/test_verify.py -q` → all green. Then the full suite:

```bash
.venv/bin/python -m pytest packages/optio-codex/tests/ -q 2>&1 | tail -4
```

Green (same skip count). `run_codex_probe` / `_codex_isolation_env` are still used (fallback) — no dead code removal.

- [ ] **Step 5: Commit** `feat(optio-codex): verify seeds via the OpenAI OIDC endpoint directly (host-free); keep agent probe as fallback`. Body pins the discovery/token-endpoint/client-id facts (Task 0) and calls out the deliberate divergence from grok (probe retained as fallback).

---

### Task 5 (Gap 5): Real-binary E2E breadth + Layer-3 capture/replay

Port grok's env-gated real-binary posture and satisfy guide row 30 + Testing Layers 2/3. Codex currently ships real-binary coverage for only two surfaces (iframe done-when: `test_real_codex_session.py`; sandbox primitive: `test_sandbox_enforce.py`). Add opt-in, skip-if-no-binary/no-auth, **never-in-default-suite** real-agent E2E for the remaining shipped surfaces, plus a Layer-3 capture-real-wire→replay-through-`reduceCodexEvent` regression fixture (the pattern opencode already ships and grok/codex lack).

**Files:**
- New: `packages/optio-codex/tests/test_real_codex_conversation.py` (opt-in: real conversation turn; **also** the capture harness that records the Layer-3 wire fixture).
- New: `packages/optio-codex/tests/test_real_codex_seed_resume.py` (opt-in: seed capture→replant; resume relaunch; remote-SSH if configured).
- New: `packages/optio-conversation-ui/src/__tests__/fixtures/codex-events.json` (the recorded real wire — materialized by the capture harness).
- New: `packages/optio-conversation-ui/src/__tests__/codex-events-fixture.test.ts` (default-suite replay of the fixture through the real reducer; skip-if-fixture-absent).
- Modify: `docs/2026-07-02-optio-codex-review-carryover.md` (record which surfaces are gated and why; any surface that genuinely cannot be auto-tested cheaply is tracked here, not silent).

**Interfaces:**
- Env gates (grok convention — opt-in + capability probes, never default):
  - `OPTIO_CODEX_CONVERSATION_TEST=1` — real `codex app-server` turn + wire capture. Skip unless: env set, real `codex` on PATH, authed `~/.codex/auth.json`.
  - `OPTIO_CODEX_SEED_RESUME_TEST=1` — seed capture→replant + resume relaunch. Same capability probes; remote-SSH sub-case additionally requires `OPTIO_CODEX_DEMO_SSH_HOST`.
- Layer-3 replay test runs in the **default** Vitest suite when `fixtures/codex-events.json` exists (committed by the capture harness); it skips cleanly if the fixture is absent (tracked, so the default suite is green either way).

- [ ] **Step 1: Layer-3 replay test (RED, default suite).** Create `packages/optio-conversation-ui/src/__tests__/codex-events-fixture.test.ts`:

```typescript
import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { initialChatState, type ChatState } from '../chat.js';
import { reduceCodexEvent } from '../codex/events.js';

// Layer 3 (guide "the real wire against the real reducer"): replay a REAL
// codex app-server event stream — captured once by the opt-in Python harness
// test_real_codex_conversation.py — through the real reducer, asserting the
// resulting ChatState is what a human should see. Fakes emit idealized events;
// only the real wire (interleaved reasoning + answer deltas from a reasoning
// model) exposes the reducer's real coalescing failure modes.
const fixtureUrl = new URL('./fixtures/codex-events.json', import.meta.url);
const fixturePath = fileURLToPath(fixtureUrl);
const present = existsSync(fixturePath);

function play(events: any[]): ChatState {
  return events.reduce((s, ev, i) => reduceCodexEvent(s, ev, i), initialChatState);
}

describe.skipIf(!present)('codex reducer — recorded real wire (Layer 3)', () => {
  const events: any[] = present ? JSON.parse(readFileSync(fixturePath, 'utf8')) : [];

  it('a real reasoning-model turn coalesces into ONE answer bubble', () => {
    const st = play(events);
    const assistants = st.items.filter((i) => i.kind === 'assistant');
    // The bug this guards: a reasoning model interleaves thought-deltas with
    // answer-deltas; a tail-position reducer fragments the answer into a bubble
    // per token. The real reducer coalesces by turn/message id.
    expect(assistants.length).toBe(1);
    expect((assistants[0] as any).text.length).toBeGreaterThan(0);
  });

  it('reasoning renders as its own activity rows, not the answer bubble', () => {
    const st = play(events);
    // At least one reasoning/activity row, distinct from the answer.
    expect(st.items.some((i) => i.kind === 'activity')).toBe(true);
  });

  it('busy is cleared at turn end', () => {
    const st = play(events);
    expect(st.busy).toBe(false);
  });
});
```

- [ ] **Step 2: Run** `packages/optio-conversation-ui/node_modules/.bin/vitest run src/__tests__/codex-events-fixture.test.ts` → the describe **skips** (no fixture yet). This is the intended RED for the default suite (green-by-skip); the fixture is produced in Step 4 and turns the skip into a pass.

- [ ] **Step 3: Real conversation turn + wire-capture harness (opt-in).** Create `packages/optio-codex/tests/test_real_codex_conversation.py`. It drives a real `codex app-server` conversation turn through the shipped `CodexConversation`, records every raw event via `on_event`, asserts a coherent turn, and writes the recorded stream to the Layer-3 fixture path so the reducer test above becomes live:

```python
"""Opt-in real-codex conversation E2E + Layer-3 wire capture (never default).

Guide Testing Layer 2 (conversation surface end-to-end against the REAL
binary) AND Layer 3 (capture the real wire → the reducer test replays it). One
real (billable) model turn, so it runs only when explicitly opted in:

    OPTIO_CODEX_CONVERSATION_TEST=1 .venv/bin/python -m pytest \
        packages/optio-codex/tests/test_real_codex_conversation.py -q

Skip-chain (grok convention): env flag set, real ``codex`` on PATH, an authed
``~/.codex/auth.json``. Writes the captured event stream to the
conversation-ui Layer-3 fixture so reduceCodexEvent is exercised on the real
wire (interleaved reasoning + answer deltas), not just fakes.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

from optio_codex import CodexTaskConfig, create_codex_task

REAL_HOME_CODEX = Path.home() / ".codex"
_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "optio-conversation-ui" / "src" / "__tests__" / "fixtures" / "codex-events.json"
)


def _authed() -> bool:
    try:
        data = json.loads((REAL_HOME_CODEX / "auth.json").read_text())
    except (OSError, ValueError):
        return False
    return bool(data.get("tokens") or data.get("OPENAI_API_KEY"))


pytestmark = pytest.mark.skipif(
    os.environ.get("OPTIO_CODEX_CONVERSATION_TEST") != "1"
    or shutil.which("codex") is None
    or not _authed(),
    reason="opt-in real-codex conversation test (OPTIO_CODEX_CONVERSATION_TEST=1, "
    "codex on PATH, authed ~/.codex/auth.json)",
)


@pytest.mark.asyncio
async def test_real_conversation_turn_and_capture(ctx_and_captures, task_root):
    from optio_core.lifecycle import Optio  # local import: heavy engine

    # Use the engine harness the fake conversation tests use; adapt to the
    # as-built helper names in test_session_conversation.py (this is an
    # adaptation seam — keep the intent: publish the live CodexConversation,
    # drive one real turn, record on_event).
    ctx, captures, _cancel = ctx_and_captures

    async def _plant_identity(hook_ctx):
        host = hook_ctx._host
        await host.write_text("home/.codex/auth.json", (REAL_HOME_CODEX / "auth.json").read_text())
        await host.write_text(
            "home/.codex/config.toml",
            f'[projects."{host.workdir}"]\ntrust_level = "trusted"\n',
        )

    events: list = []
    task = create_codex_task(
        process_id="codex-real-conv",
        name="real conversation proof",
        config=CodexTaskConfig(
            consumer_instructions="",
            mode="conversation",
            host_protocol=False,
            before_execute=_plant_identity,
        ),
    )
    # NOTE: obtaining the published conversation requires the Optio engine, as
    # in test_session_conversation.py. Wire it the same way (adhoc_define →
    # launch_and_await_result); register conv.on_event(events.append) BEFORE
    # the first send. Send a prompt that provokes reasoning + a short answer
    # (e.g. "Think step by step, then answer with just the word PONG."), await
    # the reply, then close.
    #
    #   optio = Optio(); await optio.init(mongo_db=..., prefix=...)
    #   await optio.adhoc_define(task)
    #   conv = await optio.launch_and_await_result("codex-real-conv", timeout=120)
    #   conv.on_event(events.append)
    #   done = asyncio.Queue(); conv.on_message(done.put_nowait)
    #   await conv.send("Think step by step, then answer with just PONG.")
    #   reply = await asyncio.wait_for(done.get(), 90)
    #   await conv.close()
    #
    # Fill this in against the as-built engine helpers at implementation time.
    raise NotImplementedError(
        "wire to the Optio engine exactly as test_session_conversation.py does"
    )

    assert reply and "PONG" in reply.upper()
    assert events, "no raw events captured from the real app-server"
    # Materialize the Layer-3 fixture (only the raw JSON-RPC dicts the reducer
    # consumes — no synthetic x-optio-* wrappers; those are added by the view).
    _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    _FIXTURE.write_text(json.dumps(events, indent=2))
```

> **Adaptation note.** This harness MUST reuse the exact engine wiring in `test_session_conversation.py` (`_make_optio`, `adhoc_define`, `launch_and_await_result`, `on_event`, `on_message`). The skeleton above marks the seam with `NotImplementedError` deliberately — the implementer replaces it with the real calls and removes the raise. The test is opt-in and never runs in the default suite, so the seam does not affect CI; but it MUST be completed (not left raising) before the task's commit, and MUST be run once locally against a real authed codex to (a) prove the conversation surface end-to-end and (b) produce `codex-events.json`.

- [ ] **Step 4: Produce the fixture, then re-run the Layer-3 replay.** With a real authed codex:

```bash
OPTIO_CODEX_CONVERSATION_TEST=1 .venv/bin/python -m pytest \
    packages/optio-codex/tests/test_real_codex_conversation.py -q
git status --short packages/optio-conversation-ui/src/__tests__/fixtures/codex-events.json
cd packages/optio-conversation-ui && node_modules/.bin/vitest run src/__tests__/codex-events-fixture.test.ts
```

The replay describe now runs (fixture present) and its three assertions pass — proving `reduceCodexEvent` handles the real interleaved-reasoning wire, not just the idealized fakes in `codex-events.test.ts`. **If no real authed codex is available to the implementer,** do NOT fabricate the fixture: leave it absent (the replay test skips, default suite green), commit the harness + replay test, and record in the carryover doc that the codex Layer-3 fixture is pending a real capture (tracked, not silent) — mirroring the honest gap the guide permits.

- [ ] **Step 5: Seed replant + resume relaunch + remote-SSH (opt-in).** Create `packages/optio-codex/tests/test_real_codex_seed_resume.py`:

```python
"""Opt-in real-codex E2E for the seed + resume + remote surfaces (never default).

Guide Testing Layer 2 checklist rows: seed replant (a fresh task starts
already-authenticated), resume (a relaunch picks up the prior session), and
remote-SSH (path/tty/callback assumptions that hold locally routinely break
remote). Billable/slow → opt-in:

    OPTIO_CODEX_SEED_RESUME_TEST=1 .venv/bin/python -m pytest \
        packages/optio-codex/tests/test_real_codex_seed_resume.py -q

Remote sub-case additionally requires OPTIO_CODEX_DEMO_SSH_HOST (skips locally).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

REAL_HOME_CODEX = Path.home() / ".codex"


def _authed() -> bool:
    try:
        data = json.loads((REAL_HOME_CODEX / "auth.json").read_text())
    except (OSError, ValueError):
        return False
    return bool(data.get("tokens") or data.get("OPENAI_API_KEY"))


pytestmark = pytest.mark.skipif(
    os.environ.get("OPTIO_CODEX_SEED_RESUME_TEST") != "1"
    or shutil.which("codex") is None
    or shutil.which("tmux") is None
    or not _authed(),
    reason="opt-in real-codex seed/resume test (OPTIO_CODEX_SEED_RESUME_TEST=1, "
    "codex+tmux on PATH, authed ~/.codex/auth.json)",
)


@pytest.mark.asyncio
async def test_seed_capture_then_replant(mongo_db, task_root):
    """Capture a seed from the operator identity, then launch a FRESH seeded
    task and assert it runs already-authenticated (reaches 'Codex is live' and
    emits DONE without any interactive login).

    Wire via create_codex_task with on_seed_saved on a setup-shaped task to
    capture, then seed_id=<captured> on a second task — mirror the fake-harness
    seed tests (test_session_seed.py) but with the real binary + real auth
    planted through before_execute, as test_real_codex_session.py does.
    """
    raise NotImplementedError("wire to test_session_seed.py's harness + real auth plant")


@pytest.mark.asyncio
async def test_resume_relaunch_picks_up_session(mongo_db, task_root):
    """Run a real iframe task with supports_resume=True to a snapshot, then
    relaunch with ctx.resume=True and assert it continues the SAME codex
    session id (snapshot sessionId round-trip) AND the relaunch argv carries
    the Gap-1 'you have been resumed' notice positional after `resume <id>`.
    Mirror test_session_resume.py's flow with the real binary."""
    raise NotImplementedError("wire to test_session_resume.py's flow + real codex")


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("OPTIO_CODEX_DEMO_SSH_HOST"),
    reason="remote real-codex E2E needs OPTIO_CODEX_DEMO_SSH_HOST",
)
async def test_remote_iframe_surface_end_to_end(mongo_db, task_root):
    """At least one surface end-to-end over SSH (guide row: remote path/tty
    assumptions break where local passes). Reuse _resolve_ssh_config from the
    demo, plant identity remotely, assert DONE."""
    raise NotImplementedError("wire to the SSH harness (test_session_remote.py) + real codex")
```

> **Adaptation note.** These three are opt-in and never run in the default suite; the `NotImplementedError` seams are replaced with the real engine/SSH wiring (patterns already exist in `test_session_seed.py`, `test_session_resume.py`, `test_session_remote.py`) before the commit. Each must be run once against the real binary (the SSH one against a real remote) to earn its row-30 checkmark; any that genuinely cannot be exercised in the implementer's environment is recorded as pending in the carryover doc — tracked, never a silent "green on fakes".

- [ ] **Step 6: Record the gated-surface ledger.** Append to `docs/2026-07-02-optio-codex-review-carryover.md` a short "Plan F (guide-delta) real-binary coverage" section listing, per surface, the gate env var and whether it was exercised against the real binary at implementation time (iframe done-when: `OPTIO_CODEX_REAL_SESSION_TEST`; sandbox: `OPTIO_CODEX_SANDBOX_ENFORCE_TEST`; conversation turn + Layer-3 capture: `OPTIO_CODEX_CONVERSATION_TEST`; seed replant / resume / remote: `OPTIO_CODEX_SEED_RESUME_TEST` [+ `OPTIO_CODEX_DEMO_SSH_HOST`]). Mark any surface whose real run is pending (e.g. no authed codex or no remote host available) as tracked-open.

- [ ] **Step 7: Run** both default suites — they must be green with the new opt-in tests skipped and the Layer-3 replay either passing (fixture present) or skipping (fixture pending):

```bash
.venv/bin/python -m pytest packages/optio-codex/tests/ -q 2>&1 | tail -5   # + 2 (or 3) new skips
cd packages/optio-conversation-ui && node_modules/.bin/vitest run 2>&1 | tail -6
node_modules/.bin/tsc --noEmit 2>&1 | tail -3
```

- [ ] **Step 8: Commit** `test(optio-codex): real-binary E2E breadth (conversation/seed/resume/remote) + Layer-3 codex wire replay`. Body lists the gate env vars and the row-30 coverage ledger; if the Layer-3 fixture is pending a real capture, say so.

---

## Self-review (performed at plan close; fixes folded in above)

**Coverage vs the five gaps.** Each gap maps to exactly one task with a grok source commit and a codex adaptation:
1. Resume PUSH → Task 1 (`ba739c2`): `build_resume_notice_args` + both bodies + unit + iframe-argv assertions. ✔
2. `auto_start` default → Task 2 (`87342cb`): default flip + full caller audit (2 tests + 1 demo comment; conversation/seed-setup inherit `False`). ✔
3. Seeded-teardown flush → Task 3 (`3f604c7`): `_teardown_aggressive` gates both subprocess teardowns; `cleanup_taskdir` intentionally excluded (post-flush); `fc1e5ef` verified N/A (Task 0 orphan cross-check). ✔
4. Direct-OIDC verify → Task 4 (`dd17f6d`): OpenAI OIDC discovery/refresh, `auth.json` mapping, fail-closed status, API-key alive-by-presence; **codex-specific divergence — probe kept as fallback**, so `run_codex_probe` is NOT removed. ✔
5. Real-binary breadth + Layer-3 → Task 5: conversation/seed/resume/remote opt-in E2E + capture-real-wire→replay-through-`reduceCodexEvent`; honest tracked-open ledger. ✔

**Placeholder scan.** No `TBD`/`XXX`/`...`-as-code. The only intentional `NotImplementedError` seams are in the **opt-in, never-default** real-binary tests (Task 5 Steps 3/5), each carrying an explicit adaptation note that they must be wired to as-built engine/SSH helpers and run once before commit — they cannot affect the default suite. Task 0's `<DATE>`/`<as discovered>` placeholders are investigation outputs that MUST be filled with probed values before Task 4's docstring is committed (called out inline).

**Type consistency.** `build_resume_notice_args(*, resuming: bool) -> list[str]` and `_teardown_aggressive(*, cancelled, seeded) -> bool` match grok's signatures exactly. `verify_and_refresh_seed`'s public signature is **unchanged** (keeps `ssh`/`install_dir` for the fallback probe — a deliberate divergence from grok, which dropped them). The `_finish`/`_verify_via_probe` `mark_dead` semantics are consistent: definitive-dead (no token / malformed / 4xx / probe-fail) → `dead`; transport/discovery → status untouched. Reducer fixture typed as `any[]` (matching the existing `codex-events.test.ts` and `reduceCodexEvent(state, ev: any, seq)`).

**Grok-parity check.** Ports mirror grok's commits with three consciously-documented codex divergences, each justified and recorded in the relevant commit body: (a) Gap 3 does not port `fc1e5ef`'s `exec`-prefix (codex has no tty-wrapper — native sandbox); (b) Gap 4 keeps the agent probe as a fallback (codex retains a working billable path grok discarded); (c) Gap 4 uses `last_refresh` freshness as the liveness gate instead of grok's userinfo call (codex tokens carry no cheap userinfo scope). The resume-notice "no `host_protocol` gate" decision matches grok and is verified against codex's `prompt.py` (System: convention taught in both protocol modes). Test-suite additions follow grok's env-gating style (`OPTIO_CODEX_*_TEST=1`, capability probes, never in the default suite).
