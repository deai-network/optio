# optio-opencode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `optio-opencode` Python package per `docs/2026-04-22-optio-opencode-design.md`, the adjacent `IframeWidget` template-substitution change in `optio-ui`, and a local-mode reference demo task in `optio-demo` that exercises the whole stack end-to-end.

**Architecture:** Follows the design spec. Adds a new Python package `packages/optio-opencode` that orchestrates `opencode web` (local subprocess or remote via asyncssh) as an optio task. Public surface is a factory `create_opencode_task(...) → TaskInstance`; consumers pass an `OpencodeTaskConfig` (system prompt + opencode-side config + optional SSH + deliverable callback). Internals are split across small focused modules (types, prompt composer, line parser, host abstraction, session state machine). Prerequisite optio-ui change: `IframeWidget.tsx` gains `{widgetProxyUrl}` template substitution in `widgetData.localStorageOverrides`, which lets opencode's SPA find its server through the widget proxy.

**Tech Stack:**

- **Python 3.12** via the pyenv shim at `/home/csillag/deai/pyenv/shims/python`. Test runner: `pytest` with `asyncio_mode = "auto"` and `testpaths = ["tests"]` (matches optio-core).
- **New Python runtime deps:** `asyncssh` (remote host mode), `aiofiles` (local tail).
- **Existing Python runtime dep:** `optio-core` (workspace).
- **Test infra for remote mode:** `linuxserver/openssh-server` Docker container exposed on localhost.
- **MongoDB:** via Docker only per workspace convention. Integration tests pick up `MONGO_URL` env var (default `mongodb://localhost:27017`).
- **TypeScript side:** existing optio-ui Vitest + `@testing-library/react` setup. tsc via `packages/optio-ui/node_modules/.bin/tsc` — never `npx`.
- **Workspace conventions** (from root `AGENTS.md` and `~/.claude` memory): plans/specs flat under `docs/`; no `Co-Authored-By` lines; one commit per executed plan (NOT one per task); create an in-place feature branch (not main, not worktree) before executing; do not auto-proceed from plan to execution without explicit user confirmation.

---

## Scope constraints (read first)

- **Spec is authoritative.** Every design decision lives in `docs/2026-04-22-optio-opencode-design.md`. If a task here and the spec disagree, stop and ask — do not silently diverge.
- **One commit per plan.** No per-task commits. The final task (Task 16) stages everything and writes one commit. If a task description in the TDD section ever says "commit," that is a mistake — ignore it.
- **Local mode is the MVP target for the demo.** Remote mode is implemented and integration-tested, but the reference demo task in `optio-demo` wires only local mode so a developer without SSH setup can run it.
- **No Playwright, no automated E2E.** The demo is exclusively human-driven (Section 10 of the spec).
- **Path discipline.** Every `DELIVERABLE:` path is validated to resolve inside `<workdir>/` before any fetch happens. This is a security surface; tests must cover escape attempts explicitly.
- **Don't use npx.** For any TypeScript tooling, use `packages/optio-ui/node_modules/.bin/tsc` (and similar). `npm run test` / `npm run build` inside the package dir is fine because those invoke local binaries.

---

## File Map

### New files (all under `packages/optio-opencode/`)

