# optio-grok Stage 0 (MVP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working `optio-grok` package that runs Grok Build as an optio task in iframe/ttyd mode on the local host, coordinating via `optio.log` and reporting DONE/ERROR — verified by a fake-grok test suite.

**Architecture:** Adapt `optio-claudecode` (Grok's near-twin). Reuse the shared `optio_agents` log-protocol driver; supply grok-specific `body`/`prepare`/prompt/launch. Stage 0 is iframe/ttyd + local only; resume, seeds, conversation, isolation, fs-sandbox come in later stages.

**Tech Stack:** Python 3.11+, setuptools, pytest (asyncio), tmux + ttyd, `optio-core`/`optio-host`/`optio-agents`.

## Global Constraints

- Package `optio-grok`, `src/optio_grok/`, deps `optio-core>=0.3,<0.4`, `optio-host>=0.2,<0.3`, `optio-agents>=0.3,<0.4`, `asyncssh>=2.14`, `aiohttp>=3.9`. Mirror `optio-claudecode/pyproject.toml`.
- Grok binary is `grok`, on PATH (`~/.grok/bin/grok`), v0.2.77. Instructions file the agent reads is **`AGENTS.md`**.
- **Isolation (Stage-0 minimum, correctness-critical):** launch env MUST set `HOME=<workdir>/home`, `GROK_HOME=<workdir>/home/.grok`, AND neutralize grok's claude-compat by setting `CLAUDE_CONFIG_DIR=<workdir>/home/.claude` (empty) — else the operator's `~/.claude/CLAUDE.md`, claude settings, and claude hooks leak in (proven via `grok inspect`).
- **Leader:** always pass `--no-leader` so tasks never share a grok backend; never touch `~/.grok/leader.sock`.
- Browser mode: `get_protocol(browser="suppress")` (grok uses device-auth, no loopback redirect).
- Verbatim-copy from claudecode where noted (ttyd install, tmux/ttyd argv builders, tmux socket path, shims) — do NOT reinvent; adapt only names.
- TDD: failing test first, minimal impl, commit per task. Tests requiring tmux/ttyd/Mongo mirror claudecode's fixtures.

---

### Task 1: Package scaffold + registration

**Files:**
- Create: `packages/optio-grok/pyproject.toml`, `packages/optio-grok/src/optio_grok/__init__.py`, `packages/optio-grok/README.md`
- Modify: `packages/optio-demo/Makefile` (add `optio-grok` to `LOCAL_PKGS` + an `install -e ../optio-grok` line), `packages/optio-demo/pyproject.toml` (add `optio-grok` dep), root `Makefile` (`RELEASABLE_PY` += `optio-grok`)
- Test: `packages/optio-grok/tests/test_import.py`

**Interfaces:**
- Produces: importable package `optio_grok` re-exporting `create_grok_task`, `run_grok_session`, `GrokTaskConfig`, `PermissionMode`, `DeliverableCallback`, `HookCallback`, `SSHConfig`, `HookContext`, `HookContextProtocol`, `HostCommandError`, `RunResult`.

- [ ] **Step 1: Write `pyproject.toml`** — copy `optio-claudecode/pyproject.toml`, change `name = "optio-grok"`, `version = "0.1.0"`, description "Run Grok Build (xAI) as an optio task; local subprocess or remote via SSH; ttyd-served TUI iframe.", keep everything else.

- [ ] **Step 2: Write `__init__.py`** — mirror claudecode's, importing from `optio_grok.session` / `.types`; drop seed exports (Stage 3). Include the `logging.getLogger("asyncssh").setLevel(WARNING)` line.

- [ ] **Step 3: Failing import test**
```python
# tests/test_import.py
def test_public_surface():
    import optio_grok
    assert hasattr(optio_grok, "create_grok_task")
    assert hasattr(optio_grok, "GrokTaskConfig")
```
- [ ] **Step 4: Register + editable install.** Add wiring to demo Makefile/pyproject + root Makefile per Global Constraints; `pip install -e packages/optio-grok` into the repo `.venv`.
- [ ] **Step 5: Run** `pytest packages/optio-grok/tests/test_import.py -v` → PASS. **Commit** `feat(optio-grok): package scaffold + registration`.

---

### Task 2: `types.py` — GrokTaskConfig

**Files:** Create `src/optio_grok/types.py`; Test `tests/test_config.py`

**Interfaces:**
- Produces: `GrokTaskConfig` dataclass; `PermissionMode = Literal["default","acceptEdits","auto","dontAsk","bypassPermissions","plan"]`; re-exported `DeliverableCallback`, `HookCallback` (from `optio_agents`).
- Consumed by: Tasks 4, 5.

**Stage-0 fields** (types/defaults): `consumer_instructions: str` (required); `env: dict[str,str]|None=None`; `scrub_env: list[str]|None=None`; `permission_mode: PermissionMode|None=None`; `allowed_tools: list[str]|None=None`; `disallowed_tools: list[str]|None=None`; `model: str|None=None`; `effort: str|None=None`; `reasoning_effort: str|None=None`; `ssh: SSHConfig|None=None`; `install_if_missing: bool=True`; `install_ttyd_if_missing: bool=True`; `grok_install_dir: str|None=None`; `ttyd_install_dir: str|None=None`; `auto_start: bool=True`; `no_leader: bool=True`; `before_execute: HookCallback|None=None`; `after_execute: HookCallback|None=None`; `on_deliverable: DeliverableCallback|None=None`; `supports_resume: bool=False`; `mode: Literal["iframe"]="iframe"`; `host_protocol: bool=True`. `__post_init__` validates `permission_mode` against the literal set and absolute install dirs.

- [ ] **Step 1: Failing test**
```python
# tests/test_config.py
import pytest
from optio_grok import GrokTaskConfig
def test_defaults_and_validation():
    c = GrokTaskConfig(consumer_instructions="do it")
    assert c.mode == "iframe" and c.no_leader is True and c.host_protocol is True
    with pytest.raises(ValueError):
        GrokTaskConfig(consumer_instructions="x", permission_mode="nope")
```
- [ ] **Step 2:** Run → FAIL (no module).
- [ ] **Step 3:** Implement `types.py` (adapt `ClaudeCodeTaskConfig`, keep only Stage-0 fields above; add `effort`/`reasoning_effort`/`no_leader`).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(optio-grok): GrokTaskConfig`.

---

### Task 3: `prompt.py` — AGENTS.md composition

**Files:** Create `src/optio_grok/prompt.py`; Test `tests/test_prompt.py`

**Interfaces:**
- Produces: `def compose_agents_md(consumer_instructions: str, *, host_protocol: bool = True) -> str`.
- Consumed by: Task 5 (`_prepare` writes it to `<workdir>/AGENTS.md`).

Adapt opencode's `compose_agents_md` (grok reads AGENTS.md): assemble intro + `build_log_channel_prompt(ProtocolFeatures(browser="suppress"))` keyword docs + task framing + `consumer_instructions`. When `host_protocol=False`, omit keyword docs (Stage-6 concern; keep the branch).

- [ ] **Step 1: Failing test**
```python
# tests/test_prompt.py
from optio_grok.prompt import compose_agents_md
def test_agents_md_has_protocol_and_instructions():
    md = compose_agents_md("BUILD THE THING", host_protocol=True)
    assert "BUILD THE THING" in md
    assert "STATUS:" in md and "DELIVERABLE:" in md and "DONE" in md
```
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement using `from optio_agents.protocol.prompt import build_log_channel_prompt` and `from optio_agents.protocol.features import ProtocolFeatures` (confirm import paths against the reference wrappers' `prompt.py`).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(optio-grok): AGENTS.md prompt composition`.

---

### Task 4: `host_actions.py` — launch, env, DONE/ERROR, ttyd/tmux

**Files:** Create `src/optio_grok/host_actions.py`; Test `tests/test_host_actions.py`

**Interfaces:**
- Produces:
  - `async def ensure_grok_installed(hook_ctx, *, install_if_missing=True, install_dir=None) -> str` — Stage 0: resolve `grok` on the host (`command -v grok`, or `<install_dir>/grok`); raise if absent and `not install_if_missing`. (Binary cache = Stage 5.)
  - `async def ensure_ttyd_installed(hook_ctx, *, install_if_missing=True, install_dir=None) -> str` — **copy verbatim** from claudecode.
  - `def build_grok_flags(*, permission_mode, allowed_tools, disallowed_tools, model, effort, reasoning_effort, no_leader, resuming) -> list[str]` — emits `--permission-mode`, `--allow` (repeat per rule), `--disallowed-tools` (comma), `--model`, `--effort`, `--reasoning-effort`, `--no-leader`, and `-c` when `resuming` (resuming always False in Stage 0).
  - `def build_auto_start_args(*, auto_start, prompt="Read AGENTS.md and execute the task it describes") -> list[str]` — returns `[prompt]` (positional) when `auto_start`, else `[]`.
  - `def _build_grok_shell_command(*, grok_path, workdir, extra_env, grok_flags, local_mode=False) -> tuple[list[str], str]` — env `[HOME=<wd>/home, PATH=<wd>/home/.local/bin:<base>, GROK_HOME=<wd>/home/.grok, CLAUDE_CONFIG_DIR=<wd>/home/.claude, *extras]`; bash payload `cd <wd> && <grok argv>; rc=$?; if DONE else ERROR: grok exited $rc`. Drop netns/claustrum/debug branches (adapt claudecode's simple path).
  - `build_tmux_session_argv(...)`, `build_ttyd_attach_argv(...)`, `_tmux_socket_path`, `launch_ttyd_with_grok(...)`, `tmux_session_alive(...)`, `send_text_to_grok(...)`, `_require_tmux` — **adapt from claudecode verbatim** (rename `claude`→`grok`), `session_name="optio"`.

- [ ] **Step 1: Failing test**
```python
# tests/test_host_actions.py
from optio_grok.host_actions import _build_grok_shell_command
def test_env_isolation_and_done_error():
    env, cmd = _build_grok_shell_command(
        grok_path="/x/grok", workdir="/w/task", extra_env=None,
        grok_flags=["--no-leader"],
    )
    assert "HOME=/w/task/home" in env
    assert "GROK_HOME=/w/task/home/.grok" in env
    assert "CLAUDE_CONFIG_DIR=/w/task/home/.claude" in env   # claude-compat neutralized
    assert "echo DONE" in cmd and "ERROR: grok exited" in cmd
    assert "--no-leader" in cmd
```
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement (adapt from claudecode `host_actions.py`; ttyd install + tmux/ttyd argv copied).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(optio-grok): host_actions launch + env isolation + DONE/ERROR`.

---

### Task 5: `session.py` — iframe body + factory

**Files:** Create `src/optio_grok/session.py`; Test covered by Task 6 (integration).

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces:
  - `async def run_grok_session(ctx: ProcessContext, config: GrokTaskConfig) -> None` — builds `host` (`LocalHost`/`RemoteHost` by `config.ssh`); `protocol = get_protocol(browser="suppress")`; defines `_prepare` (ensure grok + ttyd, write `AGENTS.md` via `compose_agents_md`), `_grok_body` (iframe: build flags, `launch_ttyd_with_grok`, `establish_tunnel`, `set_widget_upstream`/`set_widget_data({"iframeSrc":"{widgetProxyUrl}/"})`, poll `tmux_session_alive` while `should_continue()`), `_agent_sender` (tmux `send_text_to_grok`); calls `run_log_protocol_session(host, ctx, body=_grok_body, prepare=_prepare, on_deliverable=config.on_deliverable, after_execute=config.after_execute, protocol=protocol, agent_sender=_agent_sender, keywords=config.host_protocol)`; wraps `_SessionFailed` → `RuntimeError`; `finally` tears down the tmux/ttyd tree + `cleanup_taskdir` + `disconnect`.
  - `def create_grok_task(process_id, name, config, description=None, metadata=None) -> TaskInstance` — `ui_widget="iframe"`, `supports_resume=config.supports_resume`, `execute` closure over `run_grok_session`.

- [ ] **Step 1:** Implement `session.py` adapting claudecode's iframe path only (no conversation/seed/resume/snapshot/input-listener branches). Import `run_log_protocol_session`, `_SessionFailed`, `get_protocol` from `optio_agents` (confirm exact import paths against claudecode `session.py`).
- [ ] **Step 2:** `python -c "import optio_grok; optio_grok.create_grok_task"` → no error.
- [ ] **Step 3: Commit** `feat(optio-grok): session iframe body + create_grok_task`.

---

### Task 6: Test harness + local session tests

**Files:** Create `tests/fake_grok.py`, `tests/grok-shim.sh`, `tests/ttyd-shim.sh`, `tests/conftest.py`, `tests/test_session_local.py`

**Interfaces:** Consumes Task 5.

- [ ] **Step 1:** `ttyd-shim.sh` — **copy** claudecode's verbatim. `grok-shim.sh` — trampoline `exec python3 "$SCRIPT_DIR/fake_grok.py" "$@"`. `fake_grok.py` — scenario mode (`FAKE_GROK_SCENARIO` ∈ `happy|deliverable|error`): writes STATUS/DELIVERABLE/DONE/ERROR lines to `./optio.log`; `--version` prints `grok 0.2.77 (fake)`. (Adapt claudecode's `fake_claude.py` scenario mode; drop stream-json.)
- [ ] **Step 2:** `conftest.py` — adapt claudecode's `shim_install_dir` (symlinks `grok`→`grok-shim.sh`, `ttyd`→`ttyd-shim.sh`), `grok_cache_dir` (or pass `grok_install_dir` pointing at the shim dir), `task_root` (short `/tmp/gktr-*`), and the `ctx_and_captures` + `mongo_db` fixtures.
- [ ] **Step 3: Failing/real tests**
```python
# tests/test_session_local.py — mirrors claudecode test_session_local.py
async def test_local_deliverable_callback_fired(shim_install_dir, ctx_and_captures, monkeypatch):
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_GROK_SCENARIO", "deliverable")
    captured = []
    async def on_deliverable(hook_ctx, path, text): captured.append((path, text))
    task = create_grok_task(process_id="grok-local-deliverable", name="d",
        config=GrokTaskConfig(consumer_instructions="hand back a file",
            grok_install_dir=str(shim_install_dir), ttyd_install_dir=str(shim_install_dir),
            on_deliverable=on_deliverable))
    await task.execute(ctx)
    assert len(captured) == 1

async def test_local_error_raises(shim_install_dir, ctx_and_captures, monkeypatch):
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_GROK_SCENARIO", "error")
    task = create_grok_task(process_id="grok-local-error", name="e",
        config=GrokTaskConfig(consumer_instructions="fail",
            grok_install_dir=str(shim_install_dir), ttyd_install_dir=str(shim_install_dir)))
    with pytest.raises(RuntimeError):
        await task.execute(ctx)
```
- [ ] **Step 4:** Run `pytest packages/optio-grok/tests -v` (needs tmux + Mongo) → PASS.
- [ ] **Step 5: Commit** `test(optio-grok): fake-grok harness + local iframe session tests`.

---

## Self-Review

- **Spec coverage:** Stage 0 row of the spec (task runs iframe/ttyd + DONE/ERROR, local, AGENTS.md, GROK_HOME) ↔ Tasks 1–6. Claude-compat neutralization (spec Decision 3) ↔ Task 4 env + Task 4 test. `--no-leader` (Decision 4) ↔ Task 4. AGENTS.md (Decision 5) ↔ Task 3. Deferred: resume/seeds/conversation/isolation/demo (later stages) — intentionally out of Stage 0.
- **Placeholders:** none — every task has concrete deltas + test code; verbatim-copy items name their claudecode source.
- **Type consistency:** `create_grok_task`/`run_grok_session`/`GrokTaskConfig`/`compose_agents_md`/`_build_grok_shell_command`/`build_grok_flags`/`launch_ttyd_with_grok` used consistently across Tasks 2–6.

## Open item resolved during build
- Confirm exact `optio_agents` import paths (`run_log_protocol_session`, `_SessionFailed`, `get_protocol`, `build_log_channel_prompt`, `ProtocolFeatures`) against claudecode/opencode `session.py`/`prompt.py` before writing (they are the authority, not this plan).
