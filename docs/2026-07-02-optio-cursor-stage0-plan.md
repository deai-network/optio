# optio-cursor Stage 0 (MVP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working `optio-cursor` package that runs Cursor CLI as an optio task in iframe/ttyd mode on the local host, coordinating via `optio.log` and reporting DONE/ERROR — verified by a fake-cursor test suite.

**Architecture:** Adapt `optio-grok` (Cursor's near-twin; see `docs/2026-07-02-optio-cursor-design.md`). Reuse the shared `optio_agents` log-protocol driver; supply cursor-specific `body`/`prepare`/prompt/launch. Stage 0 is iframe/ttyd + local only; resume, seeds, conversation, isolation, fs-sandbox come in later stages.

**Tech Stack:** Python 3.11+, setuptools, pytest (asyncio), tmux + ttyd, `optio-core`/`optio-host`/`optio-agents`.

## Global Constraints

- Package `optio-cursor`, `src/optio_cursor/`, deps `optio-core>=0.3,<0.4`, `optio-host>=0.2,<0.3`, `optio-agents>=0.3,<0.4`, `asyncssh>=2.14`, `aiohttp>=3.9`. Mirror `optio-grok/pyproject.toml`.
- Agent binary is **`cursor-agent`** (v2026.07.01-41b2de7; `~/.local/bin/cursor-agent` → `~/.local/share/cursor-agent/versions/<v>/cursor-agent`). **Never** the `cursor` IDE binary. Instructions file the agent reads is **`AGENTS.md`**.
- **Isolation (Stage-0 minimum):** launch env MUST set `HOME=<workdir>/home` (cursor derives `~/.cursor` + `~/.cache` from `$HOME` — verified) plus `XDG_CONFIG_HOME`/`XDG_CACHE_HOME`/`XDG_DATA_HOME` under `<workdir>/home` for hygiene. No claude-compat neutralization needed (cursor does not ingest claude config) and no leader/daemon concerns.
- **Permissions are config-planted, not argv:** cursor has no `--allow/--deny` argv; rules live in `<home>/.cursor/cli-config.json` (`permissions.allow`/`permissions.deny`, `approvalMode`). Stage-0 `_prepare` plants this file when the config sets rules. Argv-level knobs are `--force`, `--auto-review`, `--sandbox enabled|disabled`, `--model`.
- Browser mode: `get_protocol(browser="redirect")` — cursor login prints a URL when `NO_OPEN_BROWSER=1`; surface it via `BROWSER:` (claudecode-style, NOT grok's suppress). Set `NO_OPEN_BROWSER=1` in the launch env.
- Verbatim-copy from grok where noted (ttyd install, tmux/ttyd argv builders, tmux socket path, shims) — do NOT reinvent; adapt only names.
- TDD: failing test first, minimal impl, commit per task. Tests requiring tmux/ttyd/Mongo mirror grok's fixtures.

---

### Task 1: Package scaffold + registration

**Files:**
- Create: `packages/optio-cursor/pyproject.toml`, `packages/optio-cursor/src/optio_cursor/__init__.py`, `packages/optio-cursor/README.md`
- Modify: `packages/optio-demo/Makefile` (add `optio-cursor` to `LOCAL_PKGS` + an `install -e ../optio-cursor` line), `packages/optio-demo/pyproject.toml` (add `optio-cursor` dep), root `Makefile` (`RELEASABLE_PY` += `optio-cursor`, `PY_PACKAGES` += `optio-cursor`)
- Test: `packages/optio-cursor/tests/test_import.py`

**Interfaces:**
- Produces: importable package `optio_cursor` re-exporting `create_cursor_task`, `run_cursor_session`, `CursorTaskConfig`, `DeliverableCallback`, `HookCallback`, `SSHConfig`, `HookContext`, `HookContextProtocol`, `HostCommandError`, `RunResult`.

- [ ] **Step 1: Write `pyproject.toml`** — copy `optio-grok/pyproject.toml`, change `name = "optio-cursor"`, `version = "0.1.0"`, description "Run Cursor CLI (cursor-agent) as an optio task; local subprocess or remote via SSH; ttyd-served TUI iframe.", keep everything else.
- [ ] **Step 2: Write `__init__.py`** — mirror grok's, importing from `optio_cursor.session` / `.types`; drop seed exports (Stage 3). Include the `logging.getLogger("asyncssh").setLevel(WARNING)` line.
- [ ] **Step 3: Failing import test**
```python
# tests/test_import.py
def test_public_surface():
    import optio_cursor
    assert hasattr(optio_cursor, "create_cursor_task")
    assert hasattr(optio_cursor, "CursorTaskConfig")
```
- [ ] **Step 4: Register + editable install.** Add wiring to demo Makefile/pyproject + root Makefile per Global Constraints; `pip install -e packages/optio-cursor` into the repo `.venv`.
- [ ] **Step 5: Run** `pytest packages/optio-cursor/tests/test_import.py -v` → PASS. **Commit** `feat(optio-cursor): package scaffold + registration`.

---

### Task 2: `types.py` — CursorTaskConfig

**Files:** Create `src/optio_cursor/types.py`; Test `tests/test_config.py`

**Interfaces:**
- Produces: `CursorTaskConfig` dataclass; re-exported `DeliverableCallback`, `HookCallback` (from `optio_agents`).
- Consumed by: Tasks 4, 5.

**Stage-0 fields** (types/defaults): `consumer_instructions: str` (required); `env: dict[str,str]|None=None`; `scrub_env: list[str]|None=None`; `allowed_tools: list[str]|None=None` (→ `permissions.allow` in cli-config); `disallowed_tools: list[str]|None=None` (→ `permissions.deny`); `force: bool=False` (`--force`); `auto_review: bool=False` (`--auto-review`); `sandbox: Literal["enabled","disabled"]|None=None` (`--sandbox`); `model: str|None=None`; `api_key: str|None=None` (→ `CURSOR_API_KEY` in launch env, never argv); `ssh: SSHConfig|None=None`; `install_if_missing: bool=True`; `install_ttyd_if_missing: bool=True`; `cursor_install_dir: str|None=None`; `ttyd_install_dir: str|None=None`; `auto_start: bool=True`; `before_execute: HookCallback|None=None`; `after_execute: HookCallback|None=None`; `on_deliverable: DeliverableCallback|None=None`; `supports_resume: bool=False`; `mode: Literal["iframe"]="iframe"`; `host_protocol: bool=True`. `__post_init__` validates `sandbox` against the literal set and absolute install dirs.

- [ ] **Step 1: Failing test**
```python
# tests/test_config.py
import pytest
from optio_cursor import CursorTaskConfig
def test_defaults_and_validation():
    c = CursorTaskConfig(consumer_instructions="do it")
    assert c.mode == "iframe" and c.host_protocol is True and c.force is False
    with pytest.raises(ValueError):
        CursorTaskConfig(consumer_instructions="x", sandbox="nope")
```
- [ ] **Step 2:** Run → FAIL (no module).
- [ ] **Step 3:** Implement `types.py` (adapt `GrokTaskConfig`, keep only Stage-0 fields above; cursor-specific: `force`, `auto_review`, `sandbox`, `api_key`; drop `permission_mode`/`effort`/`reasoning_effort`/`no_leader`).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(optio-cursor): CursorTaskConfig`.

---

### Task 3: `prompt.py` — AGENTS.md composition

**Files:** Create `src/optio_cursor/prompt.py`; Test `tests/test_prompt.py`

**Interfaces:**
- Produces: `def compose_agents_md(consumer_instructions: str, *, host_protocol: bool = True) -> str`.
- Consumed by: Task 5 (`_prepare` writes it to `<workdir>/AGENTS.md`).

Adapt grok's `compose_agents_md` verbatim (both read AGENTS.md): assemble intro + `build_log_channel_prompt(ProtocolFeatures(browser="redirect"))` keyword docs + task framing + `consumer_instructions`. When `host_protocol=False`, omit keyword docs (Stage-6 concern; keep the branch). Only delta vs grok: browser feature is `redirect`, so the `BROWSER:` keyword docs ARE included.

- [ ] **Step 1: Failing test**
```python
# tests/test_prompt.py
from optio_cursor.prompt import compose_agents_md
def test_agents_md_has_protocol_and_instructions():
    md = compose_agents_md("BUILD THE THING", host_protocol=True)
    assert "BUILD THE THING" in md
    assert "STATUS:" in md and "DELIVERABLE:" in md and "DONE" in md
    assert "BROWSER:" in md
```
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement (grok `prompt.py` is the authority for import paths).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(optio-cursor): AGENTS.md prompt composition`.

---

### Task 4: `host_actions.py` — launch, env, DONE/ERROR, ttyd/tmux

**Files:** Create `src/optio_cursor/host_actions.py`; Test `tests/test_host_actions.py`

**Interfaces:**
- Produces:
  - `async def ensure_cursor_installed(hook_ctx, *, install_if_missing=True, install_dir=None) -> str` — Stage 0: resolve `cursor-agent` on the host (`command -v cursor-agent`, or `<install_dir>/cursor-agent`); raise if absent and `not install_if_missing`. (Binary cache = Stage 5.)
  - `async def ensure_ttyd_installed(hook_ctx, *, install_if_missing=True, install_dir=None) -> str` — **copy verbatim** from grok.
  - `def build_cursor_flags(*, force, auto_review, sandbox, model, resuming) -> list[str]` — emits `--force`, `--auto-review`, `--sandbox <v>`, `--model <m>`, and `--continue` when `resuming` (always False in Stage 0).
  - `def build_cli_config(*, allowed_tools, disallowed_tools) -> dict | None` — returns the `cli-config.json` payload (`{"version":1,"permissions":{"allow":[...],"deny":[...]},"approvalMode":"allowlist"}`) when any rules are set, else None. Planted by `_prepare` at `<workdir>/home/.cursor/cli-config.json`.
  - `def build_auto_start_args(*, auto_start, prompt="Read AGENTS.md and execute the task it describes") -> list[str]` — returns `[prompt]` (positional) when `auto_start`, else `[]`.
  - `def _build_cursor_shell_command(*, cursor_path, workdir, extra_env, cursor_flags, local_mode=False) -> tuple[list[str], str]` — env `[HOME=<wd>/home, PATH=<wd>/home/.local/bin:<base>, XDG_CONFIG_HOME=<wd>/home/.config, XDG_CACHE_HOME=<wd>/home/.cache, XDG_DATA_HOME=<wd>/home/.local/share, NO_OPEN_BROWSER=1, *extras]`; bash payload `cd <wd> && <cursor argv>; rc=$?; if DONE else ERROR: cursor-agent exited $rc`. Adapt grok's simple path.
  - `build_tmux_session_argv(...)`, `build_ttyd_attach_argv(...)`, `_tmux_socket_path`, `launch_ttyd_with_cursor(...)`, `tmux_session_alive(...)`, `send_text_to_cursor(...)`, `_require_tmux` — **adapt from grok verbatim** (rename `grok`→`cursor`), `session_name="optio"`.

- [ ] **Step 1: Failing test**
```python
# tests/test_host_actions.py
from optio_cursor.host_actions import _build_cursor_shell_command, build_cli_config
def test_env_isolation_and_done_error():
    env, cmd = _build_cursor_shell_command(
        cursor_path="/x/cursor-agent", workdir="/w/task", extra_env=None,
        cursor_flags=["--force"],
    )
    assert "HOME=/w/task/home" in env
    assert "NO_OPEN_BROWSER=1" in env
    assert "echo DONE" in cmd and "ERROR: cursor-agent exited" in cmd
    assert "--force" in cmd

def test_cli_config_rules():
    cfg = build_cli_config(allowed_tools=["Shell(ls)"], disallowed_tools=None)
    assert cfg["permissions"]["allow"] == ["Shell(ls)"]
    assert build_cli_config(allowed_tools=None, disallowed_tools=None) is None
```
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement (adapt from grok `host_actions.py`; ttyd install + tmux/ttyd argv copied).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(optio-cursor): host_actions launch + env isolation + DONE/ERROR`.

---

### Task 5: `session.py` — iframe body + factory

**Files:** Create `src/optio_cursor/session.py`; Test covered by Task 6 (integration).

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces:
  - `async def run_cursor_session(ctx: ProcessContext, config: CursorTaskConfig) -> None` — builds `host` (`LocalHost`/`RemoteHost` by `config.ssh`); `protocol = get_protocol(browser="redirect")`; defines `_prepare` (ensure cursor-agent + ttyd, write `AGENTS.md` via `compose_agents_md`, plant `cli-config.json` when `build_cli_config` returns rules), `_cursor_body` (iframe: build flags, `launch_ttyd_with_cursor`, `establish_tunnel`, `set_widget_upstream`/`set_widget_data({"iframeSrc":"{widgetProxyUrl}/"})`, poll `tmux_session_alive` while `should_continue()`), `_agent_sender` (tmux `send_text_to_cursor`); calls `run_log_protocol_session(...)` exactly as grok does; wraps `_SessionFailed` → `RuntimeError`; `finally` tears down the tmux/ttyd tree + `cleanup_taskdir` + `disconnect`.
  - `def create_cursor_task(process_id, name, config, description=None, metadata=None) -> TaskInstance` — `ui_widget="iframe"`, `supports_resume=config.supports_resume`, `execute` closure over `run_cursor_session`.

- [ ] **Step 1:** Implement `session.py` adapting grok's iframe path only (no conversation/seed/resume/snapshot/input-listener branches). grok `session.py` is the import-path authority.
- [ ] **Step 2:** `python -c "import optio_cursor; optio_cursor.create_cursor_task"` → no error.
- [ ] **Step 3: Commit** `feat(optio-cursor): session iframe body + create_cursor_task`.

---

### Task 6: Test harness + local session tests

**Files:** Create `tests/fake_cursor.py`, `tests/cursor-shim.sh`, `tests/ttyd-shim.sh`, `tests/conftest.py`, `tests/test_session_local.py`

**Interfaces:** Consumes Task 5.

- [ ] **Step 1:** `ttyd-shim.sh` — **copy** grok's verbatim. `cursor-shim.sh` — trampoline `exec python3 "$SCRIPT_DIR/fake_cursor.py" "$@"` (installed under the name `cursor-agent`). `fake_cursor.py` — scenario mode (`FAKE_CURSOR_SCENARIO` ∈ `happy|deliverable|error`): writes STATUS/DELIVERABLE/DONE/ERROR lines to `./optio.log`; `--version` prints `2026.07.01-fake`. (Adapt grok's `fake_grok.py` scenario mode.)
- [ ] **Step 2:** `conftest.py` — adapt grok's `shim_install_dir` (symlinks `cursor-agent`→`cursor-shim.sh`, `ttyd`→`ttyd-shim.sh`), cache dir fixture, `task_root` (short `/tmp/curtr-*`), and the `ctx_and_captures` + `mongo_db` fixtures.
- [ ] **Step 3: Failing/real tests** — mirror grok `test_session_local.py`: `test_local_deliverable_callback_fired` + `test_local_error_raises` (rename grok→cursor, `FAKE_CURSOR_SCENARIO`).
- [ ] **Step 4:** Run `pytest packages/optio-cursor/tests -v` (needs tmux + Mongo) → PASS.
- [ ] **Step 5: Commit** `test(optio-cursor): fake-cursor harness + local iframe session tests`.

---

## Self-Review

- **Spec coverage:** Stage 0 row of the spec (task runs iframe/ttyd + DONE/ERROR, local, AGENTS.md, per-task HOME) ↔ Tasks 1–6. Per-task `$HOME` isolation (spec Decision 3) ↔ Task 4 env + test. Config-planted permissions ↔ Task 4 `build_cli_config`. AGENTS.md (Decision 4) ↔ Task 3. Browser=redirect (Decision 5 login path groundwork) ↔ Tasks 3–5. Deferred: resume/seeds/conversation/isolation/demo (later stages) — intentionally out of Stage 0.
- **Placeholders:** none — every task has concrete deltas + test code; verbatim-copy items name their grok source.
- **Type consistency:** `create_cursor_task`/`run_cursor_session`/`CursorTaskConfig`/`compose_agents_md`/`_build_cursor_shell_command`/`build_cursor_flags`/`build_cli_config`/`launch_ttyd_with_cursor` used consistently across Tasks 2–6.

## Open item resolved during build
- Confirm exact `optio_agents` import paths against grok `session.py`/`prompt.py` before writing (they are the authority, not this plan).