| File | Responsibility |
|------|----------------|
| `pyproject.toml` | Package metadata, deps on `optio-core`, `asyncssh`, `aiofiles`; dev-dep on `pytest` + `pytest-asyncio`. |
| `AGENTS.md` | Package-level API cheatsheet — public types, factory signature, base-prompt contract with opencode. |
| `src/optio_opencode/__init__.py` | Public re-exports: `create_opencode_task`, `OpencodeTaskConfig`, `SSHConfig`, `DeliverableCallback`. |
| `src/optio_opencode/types.py` | Dataclasses `SSHConfig`, `OpencodeTaskConfig`; `DeliverableCallback` type alias. |
| `src/optio_opencode/prompt.py` | `BASE_PROMPT` constant (the coordination boilerplate) + `compose_agents_md(consumer_instructions)`. |
| `src/optio_opencode/logparse.py` | `parse_log_line(line) → LogEvent` + `validate_deliverable_path(path, workdir)`. |
| `src/optio_opencode/host.py` | `Host` Protocol + `LocalHost` + `RemoteHost` (asyncssh) + shared helpers. |
| `src/optio_opencode/session.py` | `run_opencode_session(ctx, config)` — the full state machine; `create_opencode_task(...)` factory. |
| `tests/conftest.py` | Shared fixtures: `tmp_workdir` (tempdir auto-deleted) and `mongo_db` (copied from optio-core's pattern) for integration tests. |
| `tests/fake_opencode.py` | Scripted test-double subprocess: binds a port, prints URL, writes scripted sequences to `optio.log` and `deliverables/`. |
| `tests/test_types.py` | Round-trips + defaults for `SSHConfig` / `OpencodeTaskConfig`. |
| `tests/test_logparse.py` | `parse_log_line` + `validate_deliverable_path` — full keyword coverage + edge cases. |
| `tests/test_prompt.py` | Golden-string test for `compose_agents_md`. |
| `tests/test_host_local.py` | `LocalHost` unit tests using real tempdirs + `fake_opencode.py`. |
| `tests/test_session_local.py` | End-to-end integration over `LocalHost` + `fake_opencode.py` + a real `ProcessContext` backed by mongo. |
| `tests/test_session_remote.py` | End-to-end integration over `RemoteHost` + a Docker `linuxserver/openssh-server` container. Skip-on-no-docker. |
| `tests/docker-compose.sshd.yml` | Compose file launching an SSH container for `test_session_remote.py`. |

### Modified files

| File | Change |
|------|--------|
| `packages/optio-ui/src/widgets/IframeWidget.tsx` | In the `useEffect` that writes `localStorageOverrides`, substitute the literal token `{widgetProxyUrl}` in every VALUE with `props.widgetProxyUrl`. Other token shapes pass through unchanged. |
| `packages/optio-ui/src/__tests__/IframeWidget.test.tsx` | Add one `it(...)` asserting that a value `"{widgetProxyUrl}"` is resolved to `props.widgetProxyUrl` before the `setItem` call. |
| `packages/optio-demo/pyproject.toml` | Append `"optio-opencode"` to `dependencies`. |
| `packages/optio-demo/src/optio_demo/tasks/__init__.py` | Import `get_tasks` from a new `optio_demo.tasks.opencode` module and spread into the `get_task_definitions` return. |
| `packages/optio-demo/src/optio_demo/tasks/opencode.py` | New file: builds a `TaskInstance` via `create_opencode_task` with the demo's consumer prompt. |
| `AGENTS.md` (root) | Under "Integration Levels," add a row for `optio-opencode` (Python, workspace package) and a short "Python: optio-opencode" subsection beneath the `optio-core` one. |
| `packages/optio-demo/AGENTS.md` | Mention the new `opencode-demo` task in the existing task list. |

---

## Task 1: Feature branch + package scaffolding

**Files:**

- Create: `packages/optio-opencode/pyproject.toml`
- Create: `packages/optio-opencode/src/optio_opencode/__init__.py`
- Create: `packages/optio-opencode/tests/conftest.py`
- Create: `packages/optio-opencode/tests/test_sanity.py`

- [ ] **Step 1: Create and check out a feature branch in place**

Run from the repo root:

```bash
git checkout -b feat/optio-opencode
git status
```

Expected: `On branch feat/optio-opencode`, nothing to commit. Do not create a worktree. Do not push.

- [ ] **Step 2: Create the package directory skeleton**

```bash
mkdir -p packages/optio-opencode/src/optio_opencode
mkdir -p packages/optio-opencode/tests
```

- [ ] **Step 3: Write `pyproject.toml`**

Create `packages/optio-opencode/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "optio-opencode"
version = "0.1.0"
description = "Run opencode web as an optio task; local subprocess or remote via SSH."
license = "Apache-2.0"
requires-python = ">=3.11"
dependencies = [
    "optio-core",
    "asyncssh>=2.14",
    "aiofiles>=23.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 4: Empty package `__init__.py`**

Create `packages/optio-opencode/src/optio_opencode/__init__.py`:

```python
"""optio-opencode — run opencode web as an optio task."""
```

- [ ] **Step 5: Minimal shared conftest**

Create `packages/optio-opencode/tests/conftest.py`:

```python
"""Shared test fixtures for optio-opencode."""

import os
import shutil
import tempfile

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient


@pytest.fixture
def tmp_workdir():
    """A temporary directory that is removed after the test."""
    path = tempfile.mkdtemp(prefix="optio-opencode-test-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest_asyncio.fixture
async def mongo_db():
    """Test MongoDB database, dropped after each test. Matches optio-core's fixture."""
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_opencode_test_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()
```

- [ ] **Step 6: First passing test that proves scaffolding works**

Create `packages/optio-opencode/tests/test_sanity.py`:

```python
def test_package_imports():
    import optio_opencode  # noqa: F401


def test_tmp_workdir_fixture(tmp_workdir):
    import os
    assert os.path.isdir(tmp_workdir)
```

- [ ] **Step 7: Install in editable mode and run the sanity test**

From the repo root:

```bash
/home/csillag/deai/pyenv/shims/python -m pip install -e packages/optio-opencode
cd packages/optio-opencode && /home/csillag/deai/pyenv/shims/python -m pytest tests/test_sanity.py -v
```

Expected: both tests pass. The package is installed editable so later tasks can import it.

Do **not** commit here. Commits happen only in Task 16.

---

## Task 2: Types — `SSHConfig`, `OpencodeTaskConfig`, `DeliverableCallback`

**Files:**

- Create: `packages/optio-opencode/src/optio_opencode/types.py`
- Create: `packages/optio-opencode/tests/test_types.py`

- [ ] **Step 1: Write failing tests**

Create `packages/optio-opencode/tests/test_types.py`:

```python
from typing import get_type_hints

import pytest

from optio_opencode.types import DeliverableCallback, OpencodeTaskConfig, SSHConfig


def test_ssh_config_required_fields_only():
    cfg = SSHConfig(host="h", user="u", key_path="/tmp/k")
    assert cfg.host == "h"
    assert cfg.user == "u"
    assert cfg.key_path == "/tmp/k"
    assert cfg.port == 22


def test_ssh_config_custom_port():
    cfg = SSHConfig(host="h", user="u", key_path="/tmp/k", port=2222)
    assert cfg.port == 2222


def test_opencode_task_config_minimal():
    cfg = OpencodeTaskConfig(consumer_instructions="do X")
    assert cfg.consumer_instructions == "do X"
    assert cfg.opencode_config == {}
    assert cfg.ssh is None
    assert cfg.on_deliverable is None
    assert cfg.install_if_missing is True


def test_opencode_task_config_independent_default_dicts():
    a = OpencodeTaskConfig(consumer_instructions="")
    b = OpencodeTaskConfig(consumer_instructions="")
    a.opencode_config["k"] = 1
    assert "k" not in b.opencode_config


def test_deliverable_callback_is_callable_alias():
    # Type alias: the callback takes (str, str) and returns an awaitable.
    # Existence check only — no runtime behavior to assert.
    assert DeliverableCallback is not None
```

- [ ] **Step 2: Run tests and watch them fail**

```bash
cd packages/optio-opencode && /home/csillag/deai/pyenv/shims/python -m pytest tests/test_types.py -v
```

Expected: `ModuleNotFoundError: No module named 'optio_opencode.types'`.

- [ ] **Step 3: Implement `types.py`**

Create `packages/optio-opencode/src/optio_opencode/types.py`:

```python
"""Public data types for optio-opencode consumers."""

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


DeliverableCallback = Callable[[str, str], Awaitable[None]]
"""Consumer callback invoked per fetched DELIVERABLE.

Arguments: (remote_path, decoded_text). remote_path is the path the LLM
wrote in the DELIVERABLE log line (interpreted relative to the workdir
if not absolute). text is the file contents decoded as UTF-8.
"""


@dataclass
class SSHConfig:
    """SSH connection parameters for remote-mode optio-opencode.

    Known-hosts verification is disabled in MVP; asyncssh's
    ``known_hosts=None`` equivalent is used by the host layer.
    """
    host: str
    user: str
    key_path: str
    port: int = 22


@dataclass
class OpencodeTaskConfig:
    """Configuration for one optio-opencode task instance."""
    consumer_instructions: str
    opencode_config: dict[str, Any] = field(default_factory=dict)
    ssh: SSHConfig | None = None
    on_deliverable: DeliverableCallback | None = None
    install_if_missing: bool = True
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd packages/optio-opencode && /home/csillag/deai/pyenv/shims/python -m pytest tests/test_types.py -v
```

Expected: all 5 tests pass.

---

## Task 3: Log-line parser — `parse_log_line` + `validate_deliverable_path`

**Files:**

- Create: `packages/optio-opencode/src/optio_opencode/logparse.py`
- Create: `packages/optio-opencode/tests/test_logparse.py`

Spec reference: Section 6 (Log-Tail & Deliverable Fetch Mechanics) + Section 4 (path validation rules).

- [ ] **Step 1: Write failing tests for `parse_log_line`**

Create `packages/optio-opencode/tests/test_logparse.py`:

```python
import pytest

from optio_opencode.logparse import (
    DeliverableEvent,
    DoneEvent,
    ErrorEvent,
    StatusEvent,
    UnknownLine,
    parse_log_line,
    validate_deliverable_path,
)


# ---- STATUS ----

def test_status_plain():
    ev = parse_log_line("STATUS: working on it")
    assert isinstance(ev, StatusEvent)
    assert ev.percent is None
    assert ev.message == "working on it"


def test_status_with_percent():
    ev = parse_log_line("STATUS: 42% halfway there")
    assert isinstance(ev, StatusEvent)
    assert ev.percent == 42
    assert ev.message == "halfway there"


def test_status_with_zero_percent():
    ev = parse_log_line("STATUS: 0% just starting")
    assert isinstance(ev, StatusEvent)
    assert ev.percent == 0
    assert ev.message == "just starting"


def test_status_with_percent_over_100_is_clamped():
    ev = parse_log_line("STATUS: 150% overachiever")
    assert isinstance(ev, StatusEvent)
    assert ev.percent == 100


def test_status_empty_message_ok():
    ev = parse_log_line("STATUS: ")
    assert isinstance(ev, StatusEvent)
    assert ev.percent is None
    assert ev.message == ""


# ---- DELIVERABLE ----

def test_deliverable_relative():
    ev = parse_log_line("DELIVERABLE: ./deliverables/out.txt")
    assert isinstance(ev, DeliverableEvent)
    assert ev.path == "./deliverables/out.txt"


def test_deliverable_absolute():
    ev = parse_log_line("DELIVERABLE: /tmp/wd/deliverables/a.md")
    assert isinstance(ev, DeliverableEvent)
    assert ev.path == "/tmp/wd/deliverables/a.md"


def test_deliverable_trims_trailing_whitespace():
    ev = parse_log_line("DELIVERABLE: ./x   ")
    assert isinstance(ev, DeliverableEvent)
    assert ev.path == "./x"


# ---- DONE ----

def test_done_bare():
    ev = parse_log_line("DONE")
    assert isinstance(ev, DoneEvent)
    assert ev.summary is None


def test_done_with_summary():
    ev = parse_log_line("DONE: wrote the report")
    assert isinstance(ev, DoneEvent)
    assert ev.summary == "wrote the report"


# ---- ERROR ----

def test_error_bare():
    ev = parse_log_line("ERROR")
    assert isinstance(ev, ErrorEvent)
    assert ev.message is None


def test_error_with_message():
    ev = parse_log_line("ERROR: provider auth failed")
    assert isinstance(ev, ErrorEvent)
    assert ev.message == "provider auth failed"


# ---- Unknown ----

def test_unknown_line_preserved_verbatim():
    ev = parse_log_line("just some narration from the llm")
    assert isinstance(ev, UnknownLine)
    assert ev.text == "just some narration from the llm"


def test_empty_line_is_unknown():
    ev = parse_log_line("")
    assert isinstance(ev, UnknownLine)
    assert ev.text == ""


# ---- validate_deliverable_path ----

def test_validate_relative_ok(tmp_workdir):
    import os
    resolved = validate_deliverable_path("./deliverables/x.txt", tmp_workdir)
    assert resolved == os.path.join(tmp_workdir, "deliverables", "x.txt")


def test_validate_absolute_inside_workdir_ok(tmp_workdir):
    import os
    p = os.path.join(tmp_workdir, "a.txt")
    assert validate_deliverable_path(p, tmp_workdir) == p


def test_validate_escape_via_dotdot_rejected(tmp_workdir):
    with pytest.raises(ValueError):
        validate_deliverable_path("../etc/passwd", tmp_workdir)


def test_validate_absolute_outside_workdir_rejected(tmp_workdir):
    with pytest.raises(ValueError):
        validate_deliverable_path("/etc/passwd", tmp_workdir)
```

- [ ] **Step 2: Run tests, watch them fail**

```bash
cd packages/optio-opencode && /home/csillag/deai/pyenv/shims/python -m pytest tests/test_logparse.py -v
```

Expected: `ModuleNotFoundError: No module named 'optio_opencode.logparse'`.

- [ ] **Step 3: Implement `logparse.py`**

Create `packages/optio-opencode/src/optio_opencode/logparse.py`:

```python
"""Parse lines from the optio.log file that opencode (driven by the LLM) appends to.

The format is keyword-prefixed, one line per event. See the design spec Section 6.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class StatusEvent:
    percent: int | None
    message: str


@dataclass(frozen=True)
class DeliverableEvent:
    path: str


@dataclass(frozen=True)
class DoneEvent:
    summary: str | None


@dataclass(frozen=True)
class ErrorEvent:
    message: str | None


@dataclass(frozen=True)
class UnknownLine:
    text: str


LogEvent = Union[StatusEvent, DeliverableEvent, DoneEvent, ErrorEvent, UnknownLine]


_RE_STATUS = re.compile(r"^STATUS:\s*(?:(\d{1,3})%\s+)?(.*)$")
_RE_DELIVERABLE = re.compile(r"^DELIVERABLE:\s*(.+?)\s*$")
_RE_DONE = re.compile(r"^DONE(?::\s*(.*))?\s*$")
_RE_ERROR = re.compile(r"^ERROR(?::\s*(.*))?\s*$")


def parse_log_line(line: str) -> LogEvent:
    """Classify one line from optio.log into a LogEvent."""
    stripped = line.rstrip("\r\n").rstrip()
    m = _RE_STATUS.match(stripped)
    if m:
        pct_raw, msg = m.group(1), m.group(2)
        percent: int | None
        if pct_raw is None:
            percent = None
        else:
            percent = min(int(pct_raw), 100)
        return StatusEvent(percent=percent, message=msg)

    m = _RE_DELIVERABLE.match(stripped)
    if m:
        return DeliverableEvent(path=m.group(1))

    m = _RE_DONE.match(stripped)
    if m:
        summary = m.group(1) if m.group(1) else None
        return DoneEvent(summary=summary)

    m = _RE_ERROR.match(stripped)
    if m:
        msg = m.group(1) if m.group(1) else None
        return ErrorEvent(message=msg)

    return UnknownLine(text=stripped)


def validate_deliverable_path(path: str, workdir: str) -> str:
    """Resolve ``path`` against ``workdir`` and ensure it stays inside.

    Returns the absolute, normalized path.  Raises ValueError on escape.
    """
    workdir_abs = os.path.realpath(workdir)
    if os.path.isabs(path):
        candidate = os.path.realpath(path)
    else:
        candidate = os.path.realpath(os.path.join(workdir_abs, path))

    # Ensure the resolved path is inside workdir_abs.
    rel = os.path.relpath(candidate, workdir_abs)
    if rel == ".." or rel.startswith(".." + os.sep):
        raise ValueError(
            f"deliverable path escapes workdir: {path!r} (resolved to {candidate!r}, "
            f"workdir={workdir_abs!r})"
        )
    return candidate
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
cd packages/optio-opencode && /home/csillag/deai/pyenv/shims/python -m pytest tests/test_logparse.py -v
```

Expected: all 18 tests pass.

---

## Task 4: Prompt composer — `compose_agents_md`

**Files:**

- Create: `packages/optio-opencode/src/optio_opencode/prompt.py`
- Create: `packages/optio-opencode/tests/test_prompt.py`

Spec reference: Section 4 (the exact AGENTS.md boilerplate).

- [ ] **Step 1: Write failing tests**

Create `packages/optio-opencode/tests/test_prompt.py`:

```python
from optio_opencode.prompt import BASE_PROMPT, compose_agents_md


def test_base_prompt_contains_all_keywords():
    for kw in ("STATUS:", "DELIVERABLE:", "DONE", "ERROR"):
        assert kw in BASE_PROMPT


def test_base_prompt_mentions_log_and_deliverables_paths():
    assert "./optio.log" in BASE_PROMPT
    assert "./deliverables/" in BASE_PROMPT


def test_base_prompt_contains_task_framing():
    assert "## Task" in BASE_PROMPT
    # The framing paragraph.
    assert "ask questions and dialogue with the human" in BASE_PROMPT


def test_compose_agents_md_appends_consumer_instructions_verbatim():
    out = compose_agents_md("please compute 2 + 2")
    assert out.startswith(BASE_PROMPT)
    # Exactly one blank line separates the base prompt from consumer's text.
    assert out.endswith("please compute 2 + 2\n")
    assert "\n\nplease compute 2 + 2\n" in out


def test_compose_agents_md_empty_consumer_still_ends_cleanly():
    out = compose_agents_md("")
    assert out.startswith(BASE_PROMPT)
    assert out.endswith("\n")
```

- [ ] **Step 2: Run tests, watch them fail**

```bash
cd packages/optio-opencode && /home/csillag/deai/pyenv/shims/python -m pytest tests/test_prompt.py -v
```

Expected: import error.

- [ ] **Step 3: Implement `prompt.py`**

Create `packages/optio-opencode/src/optio_opencode/prompt.py`:

```python
"""System-prompt composition for optio-opencode.

The base prompt teaches opencode (via AGENTS.md) how to coordinate with the
host harness: which log file to append status/deliverable/done/error lines
to, where to put deliverable files, and how the human expects to be
addressed.  The consumer's own task description is then appended verbatim.
"""


BASE_PROMPT = """# Coordination protocol with the host (optio-opencode)

You are running inside a coordination harness. Follow these conventions
throughout the session.

## Log channel

Append one line per entry to `./optio.log` in this directory. Each line
must start with one of:

- `STATUS:` — progress update for the human. Optional leading percent,
  e.g. `STATUS: 50% counting my fingers`.
- `DELIVERABLE:` — absolute or workdir-relative path to a file you've
  just produced, e.g. `DELIVERABLE: ./deliverables/summary.md`.
- `DONE` — you have finished the task. May be followed by an optional
  summary on the same line: `DONE: wrote the report`.
- `ERROR` — you cannot continue. May be followed by an optional
  message: `ERROR: provider auth failed`.

After writing `DONE` or `ERROR`, the session will terminate. Do not
write further lines.

## Deliverables

Place files you want to hand back to the host under `./deliverables/`.
For each file, write a `DELIVERABLE:` log line *after* the file exists
and its contents are final. The host fetches files by reading these
log lines.

## Task

Here comes the description of your actual task to complete. Throughout
the task, you are encouraged to narrate progress — both on the normal
UI and in parallel using the `STATUS:` messages explained above — and
you are free to ask questions and dialogue with the human. They are
also working on the same task and will cooperate with you on achieving
the same goals. So:
"""


def compose_agents_md(consumer_instructions: str) -> str:
    """Build the full AGENTS.md body: base prompt + blank line + consumer text."""
    body = consumer_instructions.rstrip()
    return f"{BASE_PROMPT}\n{body}\n"
```

- [ ] **Step 4: Run tests, verify pass**

```bash
cd packages/optio-opencode && /home/csillag/deai/pyenv/shims/python -m pytest tests/test_prompt.py -v
```

Expected: all 5 tests pass.

---

## Task 5: Host abstraction — Protocol

**Files:**

- Create: `packages/optio-opencode/src/optio_opencode/host.py` (initial skeleton with Protocol only)

This task introduces the abstract interface; concrete `LocalHost` and `RemoteHost` come in Tasks 6 and 7. We test through the concrete implementations (Task 6) rather than the Protocol itself.

- [ ] **Step 1: Define the Protocol**

Create `packages/optio-opencode/src/optio_opencode/host.py`:

```python
"""Host abstraction for optio-opencode.

Two concrete implementations:

* ``LocalHost`` — the optio worker itself.  Uses asyncio subprocess + aiofiles.
* ``RemoteHost`` — a remote machine reachable over SSH.  Uses asyncssh,
  multiplexing command exec + SFTP + local port forwarding over a single
  connection.

From the caller's perspective the two are indistinguishable except that
``ensure_opencode_installed`` may install opencode on remote hosts but
never on local hosts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol


@dataclass
class LaunchedProcess:
    """Handle returned by ``Host.launch_opencode``."""
    # Implementations are free to use their own process type; this object
    # only carries the data the session state machine needs.
    pid_like: object
    """Opaque handle the host uses to terminate the process later."""

    opencode_port: int
    """The port opencode is listening on, on the host where it runs."""


class Host(Protocol):
    """Everything optio-opencode needs from a host."""

    workdir: str  # absolute path on the host where opencode runs

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def setup_workdir(self) -> None:
        """Create workdir, workdir/deliverables, and an empty workdir/optio.log."""

    async def write_text(self, relpath: str, content: str) -> None:
        """Write a UTF-8 text file inside the workdir."""

    async def ensure_opencode_installed(self, install_if_missing: bool) -> None:
        """Raise RuntimeError if opencode is not available (and install_if_missing is False
        or an install attempt failed).  Remote hosts may run the curl installer;
        local hosts never install — they raise if missing regardless of the flag."""

    async def launch_opencode(
        self,
        password: str,
        ready_timeout_s: float,
        extra_args: list[str] | None = None,
    ) -> LaunchedProcess:
        """Launch ``opencode web`` in the workdir with the given password.

        Blocks until opencode prints a ``Listening on http://...`` URL
        or ``ready_timeout_s`` elapses.  Raises TimeoutError on timeout
        (opencode is left killed/cleaned up).

        ``extra_args`` is a test-only hook for substituting a test-double
        binary for opencode; production callers omit it.
        """

    async def establish_tunnel(self, opencode_port: int) -> int:
        """Return the port on the worker machine at which the upstream
        is reachable.  Local hosts return ``opencode_port`` unchanged;
        remote hosts open an SSH local forward and return the local port."""

    def tail_log(self) -> AsyncIterator[str]:
        """Async iterator yielding lines (without trailing newlines) from
        workdir/optio.log as they are appended.  Terminates when the
        underlying tail process ends or the host disconnects."""

    async def fetch_deliverable_text(self, absolute_path: str) -> str:
        """Fetch ``absolute_path`` (already validated to live inside workdir)
        and return the UTF-8 decoded contents.  Raises UnicodeDecodeError
        on non-UTF-8 content; raises FileNotFoundError on missing file."""

    async def terminate_opencode(
        self,
        process: LaunchedProcess,
        aggressive: bool,
    ) -> None:
        """Terminate opencode.

        aggressive=False: SIGTERM, wait up to 5 s, then SIGKILL.
        aggressive=True: SIGKILL immediately; do not wait.
        """

    async def cleanup_workdir(self, aggressive: bool) -> None:
        """Remove the workdir.

        aggressive=False: wait for the rm to complete.
        aggressive=True: fire-and-forget; return as soon as the call
        has been dispatched.
        """
```

No tests yet — the Protocol is exercised via `LocalHost` tests (Task 6).

---

## Task 6: `LocalHost` implementation + tests

**Files:**

- Modify: `packages/optio-opencode/src/optio_opencode/host.py` (append `LocalHost`)
- Create: `packages/optio-opencode/tests/fake_opencode.py`
- Create: `packages/optio-opencode/tests/test_host_local.py`

- [ ] **Step 1: Write the test-double `fake_opencode.py`**

Create `packages/optio-opencode/tests/fake_opencode.py`:

```python
"""Stand-in for `opencode web` during integration tests.

Usage::

    python fake_opencode.py --port <N> --scenario <name>

Behavior:

1. Binds a TCP socket on 127.0.0.1 on ``--port`` (or a free port if 0).
2. Prints exactly one line to stdout:
   ``Listening on http://127.0.0.1:<port>/``
3. Runs the named scenario: appends scripted lines to ``./optio.log``
   and writes scripted files to ``./deliverables/``, sleeping a little
   between steps.
4. Keeps the socket open (serves a trivial 200 OK on any request) until
   SIGTERM/SIGINT or the scenario completes.

Scenarios are driven by the CWD containing ``optio.log`` and
``deliverables/`` (optio-opencode's workdir convention).
"""

import argparse
import asyncio
import os
import socket
import sys


SCENARIOS = {
    "happy": [
        ("log", "STATUS: starting"),
        ("sleep", 0.05),
        ("write", "deliverables/out.txt", "hello 42 blue"),
        ("log", "DELIVERABLE: ./deliverables/out.txt"),
        ("sleep", 0.05),
        ("log", "DONE: all good"),
    ],
    "status_percent": [
        ("log", "STATUS: 10% just starting"),
        ("sleep", 0.05),
        ("log", "STATUS: 100% finished"),
        ("log", "DONE"),
    ],
    "error": [
        ("log", "STATUS: trying"),
        ("sleep", 0.05),
        ("log", "ERROR: auth failed"),
    ],
    "no_done_then_exit": [
        ("log", "STATUS: halfway"),
        ("sleep", 0.05),
        ("exit", 0),  # Exit 0 before writing DONE — should become failed.
    ],
    "escape_path": [
        ("log", "DELIVERABLE: ../etc/passwd"),
        ("sleep", 0.05),
        ("log", "DONE"),
    ],
    "non_utf8": [
        ("write_bytes", "deliverables/bad.bin", b"\xff\xfe\x00\x01"),
        ("log", "DELIVERABLE: ./deliverables/bad.bin"),
        ("sleep", 0.05),
        ("log", "DONE"),
    ],
    "sleep_forever": [
        ("log", "STATUS: waiting to be cancelled"),
        ("sleep", 3600),
    ],
}


def append_log(line: str) -> None:
    with open("optio.log", "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()


async def run_scenario(name: str) -> int:
    steps = SCENARIOS[name]
    for step in steps:
        op = step[0]
        if op == "log":
            append_log(step[1])
        elif op == "sleep":
            await asyncio.sleep(step[1])
        elif op == "write":
            path = step[1]
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(step[2])
        elif op == "write_bytes":
            path = step[1]
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(step[2])
        elif op == "exit":
            return step[1]
    # Scenario finished; hold open until killed.
    while True:
        await asyncio.sleep(3600)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--scenario", required=True)
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", args.port))
    sock.listen(16)
    port = sock.getsockname()[1]
    print(f"Listening on http://127.0.0.1:{port}/", flush=True)

    # Minimal HTTP responder so any proxied request succeeds.
    async def serve():
        loop = asyncio.get_event_loop()
        while True:
            conn, _ = await loop.sock_accept(sock)
            conn.setblocking(False)
            try:
                await loop.sock_recv(conn, 4096)
                await loop.sock_sendall(
                    conn, b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
                )
            except Exception:
                pass
            finally:
                conn.close()

    sock.setblocking(False)
    serve_task = asyncio.create_task(serve())
    try:
        code = await run_scenario(args.scenario)
        return code
    finally:
        serve_task.cancel()
        sock.close()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
```

- [ ] **Step 2: Write failing tests for `LocalHost`**

Create `packages/optio-opencode/tests/test_host_local.py`:

```python
import asyncio
import os
import sys

import pytest

from optio_opencode.host import LocalHost


FAKE_OPENCODE = os.path.join(os.path.dirname(__file__), "fake_opencode.py")


@pytest.fixture
def local_host(tmp_workdir):
    return LocalHost(
        workdir=tmp_workdir,
        opencode_cmd=[sys.executable, FAKE_OPENCODE],
    )


async def test_setup_workdir_creates_expected_layout(local_host, tmp_workdir):
    await local_host.setup_workdir()
    assert os.path.isdir(os.path.join(tmp_workdir, "deliverables"))
    assert os.path.isfile(os.path.join(tmp_workdir, "optio.log"))


async def test_write_text_writes_utf8(local_host, tmp_workdir):
    await local_host.setup_workdir()
    await local_host.write_text("AGENTS.md", "héllo")
    with open(os.path.join(tmp_workdir, "AGENTS.md"), encoding="utf-8") as fh:
        assert fh.read() == "héllo"


async def test_launch_prints_url_and_reports_port(local_host):
    await local_host.setup_workdir()
    proc = await local_host.launch_opencode(
        password="unused-by-fake",
        ready_timeout_s=5.0,
        extra_args=["--scenario", "sleep_forever"],
    )
    try:
        assert 1024 <= proc.opencode_port < 65536
    finally:
        await local_host.terminate_opencode(proc, aggressive=True)


async def test_launch_times_out_on_no_url():
    # Use /bin/sleep — it never prints a URL.  readiness should time out.
    host = LocalHost(
        workdir=os.environ.get("PYTEST_CURRENT_TEST", "/tmp"),
        opencode_cmd=["/bin/sleep", "60"],
    )
    with pytest.raises(TimeoutError):
        await host.launch_opencode(
            password="x", ready_timeout_s=0.5, extra_args=[]
        )


async def test_tail_log_yields_appended_lines(local_host, tmp_workdir):
    await local_host.setup_workdir()

    async def append_later():
        await asyncio.sleep(0.05)
        with open(os.path.join(tmp_workdir, "optio.log"), "a", encoding="utf-8") as fh:
            fh.write("hello\n")
            fh.flush()

    task = asyncio.create_task(append_later())
    collected: list[str] = []
    async for line in local_host.tail_log():
        collected.append(line)
        if collected == ["hello"]:
            break
    await task
    assert collected == ["hello"]


async def test_fetch_deliverable_text(local_host, tmp_workdir):
    await local_host.setup_workdir()
    target = os.path.join(tmp_workdir, "deliverables", "a.txt")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write("contents")
    assert await local_host.fetch_deliverable_text(target) == "contents"


async def test_fetch_deliverable_non_utf8_raises(local_host, tmp_workdir):
    await local_host.setup_workdir()
    target = os.path.join(tmp_workdir, "deliverables", "b.bin")
    with open(target, "wb") as fh:
        fh.write(b"\xff\xfe\x00")
    with pytest.raises(UnicodeDecodeError):
        await local_host.fetch_deliverable_text(target)


async def test_cleanup_workdir_removes_directory(local_host, tmp_workdir):
    await local_host.setup_workdir()
    await local_host.cleanup_workdir(aggressive=False)
    assert not os.path.exists(tmp_workdir)
```

- [ ] **Step 3: Run tests, watch them fail**

```bash
cd packages/optio-opencode && /home/csillag/deai/pyenv/shims/python -m pytest tests/test_host_local.py -v
```

Expected: `ImportError: cannot import name 'LocalHost' from 'optio_opencode.host'`.

- [ ] **Step 4: Implement `LocalHost`**

Append to `packages/optio-opencode/src/optio_opencode/host.py`:

```python
# --- implementation -----------------------------------------------------

import asyncio
import os
import re
import shutil
from dataclasses import field


_READY_RE = re.compile(r"(http://[^\s]+)")


class LocalHost:
    """Host implementation for a local subprocess."""

    workdir: str

    def __init__(self, workdir: str, opencode_cmd: list[str] | None = None):
        self.workdir = workdir
        # Allow tests to substitute a fake opencode binary.
        self._opencode_cmd = opencode_cmd or ["opencode"]
        self._tail_proc: asyncio.subprocess.Process | None = None

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        if self._tail_proc is not None and self._tail_proc.returncode is None:
            self._tail_proc.terminate()
            try:
                await asyncio.wait_for(self._tail_proc.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                self._tail_proc.kill()
                await self._tail_proc.wait()
            self._tail_proc = None

    async def setup_workdir(self) -> None:
        os.makedirs(self.workdir, exist_ok=True)
        os.makedirs(os.path.join(self.workdir, "deliverables"), exist_ok=True)
        log_path = os.path.join(self.workdir, "optio.log")
        open(log_path, "a").close()

    async def write_text(self, relpath: str, content: str) -> None:
        full = os.path.join(self.workdir, relpath)
        os.makedirs(os.path.dirname(full) or self.workdir, exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)

    async def ensure_opencode_installed(self, install_if_missing: bool) -> None:
        # Local mode: always expects pre-install.  Spec Section 1/9.
        # For tests the _opencode_cmd points at fake_opencode.py which always exists.
        if self._opencode_cmd[0] == "opencode" and shutil.which("opencode") is None:
            raise RuntimeError(
                "opencode is not available on this local host.  "
                "Install it first (e.g. `curl -fsSL opencode.ai/install | bash`); "
                "optio-opencode does not install opencode in local mode."
            )

    async def launch_opencode(
        self,
        password: str,
        ready_timeout_s: float,
        extra_args: list[str] | None = None,
    ) -> LaunchedProcess:
        env = os.environ.copy()
        env["OPENCODE_SERVER_PASSWORD"] = password
        cmd = [
            *self._opencode_cmd,
            "--port", "0",
        ]
        # When using the real opencode binary, "web" is a subcommand.
        # When using the fake, there is no "web" subcommand.
        if self._opencode_cmd[0] == "opencode":
            cmd = [*self._opencode_cmd, "web", "--port=0", "--hostname=127.0.0.1"]
        else:
            cmd = [*self._opencode_cmd, *(extra_args or [])]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.workdir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def _read_url() -> int:
            assert proc.stdout is not None
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    raise RuntimeError("opencode exited before printing a URL")
                line = raw.decode("utf-8", errors="replace").rstrip()
                m = _READY_RE.search(line)
                if m:
                    url = m.group(1)
                    # Parse out the port.
                    m2 = re.search(r":(\d+)", url)
                    if not m2:
                        raise RuntimeError(f"could not find port in URL: {url}")
                    return int(m2.group(1))

        try:
            port = await asyncio.wait_for(_read_url(), timeout=ready_timeout_s)
        except (asyncio.TimeoutError, Exception) as exc:
            proc.kill()
            await proc.wait()
            if isinstance(exc, asyncio.TimeoutError):
                raise TimeoutError(
                    f"opencode did not print a listening URL within {ready_timeout_s}s"
                ) from None
            raise

        return LaunchedProcess(pid_like=proc, opencode_port=port)

    async def establish_tunnel(self, opencode_port: int) -> int:
        return opencode_port

    async def tail_log(self):
        log_path = os.path.join(self.workdir, "optio.log")
        self._tail_proc = await asyncio.create_subprocess_exec(
            "tail", "-F", "-n", "0", log_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert self._tail_proc.stdout is not None
        while True:
            raw = await self._tail_proc.stdout.readline()
            if not raw:
                break
            yield raw.decode("utf-8", errors="replace").rstrip("\r\n")

    async def fetch_deliverable_text(self, absolute_path: str) -> str:
        # Read bytes first so we can raise UnicodeDecodeError on bad content.
        with open(absolute_path, "rb") as fh:
            data = fh.read()
        return data.decode("utf-8")

    async def terminate_opencode(
        self,
        process: LaunchedProcess,
        aggressive: bool,
    ) -> None:
        proc: asyncio.subprocess.Process = process.pid_like  # type: ignore[assignment]
        if proc.returncode is not None:
            return
        if aggressive:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    async def cleanup_workdir(self, aggressive: bool) -> None:
        # On local filesystems rmtree is fast enough that aggressive vs. not
        # makes no difference.
        if os.path.exists(self.workdir):
            shutil.rmtree(self.workdir, ignore_errors=True)
```

- [ ] **Step 5: Run tests until green**

```bash
cd packages/optio-opencode && /home/csillag/deai/pyenv/shims/python -m pytest tests/test_host_local.py -v
```

Expected: all 8 tests pass. If `tail_log` hangs, check that `optio.log` exists before tail starts (`setup_workdir` creates it).

---

## Task 7: `RemoteHost` (asyncssh)

**Files:**

- Modify: `packages/optio-opencode/src/optio_opencode/host.py` (append `RemoteHost`)

No unit tests; correctness is validated in Task 12 (remote integration with real SSH container).

- [ ] **Step 1: Implement `RemoteHost`**

Append to `packages/optio-opencode/src/optio_opencode/host.py`:

```python
# --- RemoteHost -----------------------------------------------------

import uuid

import asyncssh

from optio_opencode.types import SSHConfig


class RemoteHost:
    """Host implementation backed by a single asyncssh connection.

    The connection multiplexes:
      * command exec (install, launch, tail -F, rm -rf)
      * SFTP (write AGENTS.md, write opencode.json, fetch deliverables)
      * local port forward (the browser → opencode tunnel)
    """

    workdir: str

    def __init__(self, ssh_config: SSHConfig):
        self._ssh = ssh_config
        self.workdir = f"/tmp/optio-opencode-{uuid.uuid4().hex[:12]}"
        self._conn: asyncssh.SSHClientConnection | None = None
        self._sftp: asyncssh.SFTPClient | None = None
        self._launch_proc: asyncssh.SSHClientProcess | None = None
        self._tail_proc: asyncssh.SSHClientProcess | None = None
        self._forward: asyncssh.SSHListener | None = None

    async def connect(self) -> None:
        self._conn = await asyncssh.connect(
            host=self._ssh.host,
            username=self._ssh.user,
            port=self._ssh.port,
            client_keys=[self._ssh.key_path],
            known_hosts=None,  # Spec: known-hosts verification disabled in MVP.
        )
        self._sftp = await self._conn.start_sftp_client()

    async def disconnect(self) -> None:
        if self._tail_proc is not None and not self._tail_proc.exit_status_ready:
            self._tail_proc.terminate()
        if self._forward is not None:
            self._forward.close()
        if self._sftp is not None:
            self._sftp.exit()
            self._sftp = None
        if self._conn is not None:
            self._conn.close()
            await self._conn.wait_closed()
            self._conn = None

    async def setup_workdir(self) -> None:
        assert self._conn is not None and self._sftp is not None
        await self._conn.run(f"mkdir -p {self.workdir}/deliverables", check=True)
        await self._conn.run(f"touch {self.workdir}/optio.log", check=True)

    async def write_text(self, relpath: str, content: str) -> None:
        assert self._sftp is not None
        remote_path = f"{self.workdir}/{relpath}"
        # Ensure parent dir exists.
        parent = os.path.dirname(remote_path)
        if parent and parent != self.workdir:
            await self._conn.run(f"mkdir -p {parent}", check=True)  # type: ignore[union-attr]
        async with self._sftp.open(remote_path, "w", encoding="utf-8") as fh:
            await fh.write(content)

    async def ensure_opencode_installed(self, install_if_missing: bool) -> None:
        assert self._conn is not None
        # Use bash -lc so that $HOME/.local/bin (where the opencode install
        # script puts the binary) is on PATH; a plain `command -v opencode`
        # via a non-login shell would miss it.
        check = await self._conn.run(
            "bash -lc 'command -v opencode'", check=False
        )
        if check.exit_status == 0:
            return
        if not install_if_missing:
            raise RuntimeError(
                f"opencode is not installed on {self._ssh.host} and "
                "install_if_missing=False was requested."
            )
        # Use the official install script.  PATH is usually not updated in
        # the non-login shell that ssh exec uses, so opencode may land in
        # ~/.local/bin — the launcher below runs through `bash -lc` to pick
        # that up.
        install = await self._conn.run(
            "curl -fsSL https://opencode.ai/install | bash",
            check=False,
        )
        if install.exit_status != 0:
            raise RuntimeError(
                f"opencode install on {self._ssh.host} failed "
                f"(exit {install.exit_status}): {install.stderr}"
            )

    async def launch_opencode(
        self,
        password: str,
        ready_timeout_s: float,
        extra_args: list[str] | None = None,
    ) -> LaunchedProcess:
        assert self._conn is not None
        cmd = (
            f"cd {self.workdir} && "
            f"OPENCODE_SERVER_PASSWORD={password} "
            f"bash -lc 'opencode web --port=0 --hostname=127.0.0.1'"
        )
        self._launch_proc = await self._conn.create_process(cmd)

        async def _read_url() -> int:
            assert self._launch_proc is not None
            async for raw in self._launch_proc.stdout:
                line = raw.rstrip()
                m = _READY_RE.search(line)
                if m:
                    m2 = re.search(r":(\d+)", m.group(1))
                    if not m2:
                        raise RuntimeError(f"could not find port in URL: {line}")
                    return int(m2.group(1))
            raise RuntimeError("opencode exited before printing a URL")

        try:
            port = await asyncio.wait_for(_read_url(), timeout=ready_timeout_s)
        except asyncio.TimeoutError:
            if self._launch_proc is not None:
                self._launch_proc.terminate()
            raise TimeoutError(
                f"opencode did not print a listening URL within {ready_timeout_s}s"
            )
        return LaunchedProcess(pid_like=self._launch_proc, opencode_port=port)

    async def establish_tunnel(self, opencode_port: int) -> int:
        assert self._conn is not None
        self._forward = await self._conn.forward_local_port(
            "127.0.0.1", 0, "127.0.0.1", opencode_port
        )
        return self._forward.get_port()

    async def tail_log(self):
        assert self._conn is not None
        log_path = f"{self.workdir}/optio.log"
        self._tail_proc = await self._conn.create_process(
            f"tail -F -n 0 {log_path}"
        )
        async for raw in self._tail_proc.stdout:
            yield raw.rstrip("\r\n")

    async def fetch_deliverable_text(self, absolute_path: str) -> str:
        assert self._sftp is not None
        async with self._sftp.open(absolute_path, "rb") as fh:
            data = await fh.read()
        return data.decode("utf-8")

    async def terminate_opencode(
        self,
        process: LaunchedProcess,
        aggressive: bool,
    ) -> None:
        proc: asyncssh.SSHClientProcess = process.pid_like  # type: ignore[assignment]
        if proc.exit_status_ready:
            return
        if aggressive:
            proc.terminate()
            # Best-effort: do not wait.
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    async def cleanup_workdir(self, aggressive: bool) -> None:
        if self._conn is None:
            return
        cmd = f"rm -rf {self.workdir}"
        if aggressive:
            # Fire-and-forget: schedule the exec, do not await completion.
            asyncio.create_task(self._conn.run(cmd, check=False))
            return
        await self._conn.run(cmd, check=False)
```

- [ ] **Step 2: Smoke-test import**

Add to `packages/optio-opencode/tests/test_sanity.py`:

```python
def test_hosts_importable():
    from optio_opencode.host import Host, LocalHost, RemoteHost  # noqa
```

Run:

```bash
cd packages/optio-opencode && /home/csillag/deai/pyenv/shims/python -m pytest tests/test_sanity.py -v
```

Expected: passes. (Real behavior tested in Task 12.)

---

## Task 8: Session state machine — `run_opencode_session`

**Files:**

- Create: `packages/optio-opencode/src/optio_opencode/session.py`

Unit tests for the state machine would require either an elaborate fake `ProcessContext` + fake `Host`, or exercise the real code via `test_session_local.py`.  We do the latter — Task 9 is the comprehensive local-mode integration test that drives `session.py` with a real `LocalHost`, a real `ProcessContext`, and the fake opencode.  So this task just writes the implementation; Task 9 validates it.

- [ ] **Step 1: Implement `session.py`**

Create `packages/optio-opencode/src/optio_opencode/session.py`:

```python
"""The state machine that runs one optio-opencode session.

Orchestrates a Host (local or remote) through the lifecycle described in
Section 5 of the design spec.  The public entry point is the factory
``create_opencode_task(...)`` which wraps ``run_opencode_session`` in a
``TaskInstance`` and sets ``ui_widget="iframe"``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets

from optio_core.context import ProcessContext
from optio_core.models import BasicAuth, TaskInstance

from optio_opencode.host import Host, LocalHost, LaunchedProcess, RemoteHost
from optio_opencode.logparse import (
    DeliverableEvent,
    DoneEvent,
    ErrorEvent,
    LogEvent,
    StatusEvent,
    UnknownLine,
    parse_log_line,
    validate_deliverable_path,
)
from optio_opencode.prompt import compose_agents_md
from optio_opencode.types import OpencodeTaskConfig


_LOG = logging.getLogger(__name__)


READY_TIMEOUT_S = 30.0
DELIVERABLE_QUEUE_BOUND = 64


class _SessionFailed(Exception):
    """Raised by the session loop to drive the process to 'failed'."""


async def run_opencode_session(ctx: ProcessContext, config: OpencodeTaskConfig) -> None:
    """Execute function body for one optio-opencode task instance."""

    host: Host
    if config.ssh is None:
        host = LocalHost(workdir=_pick_local_workdir())
    else:
        host = RemoteHost(ssh_config=config.ssh)

    password = secrets.token_urlsafe(32)
    process: LaunchedProcess | None = None
    cancelled = False

    try:
        await host.connect()

        # --- provision ---------------------------------------------------
        await host.setup_workdir()
        await host.write_text(
            "AGENTS.md", compose_agents_md(config.consumer_instructions)
        )
        await host.write_text(
            "opencode.json", json.dumps(config.opencode_config, indent=2)
        )

        # --- install (remote only) --------------------------------------
        await host.ensure_opencode_installed(config.install_if_missing)

        # --- launch -----------------------------------------------------
        ctx.report_progress(None, "Launching opencode…")
        process = await host.launch_opencode(
            password=password, ready_timeout_s=READY_TIMEOUT_S
        )

        # --- tunnel + widget registration -------------------------------
        worker_port = await host.establish_tunnel(process.opencode_port)
        await ctx.set_widget_upstream(
            f"http://127.0.0.1:{worker_port}",
            inner_auth=BasicAuth(username="opencode", password=password),
        )
        await ctx.set_widget_data(
            {
                "localStorageOverrides": {
                    "opencode.settings.dat:defaultServerUrl": "{widgetProxyUrl}",
                }
            }
        )
        ctx.report_progress(None, "opencode is live")

        # --- run --------------------------------------------------------
        deliverable_queue: asyncio.Queue[str] = asyncio.Queue(
            maxsize=DELIVERABLE_QUEUE_BOUND
        )
        done_flag = asyncio.Event()
        error_flag: list[str | None] = []  # [message] or [] if not fired
        subprocess_exit: list[int | None] = []  # [exit_code] when seen

        fetch_task = asyncio.create_task(
            _deliverable_fetch_loop(host, config.on_deliverable, deliverable_queue, ctx)
        )
        tail_task = asyncio.create_task(
            _tail_and_dispatch(
                host, ctx, deliverable_queue, done_flag, error_flag
            )
        )
        exit_task = asyncio.create_task(_await_subprocess_exit(host, process, subprocess_exit))
        cancel_task = asyncio.create_task(_watch_cancellation(ctx))

        done, _ = await asyncio.wait(
            {tail_task, exit_task, cancel_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        cancelled = cancel_task in done and cancel_task.result() is True

        if error_flag:
            raise _SessionFailed(error_flag[0] or "opencode reported ERROR")
        # Any subprocess exit without a DONE is a failure, regardless of exit
        # code (opencode web is designed to run indefinitely until the LLM
        # writes DONE or ERROR). Cancellation is handled separately via
        # ``cancelled`` — if the user cancelled, we return cleanly.
        if subprocess_exit and not done_flag.is_set() and not cancelled:
            raise _SessionFailed(
                f"opencode exited with code {subprocess_exit[0]} before DONE"
            )

        # Drain remaining deliverables before returning.
        await deliverable_queue.join()

        # Cancel the still-running watchers.
        for t in (tail_task, exit_task, cancel_task, fetch_task):
            if not t.done():
                t.cancel()
        await asyncio.gather(
            tail_task, exit_task, cancel_task, fetch_task, return_exceptions=True
        )

    except _SessionFailed as fail:
        raise RuntimeError(str(fail)) from None

    finally:
        # Teardown — aggressive if we got cancelled, polite otherwise.
        if process is not None:
            try:
                await host.terminate_opencode(process, aggressive=cancelled)
            except Exception:  # noqa: BLE001
                _LOG.exception("terminate_opencode failed")
        try:
            await host.cleanup_workdir(aggressive=cancelled)
        except Exception:  # noqa: BLE001
            _LOG.exception("cleanup_workdir failed")
        try:
            await host.disconnect()
        except Exception:  # noqa: BLE001
            _LOG.exception("host.disconnect failed")


async def _tail_and_dispatch(
    host: Host,
    ctx: ProcessContext,
    deliverable_queue: asyncio.Queue[str],
    done_flag: asyncio.Event,
    error_flag: list,
) -> None:
    """Consume tail_log, parse each line, dispatch by keyword."""
    async for line in host.tail_log():
        ev: LogEvent = parse_log_line(line)
        if isinstance(ev, StatusEvent):
            ctx.report_progress(ev.percent, ev.message)
        elif isinstance(ev, DeliverableEvent):
            ctx.report_progress(None, f"Deliverable: {ev.path}")
            try:
                resolved = validate_deliverable_path(ev.path, host.workdir)
            except ValueError:
                ctx.report_progress(
                    None, f"invalid deliverable path {ev.path!r}, skipping"
                )
                continue
            try:
                deliverable_queue.put_nowait(resolved)
            except asyncio.QueueFull:
                await deliverable_queue.put(resolved)
        elif isinstance(ev, DoneEvent):
            if ev.summary:
                ctx.report_progress(None, ev.summary)
            done_flag.set()
            return
        elif isinstance(ev, ErrorEvent):
            error_flag.append(ev.message)
            return
        else:
            assert isinstance(ev, UnknownLine)
            if ev.text:
                ctx.report_progress(None, ev.text)


async def _deliverable_fetch_loop(
    host: Host,
    callback,
    queue: asyncio.Queue[str],
    ctx: ProcessContext,
) -> None:
    """Consume resolved deliverable paths and invoke the callback."""
    while True:
        path = await queue.get()
        try:
            try:
                text = await host.fetch_deliverable_text(path)
            except UnicodeDecodeError:
                ctx.report_progress(
                    None, f"Deliverable {path}: not valid UTF-8, skipping callback"
                )
                continue
            except FileNotFoundError:
                ctx.report_progress(None, f"Deliverable {path}: not found")
                continue

            if callback is None:
                continue
            try:
                await callback(path, text)
            except Exception as exc:  # noqa: BLE001
                ctx.report_progress(
                    None, f"on_deliverable callback raised: {exc!r}"
                )
        finally:
            queue.task_done()


async def _await_subprocess_exit(host: Host, process: LaunchedProcess, out: list) -> None:
    """Wait for the opencode process to exit and record its exit code."""
    # Both LocalHost and RemoteHost wrap their process; poll returncode.
    proc = process.pid_like
    # Local: asyncio.subprocess.Process has .wait().
    # Remote: asyncssh.SSHClientProcess has .wait() returning a result object.
    if hasattr(proc, "wait"):
        result = await proc.wait()  # type: ignore[union-attr]
        if hasattr(result, "exit_status"):
            out.append(result.exit_status)
        else:
            out.append(getattr(proc, "returncode", None))


async def _watch_cancellation(ctx: ProcessContext) -> bool:
    """Return True when the process is cancelled."""
    while ctx.should_continue():
        await asyncio.sleep(0.1)
    return True


def _pick_local_workdir() -> str:
    import tempfile
    return tempfile.mkdtemp(prefix="optio-opencode-")


def create_opencode_task(
    process_id: str,
    name: str,
    config: OpencodeTaskConfig,
    description: str | None = None,
) -> TaskInstance:
    """Return a TaskInstance that runs one opencode web session."""

    async def _execute(ctx: ProcessContext) -> None:
        await run_opencode_session(ctx, config)

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        ui_widget="iframe",
    )
```

- [ ] **Step 2: Add the factory to the public API**

Replace `packages/optio-opencode/src/optio_opencode/__init__.py`:

```python
"""optio-opencode — run opencode web as an optio task."""

from optio_opencode.session import create_opencode_task, run_opencode_session
from optio_opencode.types import (
    DeliverableCallback,
    OpencodeTaskConfig,
    SSHConfig,
)

__all__ = [
    "create_opencode_task",
    "run_opencode_session",
    "DeliverableCallback",
    "OpencodeTaskConfig",
    "SSHConfig",
]
```

- [ ] **Step 3: Verify imports compile**

Run:

```bash
cd packages/optio-opencode && /home/csillag/deai/pyenv/shims/python -m pytest tests/test_sanity.py -v
```

Expected: sanity tests pass. Real verification lands in Task 9.

---

## Task 9: Local-mode integration test — full scenario suite

**Files:**

- Create: `packages/optio-opencode/tests/test_session_local.py`

Every scenario from Section 8 of the spec that can be exercised locally. Each test drives the real `run_opencode_session` function with a real `LocalHost` + the `fake_opencode.py` test double + a real `ProcessContext` backed by the `mongo_db` fixture.

Prerequisites: **MongoDB must be reachable** at `MONGO_URL` (default `mongodb://localhost:27017`). Developers start it via `docker compose up -d` in `packages/optio-demo/`.

- [ ] **Step 1: Write the integration tests**

Create `packages/optio-opencode/tests/test_session_local.py`:

```python
"""Integration tests for run_opencode_session over LocalHost + fake_opencode."""

import asyncio
import os
import sys
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from bson import ObjectId

from optio_core.context import ProcessContext
from optio_opencode.session import run_opencode_session
from optio_opencode.types import OpencodeTaskConfig


FAKE_OPENCODE = os.path.join(os.path.dirname(__file__), "fake_opencode.py")


@dataclass
class Captured:
    progress: list[tuple[float | None, str | None]] = field(default_factory=list)
    widget_upstream: list[tuple[str, object]] = field(default_factory=list)
    widget_data: list[object] = field(default_factory=list)
    deliverables: list[tuple[str, str]] = field(default_factory=list)


@pytest_asyncio.fixture
async def ctx_and_captures(mongo_db, monkeypatch):
    """A ProcessContext backed by a real mongo + capture hooks."""
    from optio_core import store

    # Insert a minimal process doc so store writes succeed.
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({
        "_id": oid,
        "processId": "p",
        "name": "P",
        "params": {},
        "metadata": {},
        "parentId": None,
        "rootId": None,
        "depth": 0,
        "order": 0,
        "adhoc": False,
        "ephemeral": False,
        "status": {"state": "running"},
        "progress": {"percent": None, "message": None},
        "log": [],
    })

    cancellation_flag = asyncio.Event()
    ctx = ProcessContext(
        process_oid=oid,
        process_id="p",
        root_oid=oid,
        depth=0,
        params={},
        services={},
        db=mongo_db,
        prefix="test",
        cancellation_flag=cancellation_flag,
        child_counter={"next": 0},
    )

    cap = Captured()

    # Intercept progress + widget calls.
    original_report = ctx.report_progress
    def _report(percent, message=None):
        cap.progress.append((percent, message))
        return original_report(percent, message)
    ctx.report_progress = _report  # type: ignore[method-assign]

    orig_upstream = ctx.set_widget_upstream
    async def _upstream(url, inner_auth=None):
        cap.widget_upstream.append((url, inner_auth))
        return await orig_upstream(url, inner_auth)
    ctx.set_widget_upstream = _upstream  # type: ignore[method-assign]

    orig_data = ctx.set_widget_data
    async def _data(payload):
        cap.widget_data.append(payload)
        return await orig_data(payload)
    ctx.set_widget_data = _data  # type: ignore[method-assign]

    yield ctx, cap, cancellation_flag


def _config(scenario: str, deliverable_cb=None, raises: bool = False) -> OpencodeTaskConfig:
    # The LocalHost test fixture is wired via session.py's use of LocalHost with
    # default opencode_cmd=["opencode"]; tests need to inject the fake binary.
    # We do that by monkeypatching LocalHost's default opencode_cmd at import.
    return OpencodeTaskConfig(
        consumer_instructions=f"(scenario: {scenario})",
        on_deliverable=deliverable_cb,
    )


@pytest.fixture(autouse=True)
def _patch_localhost_to_use_fake(monkeypatch):
    """Point LocalHost at fake_opencode.py for the duration of the test."""
    import optio_opencode.host as host_mod
    orig_init = host_mod.LocalHost.__init__

    def _init(self, workdir: str, opencode_cmd=None):
        return orig_init(
            self,
            workdir=workdir,
            opencode_cmd=[sys.executable, FAKE_OPENCODE],
        )

    monkeypatch.setattr(host_mod.LocalHost, "__init__", _init)


@pytest.fixture(autouse=True)
def _supply_scenario(monkeypatch):
    """fake_opencode expects --scenario; inject via launch_opencode's extra_args."""
    import optio_opencode.host as host_mod
    orig_launch = host_mod.LocalHost.launch_opencode
    scenario_holder: dict = {"name": "happy"}

    async def _launch(self, password, ready_timeout_s, extra_args=None):
        return await orig_launch(
            self, password, ready_timeout_s,
            extra_args=["--scenario", scenario_holder["name"]],
        )
    monkeypatch.setattr(host_mod.LocalHost, "launch_opencode", _launch)
    return scenario_holder


# ---- scenarios --------------------------------------------------------

async def test_happy_path(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "happy"

    received: list[tuple[str, str]] = []
    async def on_d(path, text):
        received.append((path, text))

    cfg = _config("happy", deliverable_cb=on_d)
    await run_opencode_session(ctx, cfg)

    assert len(received) == 1
    p, text = received[0]
    assert "deliverables/out.txt" in p
    assert text == "hello 42 blue"


async def test_status_percent_is_reported(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "status_percent"
    await run_opencode_session(ctx, _config("status_percent"))

    percentages = [p for (p, _m) in cap.progress if p is not None]
    assert 10 in percentages
    assert 100 in percentages


async def test_error_triggers_failure(ctx_and_captures, _supply_scenario):
    ctx, _cap, _ = ctx_and_captures
    _supply_scenario["name"] = "error"
    with pytest.raises(RuntimeError, match="auth failed"):
        await run_opencode_session(ctx, _config("error"))


async def test_subprocess_exit_before_done_is_failure(ctx_and_captures, _supply_scenario):
    ctx, _cap, _ = ctx_and_captures
    _supply_scenario["name"] = "no_done_then_exit"
    with pytest.raises(RuntimeError, match=r"exited with code 0 before DONE"):
        await run_opencode_session(ctx, _config("no_done_then_exit"))


async def test_invalid_deliverable_path_is_skipped(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "escape_path"

    received: list = []
    async def on_d(path, text):
        received.append((path, text))

    await run_opencode_session(ctx, _config("escape_path", deliverable_cb=on_d))
    assert received == []
    messages = [m for (_p, m) in cap.progress if m is not None]
    assert any("invalid deliverable path" in m for m in messages)


async def test_non_utf8_deliverable_is_skipped(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "non_utf8"

    received: list = []
    async def on_d(path, text):
        received.append((path, text))

    await run_opencode_session(ctx, _config("non_utf8", deliverable_cb=on_d))
    assert received == []
    messages = [m for (_p, m) in cap.progress if m is not None]
    assert any("not valid UTF-8" in m for m in messages)


async def test_callback_raises_does_not_fail_task(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "happy"

    async def on_d(path, text):
        raise RuntimeError("boom")

    await run_opencode_session(ctx, _config("happy", deliverable_cb=on_d))
    messages = [m for (_p, m) in cap.progress if m is not None]
    assert any("on_deliverable callback raised" in m for m in messages)


async def test_cancellation_triggers_aggressive_teardown(ctx_and_captures, _supply_scenario):
    ctx, cap, cancellation_flag = ctx_and_captures
    _supply_scenario["name"] = "sleep_forever"

    async def _cancel_soon():
        await asyncio.sleep(0.2)
        cancellation_flag.set()

    asyncio.create_task(_cancel_soon())
    # run_opencode_session should return (not raise) when cancelled — optio-core
    # observes the cancellation flag separately and transitions to cancelled.
    await run_opencode_session(ctx, _config("sleep_forever"))


async def test_widget_upstream_and_data_are_set(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "happy"
    await run_opencode_session(ctx, _config("happy"))

    assert len(cap.widget_upstream) == 1
    url, inner_auth = cap.widget_upstream[0]
    assert url.startswith("http://127.0.0.1:")
    from optio_core.models import BasicAuth
    assert isinstance(inner_auth, BasicAuth)
    assert inner_auth.username == "opencode"
    assert len(inner_auth.password) > 16

    assert len(cap.widget_data) == 1
    payload = cap.widget_data[0]
    assert payload["localStorageOverrides"]["opencode.settings.dat:defaultServerUrl"] == "{widgetProxyUrl}"
```

- [ ] **Step 2: Run the suite**

Ensure MongoDB is running (`docker compose up -d` inside `packages/optio-demo/`) then:

```bash
cd packages/optio-opencode && /home/csillag/deai/pyenv/shims/python -m pytest tests/test_session_local.py -v
```

Expected: all 9 scenarios pass. If a test hangs, likely the tail loop or cancellation watcher is not properly cancelled; check that `_tail_and_dispatch` exits on DONE/ERROR and that `finally` teardown unblocks the remaining awaits.

---

## Task 10: Remote-mode integration test

**Files:**

- Create: `packages/optio-opencode/tests/docker-compose.sshd.yml`
- Create: `packages/optio-opencode/tests/test_session_remote.py`

These tests spin up `linuxserver/openssh-server` on localhost and exercise `RemoteHost` + `run_opencode_session` through it. They **skip cleanly** if Docker is unavailable or the container fails to come up within a short window, so unit-test CI without Docker still works.

Because installing the real opencode inside a throwaway container is heavy, the container is prebaked with `fake_opencode.py` mounted and a wrapper `opencode` shim that invokes it. This follows the same "inject the fake binary" pattern as Task 9.

- [ ] **Step 1: Write the docker-compose file**

Create `packages/optio-opencode/tests/docker-compose.sshd.yml`:

```yaml
services:
  sshd:
    image: linuxserver/openssh-server:latest
    environment:
      - PUID=1000
      - PGID=1000
      - USER_NAME=optiotest
      - PUBLIC_KEY_FILE=/keys/id_ed25519.pub
      - SUDO_ACCESS=false
      - PASSWORD_ACCESS=false
    volumes:
      - ./ssh-keys:/keys:ro
      - ./fake_opencode.py:/usr/local/bin/fake_opencode.py:ro
      - ./opencode-shim.sh:/usr/local/bin/opencode:ro
    ports:
      - "127.0.0.1:22222:2222"
```

- [ ] **Step 2: Add the shim**

Create `packages/optio-opencode/tests/opencode-shim.sh`:

```bash
#!/bin/sh
# Stand-in for the opencode binary inside the test SSH container.
# Translates `opencode web --port=<N> ...` into a python fake_opencode.py call.
exec python3 /usr/local/bin/fake_opencode.py "$@"
```

Mark it executable when writing (the tests chmod it on setup).

- [ ] **Step 3: Write the remote integration test**

Create `packages/optio-opencode/tests/test_session_remote.py`:

```python
"""Remote-mode integration test — spins up an SSH container."""

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import pytest_asyncio
from bson import ObjectId

from optio_core.context import ProcessContext
from optio_opencode.session import run_opencode_session
from optio_opencode.types import OpencodeTaskConfig, SSHConfig


HERE = Path(__file__).parent
COMPOSE = HERE / "docker-compose.sshd.yml"


def _have_docker() -> bool:
    return shutil.which("docker") is not None


pytestmark = pytest.mark.skipif(
    not _have_docker(), reason="Docker not available"
)


@pytest_asyncio.fixture(scope="module")
async def sshd():
    """Start the SSH container, generate a key pair, wait for port 22222."""
    keys_dir = HERE / "ssh-keys"
    keys_dir.mkdir(exist_ok=True)
    priv = keys_dir / "id_ed25519"
    if not priv.exists():
        subprocess.check_call([
            "ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(priv)
        ])
    # Make shim executable.
    (HERE / "opencode-shim.sh").chmod(0o755)

    subprocess.check_call(["docker", "compose", "-f", str(COMPOSE), "up", "-d"])

    # Wait for port.
    deadline = time.time() + 30
    import socket as _s
    while time.time() < deadline:
        try:
            c = _s.create_connection(("127.0.0.1", 22222), timeout=1)
            c.close()
            break
        except OSError:
            time.sleep(0.5)
    else:
        subprocess.call(["docker", "compose", "-f", str(COMPOSE), "down"])
        pytest.skip("sshd container did not come up")

    # Extra settle time for sshd to accept auth.
    await asyncio.sleep(2)

    yield {
        "host": "127.0.0.1",
        "port": 22222,
        "user": "optiotest",
        "key_path": str(priv),
    }

    subprocess.call(["docker", "compose", "-f", str(COMPOSE), "down"])


@pytest_asyncio.fixture
async def ctx(mongo_db):
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({
        "_id": oid, "processId": "p", "name": "P", "params": {},
        "metadata": {}, "parentId": None, "rootId": None, "depth": 0,
        "order": 0, "adhoc": False, "ephemeral": False,
        "status": {"state": "running"},
        "progress": {"percent": None, "message": None},
        "log": [],
    })
    return ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0,
        params={}, services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
    )


async def test_remote_happy_path(sshd, ctx):
    received: list = []
    async def on_d(path, text):
        received.append((path, text))

    config = OpencodeTaskConfig(
        consumer_instructions="remote test",
        ssh=SSHConfig(
            host=sshd["host"], user=sshd["user"],
            key_path=sshd["key_path"], port=sshd["port"],
        ),
        on_deliverable=on_d,
        install_if_missing=False,  # Shim is already present in the container.
    )

    # fake_opencode inside the container still needs a --scenario arg.
    # We pass one via opencode-shim.sh's arg passthrough + session.py's
    # launch_opencode.  The simplest hook: add a FAKE_SCENARIO env var the
    # shim reads.  For the happy-path test we set it in session via env
    # (production wiring passes it through OPENCODE_SERVER_PASSWORD only).
    #
    # Implementation detail: in the test, we install the scenario by editing
    # the shim once per session.  See conftest.
    scenario_file = HERE / "scenario.txt"
    scenario_file.write_text("happy\n")
    shim = HERE / "opencode-shim.sh"
    shim.write_text("#!/bin/sh\nexec python3 /usr/local/bin/fake_opencode.py \"$@\" --scenario happy\n")
    shim.chmod(0o755)

    await run_opencode_session(ctx, config)
    assert len(received) == 1
    path, text = received[0]
    assert text == "hello 42 blue"
```

- [ ] **Step 4: Run the remote tests**

Ensure MongoDB + Docker are running:

```bash
cd packages/optio-opencode && /home/csillag/deai/pyenv/shims/python -m pytest tests/test_session_remote.py -v
```

Expected: skipped if Docker is unavailable; passes otherwise. Diagnose via `docker compose -f tests/docker-compose.sshd.yml logs sshd` if the container fails to come up.

---

## Task 11: `IframeWidget` template substitution (optio-ui)

**Files:**

- Modify: `packages/optio-ui/src/widgets/IframeWidget.tsx`
- Modify: `packages/optio-ui/src/__tests__/IframeWidget.test.tsx`

Spec reference: Section 5a + 11a.

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-ui/src/__tests__/IframeWidget.test.tsx` inside the existing `describe('IframeWidget', ...)` block (just before the closing `});`):

```typescript
  it('substitutes {widgetProxyUrl} in localStorageOverrides values', () => {
    const props = makeProps({
      widgetData: {
        localStorageOverrides: {
          'opencode.settings.dat:defaultServerUrl': '{widgetProxyUrl}',
          'static.key': 'static-value',
        },
      },
    });
    render(<IframeWidget {...props} />);
    expect(
      localStorage.getItem('opencode.settings.dat:defaultServerUrl'),
    ).toBe('http://localhost:3000/api/widget/abc/');
    expect(localStorage.getItem('static.key')).toBe('static-value');
  });
```

- [ ] **Step 2: Run the test and watch it fail**

```bash
cd packages/optio-ui && npm test -- IframeWidget
```

Expected: the new `it` fails — the override writes the literal string `{widgetProxyUrl}` to localStorage.

- [ ] **Step 3: Implement the substitution**

In `packages/optio-ui/src/widgets/IframeWidget.tsx`, replace the current `useEffect` block with:

```typescript
  useEffect(() => {
    if (!widgetData?.localStorageOverrides) return;
    const keys = Object.keys(widgetData.localStorageOverrides);
    for (const k of keys) {
      const raw = widgetData.localStorageOverrides[k];
      const resolved = raw.replace(/\{widgetProxyUrl\}/g, props.widgetProxyUrl);
      localStorage.setItem(k, resolved);
    }
    return () => {
      for (const k of keys) localStorage.removeItem(k);
    };
  }, [widgetData?.localStorageOverrides, props.widgetProxyUrl]);
```

- [ ] **Step 4: Run test again**

```bash
cd packages/optio-ui && npm test -- IframeWidget
```

Expected: all IframeWidget tests pass, including the new one. The pre-existing test `writes localStorageOverrides before mount and clears on unmount` still passes because its override value (`'v1'`) contains no template token.

- [ ] **Step 5: Typecheck**

```bash
cd packages/optio-ui && node_modules/.bin/tsc --noEmit
```

Expected: no type errors.

---

## Task 12: Reference demo task in optio-demo

**Files:**

- Create: `packages/optio-demo/src/optio_demo/tasks/opencode.py`
- Modify: `packages/optio-demo/src/optio_demo/tasks/__init__.py`
- Modify: `packages/optio-demo/pyproject.toml`

Spec reference: Section 10 (Reference Demo Task).

- [ ] **Step 1: Add the dependency**

In `packages/optio-demo/pyproject.toml`, under `dependencies`:

```toml
dependencies = [
    "optio-core[redis]",
    "marimo>=0.9",
    "optio-opencode",
]
```

- [ ] **Step 2: Create the demo task**

Create `packages/optio-demo/src/optio_demo/tasks/opencode.py`:

```python
"""Reference demo task for optio-opencode.

Local-mode only, per the design spec (Section 10).  The user must have
opencode installed and authenticated on their machine.

Consumer prompt is the color-and-42 prompt from the spec.  The
deliverable callback surfaces the file contents back into the optio log
channel so the human can visually confirm round-trip success.
"""

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_opencode import OpencodeTaskConfig, create_opencode_task


CONSUMER_PROMPT = (
    "Ask the human about their favorite color, then ship a deliverable "
    "containing the number 42 and the designated color. Then signal that "
    "you have finished."
)


def _make_on_deliverable(ctx: ProcessContext):
    async def _cb(path: str, text: str) -> None:
        ctx.report_progress(
            None,
            f"callback received deliverable at {path}: {text[:200]}",
        )
    return _cb


def get_tasks() -> list[TaskInstance]:
    async def _execute(ctx: ProcessContext) -> None:
        config = OpencodeTaskConfig(
            consumer_instructions=CONSUMER_PROMPT,
            opencode_config={},
            ssh=None,  # local mode for the demo
            on_deliverable=_make_on_deliverable(ctx),
        )
        inner = create_opencode_task(
            process_id="opencode-demo-inner",
            name="opencode demo inner",
            config=config,
        )
        await inner.execute(ctx)

    return [
        TaskInstance(
            execute=_execute,
            process_id="opencode-demo",
            name="Opencode demo",
            description="Local opencode session asking for a color and shipping a deliverable",
            ui_widget="iframe",
        )
    ]
```

- [ ] **Step 3: Register the task**

In `packages/optio-demo/src/optio_demo/tasks/__init__.py`, add the import and spread:

```python
"""Task definitions for the optio demo application."""

from optio_core.models import TaskInstance

from optio_demo.tasks.terraforming import get_tasks as terraforming_tasks
from optio_demo.tasks.home import get_tasks as home_tasks
from optio_demo.tasks.heist import get_tasks as heist_tasks
from optio_demo.tasks.festival import get_tasks as festival_tasks
from optio_demo.tasks.wakeup import get_tasks as wakeup_tasks
from optio_demo.tasks.marimo import get_tasks as marimo_tasks
from optio_demo.tasks.opencode import get_tasks as opencode_tasks


async def get_task_definitions(services: dict) -> list[TaskInstance]:
    return [
        *terraforming_tasks(),
        *home_tasks(),
        *heist_tasks(),
        *festival_tasks(),
        *wakeup_tasks(),
        *marimo_tasks(),
        *opencode_tasks(),
    ]
```

- [ ] **Step 4: Install the new dep and verify import**

```bash
/home/csillag/deai/pyenv/shims/python -m pip install -e packages/optio-demo
/home/csillag/deai/pyenv/shims/python -c "from optio_demo.tasks.opencode import get_tasks; print([t.process_id for t in get_tasks()])"
```

Expected: `['opencode-demo']`.

---

## Task 13: Update AGENTS.md files

**Files:**

- Create: `packages/optio-opencode/AGENTS.md`
- Modify: `AGENTS.md` (root)
- Modify: `packages/optio-demo/AGENTS.md`

- [ ] **Step 1: Write the package-level AGENTS.md**

Create `packages/optio-opencode/AGENTS.md`:

```markdown
# optio-opencode — Agent Cheatsheet

Run `opencode web` as an optio task.  Either a local subprocess or a
remote host reached over SSH; the optio dashboard embeds opencode's UI
via the widget proxy.

Full design: `docs/2026-04-22-optio-opencode-design.md`.

## Public API

```python
from optio_opencode import (
    create_opencode_task,
    OpencodeTaskConfig,
    SSHConfig,
    DeliverableCallback,
)

async def on_file(path: str, text: str) -> None:
    ...

task = create_opencode_task(
    process_id="my-task",
    name="My task",
    config=OpencodeTaskConfig(
        consumer_instructions="...",      # prepended with optio-opencode's base prompt
        opencode_config={"model": "..."},  # passthrough to opencode.json
        ssh=None,                          # None = local subprocess
        on_deliverable=on_file,
        install_if_missing=True,
    ),
    description="optional",
)
```

The returned `TaskInstance` has `ui_widget="iframe"` baked in.

## Log-file contract

optio-opencode tells opencode (via AGENTS.md) to append one line per
event to `./optio.log` with keywords:

- `STATUS: [N%] <msg>`
- `DELIVERABLE: <path>`
- `DONE[: summary]`
- `ERROR[: message]`

The Python side tails the file, dispatches by keyword, SFTPs
deliverable files back, decodes UTF-8, and invokes `on_deliverable`.
DONE / ERROR terminate the session; other keywords flow through as
progress updates / log entries.

## Operating modes

- **Local**: `asyncio.create_subprocess_exec("opencode", "web", ...)`.
  Workdir is a `tempfile.mkdtemp`.  Expects opencode pre-installed.
- **Remote**: single asyncssh connection multiplexes exec (install,
  launch, `tail -F`, teardown), SFTP, and local port forward.  Workdir
  is `/tmp/optio-opencode-<uuid>/` on the remote.

## Testing

Unit + local integration: `pytest tests/` (needs MongoDB via Docker).

Remote integration: `pytest tests/test_session_remote.py` — skips on
machines without Docker; brings up `linuxserver/openssh-server` on
`127.0.0.1:22222`.

## Known limits (MVP)

- SSH auth is key-path only; no agent, inline keys, or passwords.
- No host-key verification (`known_hosts=None`).
- Fail-fast on SSH drop; no reconnect.
- Text deliverables only (non-UTF-8 files are skipped, not delivered).
- Grace-period sensitive: teardown can exceed 5 s; host apps running
  slow / remote sessions should call
  `optio_core.shutdown(grace_seconds=30)`.

Deferred items live in spec Section 11.
```

- [ ] **Step 2: Append `optio-opencode` row to the root AGENTS.md integration table**

In `/home/csillag/deai/optio/AGENTS.md`, find the `## Integration Levels` table (around line ~40) and append a new row after the optio-core rows:

```markdown
| 1+ — Opencode runner | `optio-opencode` | Python | workspace; runs `opencode web` as an optio task (local subprocess or remote via SSH) |
```

Also add a short subsection under the existing "Python: optio-core" section:

```markdown
## Python: optio-opencode

Run `opencode web` as an optio task.  Public API:

```python
from optio_opencode import create_opencode_task, OpencodeTaskConfig, SSHConfig

task = create_opencode_task(
    process_id="my-task", name="My task",
    config=OpencodeTaskConfig(
        consumer_instructions="...",
        opencode_config={...},     # passthrough to opencode.json
        ssh=None,                  # None = local subprocess
        on_deliverable=cb,
    ),
)
```

Full details: `packages/optio-opencode/AGENTS.md`.
```

- [ ] **Step 3: Append the new task to `packages/optio-demo/AGENTS.md`**

If `packages/optio-demo/AGENTS.md` has a task list, add a line mentioning `opencode-demo` — a local-mode reference demo for `optio-opencode`.  If no task list exists, a short section is fine:

```markdown
## Opencode demo

Task `opencode-demo` runs a short local opencode session that asks the
human for a favorite color and ships a deliverable containing the
color and the number 42.  Reference consumer for `optio-opencode`;
exercises the full iframe + proxy + log-tail stack.  Requires opencode
to be installed and authenticated on the developer's machine.
```

---

## Task 14: Full test sweep

- [ ] **Step 1: Run Python tests**

```bash
# Start mongo if not already up.
docker compose -f packages/optio-demo/docker-compose.yml up -d
cd packages/optio-opencode && /home/csillag/deai/pyenv/shims/python -m pytest -v
```

Expected: all tests pass. The remote-integration test will skip if Docker isn't available; that's fine.

- [ ] **Step 2: Run optio-ui tests**

```bash
cd packages/optio-ui && npm test
```

Expected: all tests pass (existing + the new substitution test).

- [ ] **Step 3: Typecheck**

```bash
cd packages/optio-ui && node_modules/.bin/tsc --noEmit
```

Expected: no type errors.

---

## Task 15: Manual verification of the reference demo

Per spec Section 10, the demo is human-driven — there is no automated E2E. Run through the walkthrough once before committing.

- [ ] **Step 1: Start the stack**

```bash
# From the repo root:
docker compose -f packages/optio-demo/docker-compose.yml up -d
(cd packages/optio-demo && /home/csillag/deai/pyenv/shims/python -m optio_demo &)
(cd packages/optio-dashboard && npm run dev &)
```

- [ ] **Step 2: Verify the walkthrough**

In the dashboard (open in a browser):

1. Authenticate.
2. Launch the `opencode-demo` task.
3. Open its process detail view. Iframe mounts; opencode's UI loads (via the proxy — watch the browser devtools network tab for requests under `/api/widget/.../`; they should succeed 200).
4. The LLM asks for a favorite color in the iframe.
5. Type a color into opencode's input.
6. Watch `STATUS:` lines flow into the dashboard's log pane.
7. When the LLM writes the deliverable, confirm the "callback received deliverable" line appears in the dashboard log, containing both `42` and the color.
8. On `DONE`, the process transitions to `done`; the "session ended" banner appears on the iframe; the workdir is removed.
9. Dismiss the process.

- [ ] **Step 3: Stop the stack**

```bash
# Stop dashboard + demo (fg + Ctrl-C each, or kill by PID).
docker compose -f packages/optio-demo/docker-compose.yml down
```

If any step fails, stop before committing. Diagnose (dashboard devtools, demo process log, mongo state via `mongosh`) and fix. Do not commit broken verification.

---

## Task 16: Final commit

- [ ] **Step 1: Stage everything and inspect**

```bash
git -C /home/csillag/deai/optio status
git -C /home/csillag/deai/optio add \
    packages/optio-opencode \
    packages/optio-ui/src/widgets/IframeWidget.tsx \
    packages/optio-ui/src/__tests__/IframeWidget.test.tsx \
    packages/optio-demo/pyproject.toml \
    packages/optio-demo/src/optio_demo/tasks/__init__.py \
    packages/optio-demo/src/optio_demo/tasks/opencode.py \
    packages/optio-demo/AGENTS.md \
    AGENTS.md
git -C /home/csillag/deai/optio status
```

Expected status: all the above files are staged; nothing else. No stray `*.pyc`, no editor backup files, no `tests/ssh-keys/` (those are generated test artifacts; add them to `.gitignore` if they appear).

- [ ] **Step 2: Add `.gitignore` entries for test artifacts**

If `git status` shows any of these as untracked, append them to `packages/optio-opencode/.gitignore`:

```
tests/ssh-keys/
tests/scenario.txt
*.egg-info/
__pycache__/
```

Then `git add packages/optio-opencode/.gitignore`.

- [ ] **Step 3: Create the commit (no co-author, single commit for the whole plan)**

```bash
git -C /home/csillag/deai/optio commit -m "$(cat <<'EOF'
feat: add optio-opencode package

Ship the optio-opencode Python package per the 2026-04-22 design spec:
a factory that runs `opencode web` as an optio task, either as a local
subprocess or on a remote host over SSH, with opencode's UI embedded
in the optio dashboard via the widget proxy.  The package tails a
keyword-prefixed log file the LLM writes to (STATUS, DELIVERABLE, DONE,
ERROR), fetches deliverables by SFTP, and dispatches them to a
consumer-supplied callback.

Adjacent changes:
- optio-ui: IframeWidget now substitutes `{widgetProxyUrl}` in
  widgetData.localStorageOverrides values, letting opencode's SPA
  route through the widget proxy.
- optio-demo: new opencode-demo reference task (local mode) asking
  the human for a favorite color and shipping a deliverable with
  42 + the color.

Coverage: unit tests for types, prompt composer, line parser, and
workdir path validation; LocalHost unit tests; full-suite local-mode
integration with a Python test-double binary; remote-mode integration
via a linuxserver/openssh-server container (skip-on-no-docker).  The
reference demo is human-driven per spec Section 10.

Phase-2 items deferred per spec Section 11 (post-disconnect cleanup
child, SSH reconnect, binary deliverables, etc.).
EOF
)"
```

- [ ] **Step 4: Verify**

```bash
git -C /home/csillag/deai/optio log -1
git -C /home/csillag/deai/optio status
```

Expected: one new commit, clean working tree.

---

## Self-review checklist (for the plan author, already performed)

- **Spec coverage:** Every spec section (1-11, plus 5a and 11a) is covered:
  - 1 Purpose/Scope → Task 1 scaffolding.
  - 2 Architecture → Task 5 (Protocol) + Task 6 (Local) + Task 7 (Remote).
  - 3 Consumer interface → Task 2 (types) + Task 8 (factory).
  - 4 Workdir layout → Task 3 (`validate_deliverable_path`) + Task 4 (`compose_agents_md`) + Task 6 (`setup_workdir`).
  - 5 Runtime lifecycle → Task 8 (`run_opencode_session`).
  - 5a widgetProxyUrl wrinkle → Task 11 (optio-ui).
  - 6 Log tail / deliverable fetch → Task 3 (parser) + Task 8 (`_tail_and_dispatch`, `_deliverable_fetch_loop`).
  - 7 Provider credentials → Task 2 (`opencode_config` dict passthrough) + design-only, no code.
  - 8 Failure modes → Task 9 (local integration test covers every row that can be exercised locally).
  - 9 Testing approach → Tasks 9 (local) + 10 (remote).
  - 10 Reference demo → Task 12 + Task 15 (manual walkthrough).
  - 11 Deferred → not implemented (by design); acknowledged in `AGENTS.md`.
  - 11a optio-ui change → Task 11.
- **No placeholders:** No "TBD"/"TODO"/"implement later" remaining. Every code step contains the complete code the engineer needs.
- **Type consistency:** `Host` Protocol methods match their usage in `session.py`. `LaunchedProcess.pid_like` is opaque to session.py — the session only calls `process.pid_like` via `host.terminate_opencode(process, ...)` and `_await_subprocess_exit`, which works for both LocalHost's `asyncio.subprocess.Process` and RemoteHost's `asyncssh.SSHClientProcess`.
- **TDD discipline:** Every task with code writes the failing test first, runs it, then implements, then runs until green. No commits per task — Task 16 is the single commit.
