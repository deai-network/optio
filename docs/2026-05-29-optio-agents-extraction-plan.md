# optio-agents Package Extraction (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract a new `optio-agents` Python package out of `optio-host`, moving the keyword-protocol parser, the coordination-session driver, and `HookContext`/`HookContextProtocol` into it, while relocating `RunResult`/`HostCommandError` into `optio_host.host` and consolidating the LLM-facing keyword documentation into a single source of truth (`optio_agents/protocol/prompt.py`).

**Architecture:** This is a pure structural refactor — runtime behavior is unchanged. `optio-agents` depends on `optio-host` and `optio-core` (no cycle). Symbols move verbatim except for import-path edits. The opencode prompt builder is rewritten to compose the new SSOT prompt block, staying semantically equivalent. Acceptance = full suite green after a clean `make install`, and grep finds no `optio_host.protocol` or `optio_host.context.HookContext` references.

**Tech Stack:** Python 3.11+, setuptools, pytest + pytest-asyncio (`asyncio_mode = "auto"`), Makefile editable-install loop. Repo venv: `/home/csillag/deai/optio/.venv`.

**Spec:** `docs/2026-05-29-optio-agents-extraction-design.md`

---

## File Structure

**New package `packages/optio-agents/`:**
- `pyproject.toml` — package metadata; deps `optio-core`, `optio-host`; dev extras pytest + pytest-asyncio; mirrors optio-host structure.
- `README.md` — describes the coordination protocol + keyword SSOT.
- `src/optio_agents/__init__.py` — top-level exports: `HookContext`, `HookContextProtocol`, and the protocol surface.
- `src/optio_agents/context.py` — `HookContext` + `HookContextProtocol` + `_resolve_target_path` (moved from `optio_host.context`).
- `src/optio_agents/protocol/__init__.py` — re-exports the protocol surface (parser + session symbols).
- `src/optio_agents/protocol/parser.py` — keyword parser (moved verbatim from `optio_host.protocol.parser`).
- `src/optio_agents/protocol/session.py` — coordination driver (moved verbatim from `optio_host.protocol.session`, imports repointed).
- `src/optio_agents/protocol/prompt.py` — NEW: the SSOT "## Log channel" + "## Deliverables" keyword block.
- `tests/conftest.py` — `tmp_workdir` fixture (copied from optio-host conftest; needed by moved parser tests).
- `tests/test_protocol_parser.py` — moved from optio-host (imports repointed).
- `tests/test_context.py` — moved from optio-host (imports repointed).
- `tests/test_download.py` — the `HookContext.download_file` routing tests split out of optio-host's `test_download.py` (imports repointed).
- `tests/test_prompt.py` — NEW: tests for the SSOT prompt block.

**Modified in `packages/optio-host/`:**
- `src/optio_host/host.py` — gains `RunResult` + `HostCommandError` definitions (moved from `context.py`); drops the `from optio_host.context import RunResult` import.
- `src/optio_host/context.py` — DELETED (no residents left).
- `src/optio_host/protocol/` — DELETED (parser.py, session.py, __init__.py moved to optio-agents).
- `src/optio_host/__init__.py` — drop `HookContext`/`HookContextProtocol`; import `RunResult`/`HostCommandError` from `host` not `context`.
- `tests/test_context.py` — DELETED (moved).
- `tests/test_protocol_parser.py` — DELETED (moved).
- `tests/test_download.py` — `HookContext.download_file` routing tests removed (moved); factory + curl tests stay.
- `README.md` — drop "+ log/deliverables protocol" framing; point to optio-agents.
- `pyproject.toml` — `description` reworded (drops protocol framing).

**Modified in `packages/optio-opencode/`:**
- `src/optio_opencode/types.py` — `DeliverableCallback, HookCallback` ← `optio_agents.protocol.session`.
- `src/optio_opencode/session.py` — `HookContext` ← `optio_agents`; `_SessionFailed, run_log_protocol_session` ← `optio_agents.protocol.session`.
- `src/optio_opencode/__init__.py` — re-export `HookContext, HookContextProtocol` from `optio_agents`.
- `src/optio_opencode/prompt.py` — `BASE_PROMPT_PRE` rewritten to compose the optio-agents SSOT block.
- `pyproject.toml` — add `optio-agents>=0.1,<0.2` to dependencies.
- `tests/test_session_hooks.py` — repoint `RunResult` → `optio_host.host`; `HookContext`, `_deliverable_fetch_loop` → `optio_agents`.
- `tests/test_smart_install.py` — repoint `RunResult` → `optio_host.host`; `HookContext` → `optio_agents`.
- `tests/test_host_local.py` — repoint `fetch_deliverable_text` → `optio_agents.protocol.session`.

**Modified at repo root:**
- `Makefile` — `PY_PACKAGES := optio-core optio-host optio-agents optio-opencode`.

---

## Task 1: Relocate RunResult + HostCommandError into optio_host.host

**Files:**
- Modify: `packages/optio-host/src/optio_host/host.py:21`
- Modify: `packages/optio-host/src/optio_host/context.py:9-31`

This breaks the current inversion (`host.py:21` importing `RunResult` from `context.py`) so that the transport result/error types live where `run_command` produces them. `context.py` keeps its remaining residents (HookContext etc.) until Task 4 moves them.

- [ ] **Step 1: Add RunResult + HostCommandError definitions to host.py**

In `packages/optio-host/src/optio_host/host.py`, replace the import line at line 21:

```python
from optio_host.context import RunResult
```

with the inline definitions (delete the import, add the classes right after the existing `from dataclasses import dataclass` / `from typing import ...` block at the top of the module, before `class ProcessHandle`):

```python
@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int


class HostCommandError(Exception):
    def __init__(
        self,
        command: str,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> None:
        self.command = command
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(
            f"command failed (exit {exit_code}): {command!r}\n"
            f"stderr: {stderr[:200]}"
        )
```

(`dataclass` is already imported at `host.py:18`.)

- [ ] **Step 2: Point context.py at the new location**

In `packages/optio-host/src/optio_host/context.py`, delete the `RunResult` dataclass (lines 9-13) and the `HostCommandError` class (lines 16-31), and add an import near the top of the file (after `from dataclasses import dataclass`):

```python
from optio_host.host import HostCommandError, RunResult
```

The `HookContext.run_on_host` method (still in context.py for now) references `RunResult`/`HostCommandError` — keep it working via this import. The `from dataclasses import dataclass` import in context.py is now unused; remove it.

- [ ] **Step 3: Update optio_host/__init__.py to import from host**

In `packages/optio-host/src/optio_host/__init__.py`, change the import block at lines 8-13 so `RunResult` and `HostCommandError` come from `host`, not `context`:

```python
from optio_host.context import (
    HookContext,
    HookContextProtocol,
)
from optio_host.download import DownloadFailed, create_download_task
from optio_host.host import (
    Host,
    HostCommandError,
    LocalHost,
    ProcessHandle,
    RemoteHost,
    RunResult,
    make_host,
)
from optio_host.types import SSHConfig
```

Leave `__all__` unchanged (still lists all of the above including `HookContext`, `HookContextProtocol`, `RunResult`, `HostCommandError`).

- [ ] **Step 4: Run optio-host tests to verify no regression**

Run: `cd packages/optio-host && ../../.venv/bin/python -m pytest tests/test_context.py tests/test_download.py -q`
Expected: PASS (all tests, e.g. `... passed`). `test_context.py` imports `RunResult`/`HostCommandError` from `optio_host.context`, which now re-exports them — still works.

- [ ] **Step 5: Run the opencode tests that import RunResult from context**

Run: `cd packages/optio-opencode && ../../.venv/bin/python -m pytest tests/test_smart_install.py -q`
Expected: PASS. (These still import `from optio_host.context import RunResult`, which re-exports — repointed in Task 9.)

- [ ] **Step 6: Commit**

```bash
git add packages/optio-host/src/optio_host/host.py packages/optio-host/src/optio_host/context.py packages/optio-host/src/optio_host/__init__.py
git commit -m "refactor(optio-host): relocate RunResult + HostCommandError into host.py"
```

---

## Task 2: Scaffold the optio-agents package

**Files:**
- Create: `packages/optio-agents/pyproject.toml`
- Create: `packages/optio-agents/src/optio_agents/__init__.py`
- Create: `packages/optio-agents/tests/conftest.py`

Mirror optio-host's `pyproject.toml` structure exactly, swapping the package metadata and dependency set.

- [ ] **Step 1: Write pyproject.toml**

Create `packages/optio-agents/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "optio-agents"
version = "0.1.0"
description = "Agent-coordination layer for optio task types: the optio.log keyword protocol, its session driver, and the LLM-facing keyword documentation (SSOT)."
readme = "README.md"
license = "Apache-2.0"
requires-python = ">=3.11"
authors = [
    { name = "Kristof Csillag", email = "kristof.csillag@deai-labs.com" },
]
classifiers = [
    "Development Status :: 4 - Beta",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Operating System :: POSIX :: Linux",
    "Operating System :: MacOS",
    "Topic :: Software Development :: Libraries :: Python Modules",
    "Topic :: System :: Distributed Computing",
    "Framework :: AsyncIO",
]
dependencies = [
    "optio-core>=0.1,<0.2",
    "optio-host>=0.1,<0.2",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[project.urls]
Homepage = "https://github.com/deai-network/optio"
Repository = "https://github.com/deai-network/optio"
Issues = "https://github.com/deai-network/optio/issues"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Write a placeholder package __init__.py**

Create `packages/optio-agents/src/optio_agents/__init__.py` with a minimal docstring (real exports added in Task 6):

```python
"""optio-agents — agent-coordination layer for optio task types.

Owns the optio.log keyword protocol (parser + session driver), the
``HookContext`` passed to agent task hooks, and the single source of
truth for the LLM-facing keyword-protocol documentation.
"""
```

- [ ] **Step 3: Write the tests conftest.py**

Create `packages/optio-agents/tests/conftest.py` (copied from `optio-host/tests/conftest.py`; the moved parser tests use `tmp_workdir`):

```python
"""Shared test fixtures for optio-agents."""

import shutil
import tempfile

import pytest


@pytest.fixture
def tmp_workdir():
    """A temporary directory that is removed after the test."""
    path = tempfile.mkdtemp(prefix="optio-agents-test-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
```

- [ ] **Step 4: Install the new package editable into the venv**

Run: `/home/csillag/deai/optio/.venv/bin/pip install -e packages/optio-agents`
Expected: ends with `Successfully installed optio-agents-0.1.0` (or `already satisfied` for deps).

- [ ] **Step 5: Verify the package imports**

Run: `/home/csillag/deai/optio/.venv/bin/python -c "import optio_agents; print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add packages/optio-agents/pyproject.toml packages/optio-agents/src/optio_agents/__init__.py packages/optio-agents/tests/conftest.py
git commit -m "feat(optio-agents): scaffold package (pyproject, init, conftest)"
```

---

## Task 3: Move the parser into optio-agents

**Files:**
- Create: `packages/optio-agents/src/optio_agents/protocol/__init__.py`
- Create: `packages/optio-agents/src/optio_agents/protocol/parser.py`
- Create: `packages/optio-agents/tests/test_protocol_parser.py`
- Delete: `packages/optio-host/src/optio_host/protocol/parser.py` (in Task 5, after session moves)
- Delete: `packages/optio-host/tests/test_protocol_parser.py`

The parser is pure stdlib — moved verbatim.

- [ ] **Step 1: Create the moved parser tests (failing import)**

Create `packages/optio-agents/tests/test_protocol_parser.py` — copy `packages/optio-host/tests/test_protocol_parser.py` verbatim but change both import statements (`from optio_host.protocol.parser import ...` at lines 3 and 138) to `from optio_agents.protocol.parser import ...`. The full file:

```python
import pytest

from optio_agents.protocol.parser import (
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


# ---- relativize_deliverable_path ----

from optio_agents.protocol.parser import relativize_deliverable_path


def test_relativize_direct_child_of_deliverables(tmp_workdir):
    import os
    abs_path = os.path.join(tmp_workdir, "deliverables", "foo.md")
    assert relativize_deliverable_path(abs_path, tmp_workdir) == "foo.md"


def test_relativize_nested_under_deliverables(tmp_workdir):
    import os
    abs_path = os.path.join(tmp_workdir, "deliverables", "sub", "foo.md")
    expected = os.path.join("sub", "foo.md")
    assert relativize_deliverable_path(abs_path, tmp_workdir) == expected


def test_relativize_inside_workdir_but_not_deliverables_rejected(tmp_workdir):
    import os
    abs_path = os.path.join(tmp_workdir, "foo.md")
    with pytest.raises(ValueError):
        relativize_deliverable_path(abs_path, tmp_workdir)


def test_relativize_sibling_dir_with_deliverables_prefix_rejected(tmp_workdir):
    import os
    abs_path = os.path.join(tmp_workdir, "deliverables_other", "foo.md")
    with pytest.raises(ValueError):
        relativize_deliverable_path(abs_path, tmp_workdir)


def test_relativize_deliverables_root_itself_rejected(tmp_workdir):
    import os
    abs_path = os.path.join(tmp_workdir, "deliverables")
    with pytest.raises(ValueError):
        relativize_deliverable_path(abs_path, tmp_workdir)


def test_relativize_outside_workdir_rejected(tmp_workdir):
    with pytest.raises(ValueError):
        relativize_deliverable_path("/etc/passwd", tmp_workdir)
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest tests/test_protocol_parser.py -q`
Expected: collection error / FAIL — `ModuleNotFoundError: No module named 'optio_agents.protocol'`.

- [ ] **Step 3: Create the protocol package and move parser.py verbatim**

Create `packages/optio-agents/src/optio_agents/protocol/parser.py` with the exact contents of `packages/optio-host/src/optio_host/protocol/parser.py` (no edits — it is pure stdlib). The full file:

```python
"""Parse lines from the optio.log file that the on-host agent appends to.

The format is keyword-prefixed, one line per event. See the design spec
Section 6 of the optio-opencode design (the protocol's original home;
unchanged after the optio-host split).
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


DELIVERABLES_SUBDIR = "deliverables"


def relativize_deliverable_path(absolute_path: str, workdir: str) -> str:
    """Return ``absolute_path`` made relative to ``<workdir>/deliverables/``.

    Precondition: ``absolute_path`` has already been validated to be
    inside ``workdir`` (via :func:`validate_deliverable_path`). Both
    arguments may be any absolute paths; this function realpaths them
    internally before relativizing.

    Raises ``ValueError`` if ``absolute_path`` is not strictly under
    ``<workdir>/deliverables/`` (including when it equals the
    deliverables root itself or escapes outside it).
    """
    deliverables_root = os.path.realpath(
        os.path.join(workdir, DELIVERABLES_SUBDIR)
    )
    target = os.path.realpath(absolute_path)
    rel = os.path.relpath(target, deliverables_root)
    if rel == "." or rel == ".." or rel.startswith(".." + os.sep):
        raise ValueError(
            f"deliverable path is not under <workdir>/{DELIVERABLES_SUBDIR}/: "
            f"{absolute_path!r} (workdir={workdir!r})"
        )
    return rel
```

- [ ] **Step 4: Create protocol/__init__.py exporting the parser surface**

Create `packages/optio-agents/src/optio_agents/protocol/__init__.py` (session symbols are added in Task 5):

```python
"""optio-agents log/deliverables coordination protocol.

Built on top of optio_host.host primitives. Knows nothing about specific
consumer task types (opencode, recipe execution, ...).
"""

from optio_agents.protocol.parser import (
    DeliverableEvent,
    DoneEvent,
    ErrorEvent,
    LogEvent,
    StatusEvent,
    UnknownLine,
    parse_log_line,
    relativize_deliverable_path,
    validate_deliverable_path,
)

__all__ = [
    # parser
    "parse_log_line",
    "DeliverableEvent",
    "DoneEvent",
    "ErrorEvent",
    "LogEvent",
    "StatusEvent",
    "UnknownLine",
    "validate_deliverable_path",
    "relativize_deliverable_path",
]
```

- [ ] **Step 5: Run the moved parser test to verify it passes**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest tests/test_protocol_parser.py -q`
Expected: PASS — `33 passed`.

- [ ] **Step 6: Delete the old parser test from optio-host**

Run: `git rm packages/optio-host/tests/test_protocol_parser.py`
(The source `optio_host/protocol/parser.py` stays for now; session.py still imports it. It is deleted in Task 5.)

- [ ] **Step 7: Commit**

```bash
git add packages/optio-agents/src/optio_agents/protocol/__init__.py packages/optio-agents/src/optio_agents/protocol/parser.py packages/optio-agents/tests/test_protocol_parser.py
git commit -m "feat(optio-agents): move keyword parser + its tests from optio-host"
```

---

## Task 4: Move HookContext into optio-agents

**Files:**
- Create: `packages/optio-agents/src/optio_agents/context.py`
- Create: `packages/optio-agents/tests/test_context.py`
- Delete: `packages/optio-host/src/optio_host/context.py`
- Delete: `packages/optio-host/tests/test_context.py`
- Modify: `packages/optio-host/src/optio_host/__init__.py`

`HookContext`/`HookContextProtocol`/`_resolve_target_path` move to `optio_agents.context`. They import `RunResult`/`HostCommandError` from `optio_host.host` (Task 1) and `create_download_task` from `optio_host.download`.

- [ ] **Step 1: Create the moved HookContext tests (failing import)**

Create `packages/optio-agents/tests/test_context.py` — copy `packages/optio-host/tests/test_context.py` verbatim, then repoint every `optio_host.context` import to `optio_agents.context`. There are six such import sites (lines 5, 74, 125, 133, 143, 152, 161). The full file:

```python
"""Tests for HookContext foundations: dataclasses + path resolver."""

import pytest

from optio_agents.context import (
    HostCommandError,
    RunResult,
    _resolve_target_path,
)


def test_run_result_fields():
    r = RunResult(stdout="hi\n", stderr="", exit_code=0)
    assert r.stdout == "hi\n"
    assert r.stderr == ""
    assert r.exit_code == 0


def test_host_command_error_str_includes_exit_code_and_stderr():
    err = HostCommandError(
        command="false", exit_code=1, stdout="", stderr="boom\n",
    )
    s = str(err)
    assert "exit 1" in s
    assert "boom" in s
    assert "false" in s


def test_resolve_target_path_workdir_relative():
    out = _resolve_target_path("data/foo.yaml", "/wd", "/home/u")
    assert out == "/wd/data/foo.yaml"


def test_resolve_target_path_absolute_passthrough():
    assert _resolve_target_path("/usr/local/bin/tool", "/wd", "/home/u") == "/usr/local/bin/tool"


def test_resolve_target_path_home_relative_expanded():
    assert _resolve_target_path("~/.local/bin/tool", "/wd", "/home/u") == "/home/u/.local/bin/tool"


def test_resolve_target_path_bare_tilde_expanded():
    assert _resolve_target_path("~", "/wd", "/home/u") == "/home/u"


def test_resolve_target_path_rejects_empty():
    with pytest.raises(ValueError):
        _resolve_target_path("", "/wd", "/home/u")


def test_resolve_target_path_rejects_dotdot_in_workdir_relative():
    with pytest.raises(ValueError):
        _resolve_target_path("data/../../etc/passwd", "/wd", "/home/u")


def test_resolve_target_path_rejects_workdir_relative_escape():
    # Even after normalization, must stay inside workdir.
    with pytest.raises(ValueError):
        _resolve_target_path("../outside", "/wd", "/home/u")


def test_resolve_target_path_dotdot_allowed_in_absolute():
    # Absolute paths are consumer-trusted; .. is fine there.
    assert _resolve_target_path("/usr/../tmp/x", "/wd", "/home/u") == "/usr/../tmp/x"


def test_resolve_target_path_dotdot_allowed_in_home_relative():
    # Home-relative is also consumer-trusted.
    assert _resolve_target_path("~/foo/../bar", "/wd", "/home/u") == "/home/u/foo/../bar"


import asyncio

from optio_agents.context import HookContext, HookContextProtocol


class _FakeCtx:
    def __init__(self):
        self.process_id = "p"
        self.params = {"k": "v"}
        self.calls = []

    def report_progress(self, percent, message=None):
        self.calls.append(("rp", percent, message))


class _FakeHost:
    def __init__(self):
        self.workdir = "/wd"


def test_hook_context_delegates_attributes():
    ctx = _FakeCtx()
    host = _FakeHost()
    h = HookContext(ctx, host)
    # Process-context attributes flow through __getattr__.
    assert h.process_id == "p"
    assert h.params == {"k": "v"}
    h.report_progress(50, "halfway")
    assert ctx.calls == [("rp", 50, "halfway")]


def test_hook_context_protocol_is_protocol():
    # Smoke test: the Protocol exists and has the expected method names.
    methods = {m for m in dir(HookContextProtocol) if not m.startswith("_")}
    expected = {
        "copy_file", "run_on_host", "read_from_host", "read_text_from_host",
        "report_progress", "should_continue", "params", "metadata",
    }
    assert expected <= methods


class _FakeRunHost:
    def __init__(self, results):
        self.workdir = "/wd"
        self._results = list(results)
        self.calls = []

    async def run_command(self, command, *, cwd=None, env=None):
        self.calls.append((command, cwd, env))
        return self._results.pop(0)


async def test_run_on_host_check_true_returns_stdout_on_success():
    from optio_agents.context import RunResult
    host = _FakeRunHost([RunResult(stdout="hi\n", stderr="", exit_code=0)])
    h = HookContext(_FakeCtx(), host)
    out = await h.run_on_host("echo hi")
    assert out == "hi\n"


async def test_run_on_host_check_true_raises_on_nonzero():
    from optio_agents.context import HostCommandError, RunResult
    host = _FakeRunHost([RunResult(stdout="", stderr="boom", exit_code=2)])
    h = HookContext(_FakeCtx(), host)
    with pytest.raises(HostCommandError) as ei:
        await h.run_on_host("false")
    assert ei.value.exit_code == 2
    assert ei.value.stderr == "boom"


async def test_run_on_host_check_false_returns_result_object():
    from optio_agents.context import RunResult
    host = _FakeRunHost([RunResult(stdout="", stderr="oops", exit_code=3)])
    h = HookContext(_FakeCtx(), host)
    res = await h.run_on_host("false", check=False)
    assert res.exit_code == 3
    assert res.stderr == "oops"


async def test_run_on_host_capture_stderr_merges_into_returned_stdout():
    from optio_agents.context import RunResult
    host = _FakeRunHost([RunResult(stdout="o", stderr="e", exit_code=0)])
    h = HookContext(_FakeCtx(), host)
    out = await h.run_on_host("cmd", capture_stderr=True)
    assert out == "oe"


async def test_run_on_host_cwd_is_forwarded():
    from optio_agents.context import RunResult
    host = _FakeRunHost([RunResult(stdout="", stderr="", exit_code=0)])
    h = HookContext(_FakeCtx(), host)
    await h.run_on_host("pwd", cwd="/elsewhere")
    assert host.calls[0][1] == "/elsewhere"


class _FakeCopyHost:
    def __init__(self, *, host_home="/home/u"):
        self.workdir = "/wd"
        self._host_home = host_home
        self.put_calls = []
        self.fetch_calls = []
        self.fetch_returns = b""

    async def resolve_host_home(self):
        return self._host_home

    async def put_file_to_host(
        self, source, absolute_target, *,
        expected_sha256=None, skip_if_unchanged=False, progress_cb=None,
    ):
        # Snapshot the call.
        self.put_calls.append({
            "source": source,
            "absolute_target": absolute_target,
            "expected_sha256": expected_sha256,
            "skip_if_unchanged": skip_if_unchanged,
        })
        if progress_cb is not None:
            progress_cb(None, None)
            progress_cb(50.0, None)
            progress_cb(100.0, None)

    async def fetch_bytes_from_host(self, absolute_path, *, progress_cb=None):
        self.fetch_calls.append(absolute_path)
        return self.fetch_returns


async def test_copy_file_workdir_relative_resolves_to_workdir():
    host = _FakeCopyHost()
    h = HookContext(_FakeCtx(), host)
    await h.copy_file(b"data", "out/foo.bin")
    assert host.put_calls[0]["absolute_target"] == "/wd/out/foo.bin"


async def test_copy_file_absolute_passthrough():
    host = _FakeCopyHost()
    h = HookContext(_FakeCtx(), host)
    await h.copy_file(b"data", "/usr/local/bin/tool")
    assert host.put_calls[0]["absolute_target"] == "/usr/local/bin/tool"


async def test_copy_file_home_relative_expanded():
    host = _FakeCopyHost(host_home="/home/u")
    h = HookContext(_FakeCtx(), host)
    await h.copy_file(b"data", "~/.local/bin/tool")
    assert host.put_calls[0]["absolute_target"] == "/home/u/.local/bin/tool"


async def test_copy_file_path_source_forwarded():
    host = _FakeCopyHost()
    h = HookContext(_FakeCtx(), host)
    await h.copy_file("/worker/path/file", "out.bin")
    assert host.put_calls[0]["source"] == "/worker/path/file"


async def test_copy_file_emits_progress_messages():
    ctx = _FakeCtx()
    host = _FakeCopyHost()
    h = HookContext(ctx, host)
    await h.copy_file(b"data", "out.bin")
    # First call: start message; subsequent: percent.
    msgs = [c[2] for c in ctx.calls]
    assert any("Copying out.bin" in (m or "") for m in msgs)


async def test_copy_file_skip_if_unchanged_emits_verifying_then_done():
    """When the host reports skipped (progress_cb called once with 'already up to date'),
    HookContext should emit Verifying + Already up to date messages."""
    ctx = _FakeCtx()

    class _SkippingHost(_FakeCopyHost):
        async def put_file_to_host(self, source, absolute_target, *,
                                   expected_sha256=None,
                                   skip_if_unchanged=False,
                                   progress_cb=None):
            self.put_calls.append({"source": source, "absolute_target": absolute_target})
            if progress_cb is not None:
                progress_cb(None, "already up to date")

    host = _SkippingHost()
    h = HookContext(ctx, host)
    await h.copy_file(b"data", "out.bin", skip_if_unchanged=True)
    msgs = [c[2] for c in ctx.calls]
    assert any("Verifying out.bin" in (m or "") for m in msgs)
    assert any("Already up to date: out.bin" in (m or "") for m in msgs)


async def test_read_from_host_workdir_relative_resolves():
    host = _FakeCopyHost()
    host.fetch_returns = b"contents"
    h = HookContext(_FakeCtx(), host)
    out = await h.read_from_host("data/x")
    assert out == b"contents"
    assert host.fetch_calls[0] == "/wd/data/x"


async def test_read_text_from_host_decodes_utf8():
    host = _FakeCopyHost()
    host.fetch_returns = "héllo".encode("utf-8")
    h = HookContext(_FakeCtx(), host)
    out = await h.read_text_from_host("data/x")
    assert out == "héllo"


async def test_read_from_host_emits_reading_message():
    ctx = _FakeCtx()
    host = _FakeCopyHost()
    host.fetch_returns = b"abc"
    h = HookContext(ctx, host)
    await h.read_from_host("data/x")
    msgs = [c[2] for c in ctx.calls]
    assert any("Reading x" in (m or "") for m in msgs)


async def test_read_from_host_silent_suppresses_reading_message():
    ctx = _FakeCtx()
    host = _FakeCopyHost()
    host.fetch_returns = b"abc"
    h = HookContext(ctx, host)
    await h.read_from_host("data/x", silent=True)
    msgs = [c[2] for c in ctx.calls]
    assert not any("Reading" in (m or "") for m in msgs)


async def test_read_text_from_host_silent_suppresses_reading_message():
    ctx = _FakeCtx()
    host = _FakeCopyHost()
    host.fetch_returns = b"abc"
    h = HookContext(ctx, host)
    await h.read_text_from_host("data/x", silent=True)
    msgs = [c[2] for c in ctx.calls]
    assert not any("Reading" in (m or "") for m in msgs)


from bson import ObjectId


class _FakeBlobReader:
    def __init__(self, data: bytes):
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def read(self, n=None):
        if n is None:
            data, self._data = self._data, b""
            return data
        out, self._data = self._data[:n], self._data[n:]
        return out


class _BlobCtx(_FakeCtx):
    def __init__(self, blobs: dict):
        super().__init__()
        self._blobs = blobs

    def load_blob(self, file_id):
        # Return an async-context-manager wrapping a reader.
        return _FakeBlobReader(self._blobs[file_id])


async def test_copy_file_objectid_source_streams_blob():
    blob_id = ObjectId()
    payload = b"blob-payload"
    ctx = _BlobCtx({blob_id: payload})
    host = _FakeCopyHost()
    h = HookContext(ctx, host)
    await h.copy_file(blob_id, "out.bin")
    # The host should have received an async iterator that yields the blob bytes.
    src = host.put_calls[0]["source"]
    assert hasattr(src, "__aiter__")
    collected = b"".join([chunk async for chunk in src])
    assert collected == payload


async def test_copy_file_objectid_skip_if_unchanged_supplies_expected_sha():
    import hashlib
    blob_id = ObjectId()
    payload = b"blob-payload"
    expected_sha = hashlib.sha256(payload).hexdigest()
    ctx = _BlobCtx({blob_id: payload})
    host = _FakeCopyHost()
    h = HookContext(ctx, host)
    await h.copy_file(blob_id, "out.bin", skip_if_unchanged=True)
    assert host.put_calls[0]["expected_sha256"] == expected_sha
    assert host.put_calls[0]["skip_if_unchanged"] is True
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest tests/test_context.py -q`
Expected: collection error / FAIL — `ModuleNotFoundError: No module named 'optio_agents.context'`.

- [ ] **Step 3: Create optio_agents/context.py**

Create `packages/optio-agents/src/optio_agents/context.py` with the `HookContext` body moved from optio-host. The `RunResult`/`HostCommandError` definitions are NOT redefined here — they are imported from `optio_host.host` (Task 1). `_resolve_target_path` moves verbatim. The full file:

```python
"""HookContext: ProcessContext + host primitives for task-body hooks."""

from __future__ import annotations

import os
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

# Imported at module top so tests can monkeypatch this name on the module.
from optio_host.download import create_download_task
from optio_host.host import HostCommandError, RunResult


class HookContext:
    """ProcessContext + host primitives, passed to before/after_execute hooks
    and to on_deliverable callbacks.

    Attributes not defined on this class fall through to the wrapped
    ProcessContext via __getattr__, so consumers can call e.g.
    ``hook_ctx.report_progress(...)`` directly.
    """

    def __init__(self, ctx, host) -> None:
        # Use object.__setattr__ to avoid __getattr__ recursion in __init__.
        object.__setattr__(self, "_ctx", ctx)
        object.__setattr__(self, "_host", host)

    def __getattr__(self, name: str) -> Any:
        # Only called if the attribute isn't found on the instance / class.
        return getattr(self._ctx, name)

    async def run_on_host(
        self,
        command: str,
        *,
        check: bool = True,
        capture_stderr: bool = False,
        cwd: str | None = None,
    ):
        result = await self._host.run_command(command, cwd=cwd)
        if check:
            if result.exit_code != 0:
                raise HostCommandError(
                    command=command,
                    exit_code=result.exit_code,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            return result.stdout + (result.stderr if capture_stderr else "")
        return result

    async def copy_file(
        self,
        source,
        target: str,
        *,
        skip_if_unchanged: bool = False,
    ) -> None:
        from bson import ObjectId  # local import to avoid a hard top-level dep

        host_home = await self._host.resolve_host_home()
        abs_target = _resolve_target_path(target, self._host.workdir, host_home)
        basename = os.path.basename(abs_target) or abs_target
        ctx = self._ctx
        skipped = False

        def _progress_cb(percent, message):
            nonlocal skipped
            if message == "already up to date":
                skipped = True
                ctx.report_progress(None, f"Already up to date: {basename}")
            elif percent is not None:
                ctx.report_progress(percent, None)

        # Resolve blob sources to (iterator, expected_sha) pair.
        expected_sha: str | None = None
        if isinstance(source, ObjectId):
            payload = await self._read_blob_bytes(source)
            if skip_if_unchanged:
                import hashlib
                expected_sha = hashlib.sha256(payload).hexdigest()

            async def _gen():
                # Yield the payload as one (or a few) chunks. For very large
                # blobs we could stream via load_blob, but reading-then-iterating
                # is simpler and correct.
                yield payload

            source = _gen()

        if skip_if_unchanged:
            ctx.report_progress(None, f"Verifying {basename}...")
        else:
            ctx.report_progress(None, f"Copying {basename}...")

        await self._host.put_file_to_host(
            source,
            abs_target,
            expected_sha256=expected_sha,
            skip_if_unchanged=skip_if_unchanged,
            progress_cb=_progress_cb,
        )

        if skip_if_unchanged and not skipped:
            ctx.report_progress(None, f"Copying {basename}...")

    async def _read_blob_bytes(self, file_id) -> bytes:
        async with self._ctx.load_blob(file_id) as reader:
            return await reader.read()

    async def read_from_host(self, path: str, *, silent: bool = False) -> bytes:
        host_home = await self._host.resolve_host_home()
        abs_path = _resolve_target_path(path, self._host.workdir, host_home)
        if not silent:
            basename = os.path.basename(abs_path) or abs_path
            self._ctx.report_progress(None, f"Reading {basename}...")

        def _progress_cb(percent, message):
            if percent is not None:
                self._ctx.report_progress(percent, None)

        return await self._host.fetch_bytes_from_host(
            abs_path, progress_cb=_progress_cb,
        )

    async def read_text_from_host(self, path: str, *, silent: bool = False) -> str:
        data = await self.read_from_host(path, silent=silent)
        return data.decode("utf-8")

    async def download_file(
        self,
        url: str,
        target: str,
        *,
        description: str | None = None,
        cleanup_on_fail: bool = True,
    ) -> None:
        """Download ``url`` to ``target`` on the host as a child task.

        Spawns a child process under the current task that runs curl on the
        same host the parent runs on. Reports a single "Downloading
        <basename>" message followed by numeric progress percent updates on
        the child's ProcessContext.

        ``target`` is resolved by the same rules as ``copy_file``: absolute
        path | ``~`` / ``~/...`` home-relative | workdir-relative. On a
        workdir-escape attempt this raises ``ValueError`` without spawning
        a child.

        Returns None on success. Raises
        ``optio_core.exceptions.ChildProcessFailed`` if the child fails;
        the original ``DownloadFailed`` is preserved via ``__cause__``
        (and on the child's ``ChildResult.original_exception`` when caller
        uses ``parallel_group``). Parent-task cancellation propagates to
        the child automatically.
        """
        host_home = await self._host.resolve_host_home()
        abs_target = _resolve_target_path(target, self._host.workdir, host_home)
        basename = os.path.basename(abs_target) or abs_target

        n = self._ctx._child_counter.get("next", 0)
        child_process_id = f"{self._ctx.process_id}.download-{n}"
        child_name = f"download {basename}"

        task = create_download_task(
            process_id=child_process_id,
            name=child_name,
            url=url,
            target=abs_target,
            host=self._host,
            description=description,
            cleanup_on_fail=cleanup_on_fail,
        )
        await self._ctx.run_child_task(task)


class HookContextProtocol(Protocol):
    """Type-hint surface for hook authors who want IDE discoverability.

    Subset of ProcessContext + the four new methods.
    """

    process_id: str

    @property
    def params(self) -> dict: ...

    @property
    def metadata(self) -> dict: ...

    def report_progress(self, percent: float | None, message: str | None = None) -> None: ...
    def should_continue(self) -> bool: ...
    async def copy_file(
        self,
        source,
        target: str,
        *,
        skip_if_unchanged: bool = False,
    ) -> None: ...
    async def run_on_host(
        self,
        command: str,
        *,
        check: bool = True,
        capture_stderr: bool = False,
        cwd: str | None = None,
    ) -> "str | RunResult": ...
    async def read_from_host(self, path: str, *, silent: bool = False) -> bytes: ...
    async def read_text_from_host(self, path: str, *, silent: bool = False) -> str: ...
    async def download_file(
        self,
        url: str,
        target: str,
        *,
        description: str | None = None,
        cleanup_on_fail: bool = True,
    ) -> None: ...


def _resolve_target_path(path: str, workdir: str, host_home: str) -> str:
    """Resolve a user-supplied path to an absolute host path.

    Three forms:
      - starts with `/` → absolute, used as-is
      - `~` or starts with `~/` → home-relative, expand once
      - otherwise → workdir-relative; reject `..` and any escape

    workdir and host_home must both be absolute paths.
    """
    if not path:
        raise ValueError("path must not be empty")
    if path.startswith("/"):
        return path
    if path == "~":
        return host_home
    if path.startswith("~/"):
        return host_home + "/" + path[2:]
    # workdir-relative
    if ".." in path.split("/"):
        raise ValueError(f"workdir-relative path may not contain '..': {path!r}")
    resolved = os.path.normpath(os.path.join(workdir, path))
    workdir_norm = os.path.normpath(workdir).rstrip("/")
    if resolved != workdir_norm and not resolved.startswith(workdir_norm + "/"):
        raise ValueError(f"workdir-relative path escapes workdir: {path!r}")
    return resolved
```

Note: `HostCommandError`/`RunResult` are re-exported from this module (imported at top) so the test's `from optio_agents.context import HostCommandError, RunResult` resolves.

- [ ] **Step 4: Run the moved HookContext test to verify it passes**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest tests/test_context.py -q`
Expected: PASS — `36 passed`.

- [ ] **Step 5: Delete the old context.py and its test from optio-host**

`optio_host.context` has no residents left (`RunResult`/`HostCommandError` moved to host.py in Task 1; `HookContext`/`HookContextProtocol`/`_resolve_target_path` moved here). Delete both:

```bash
git rm packages/optio-host/src/optio_host/context.py packages/optio-host/tests/test_context.py
```

- [ ] **Step 6: Drop HookContext/HookContextProtocol from optio_host/__init__.py**

In `packages/optio-host/src/optio_host/__init__.py`, remove the `from optio_host.context import (...)` block entirely and drop `HookContext`/`HookContextProtocol` from `__all__`. The final file:

```python
"""optio-host — local-or-remote host abstraction.

Top-level public API. See ``optio_host.host`` for primitives and the
``RunResult`` / ``HostCommandError`` transport types. Agent-coordination
concerns (the optio.log keyword protocol, ``HookContext``) live in the
``optio-agents`` package.
"""

from optio_host.download import DownloadFailed, create_download_task
from optio_host.host import (
    Host,
    HostCommandError,
    LocalHost,
    ProcessHandle,
    RemoteHost,
    RunResult,
    make_host,
)
from optio_host.types import SSHConfig

__all__ = [
    "Host",
    "LocalHost",
    "RemoteHost",
    "ProcessHandle",
    "make_host",
    "HostCommandError",
    "RunResult",
    "SSHConfig",
    "DownloadFailed",
    "create_download_task",
]
```

- [ ] **Step 7: Verify optio-host still imports (session.py in protocol/ still imports context — moved next task)**

Run: `cd packages/optio-host && ../../.venv/bin/python -c "import optio_host; print(sorted(optio_host.__all__))"`
Expected: prints the list without `HookContext`/`HookContextProtocol`. (Note: `optio_host.protocol.session` still imports `optio_host.context` — but `import optio_host` does not pull in the protocol subpackage, so this succeeds. The protocol subpackage is removed in Task 5.)

- [ ] **Step 8: Commit**

```bash
git add packages/optio-agents/src/optio_agents/context.py packages/optio-agents/tests/test_context.py packages/optio-host/src/optio_host/__init__.py
git commit -m "feat(optio-agents): move HookContext from optio-host; delete optio_host.context"
```

---

## Task 5: Move the session driver into optio-agents

**Files:**
- Create: `packages/optio-agents/src/optio_agents/protocol/session.py`
- Modify: `packages/optio-agents/src/optio_agents/protocol/__init__.py`
- Delete: `packages/optio-host/src/optio_host/protocol/` (parser.py, session.py, __init__.py)

The session driver moves verbatim except for repointing its two non-host imports (`HookContext` and the parser) to `optio_agents`.

- [ ] **Step 1: Create optio_agents/protocol/session.py with repointed imports**

Create `packages/optio-agents/src/optio_agents/protocol/session.py` — the exact contents of `packages/optio-host/src/optio_host/protocol/session.py`, with two import edits: line 25 `from optio_host.context import HookContext` → `from optio_agents.context import HookContext`; line 27 `from optio_host.protocol.parser import (` → `from optio_agents.protocol.parser import (`. `from optio_host.host import Host` (line 26) is unchanged. The full file:

```python
"""Generic log/deliverables session driver.

`run_log_protocol_session` runs a caller-supplied ``body`` callable
against a ``Host`` while two cooperating tasks consume the
``<workdir>/optio.log`` channel:

  - ``_tail_and_dispatch`` parses each log line into a typed event
    (STATUS / DELIVERABLE / DONE / ERROR) and dispatches accordingly.
  - ``_deliverable_fetch_loop`` drains the deliverable queue, fetches
    each file from the host, decodes UTF-8, and invokes the
    consumer's ``on_deliverable`` callback.

The driver knows nothing about specific consumers (opencode,
recipe-execution, ...). Each consumer's body is responsible for its
own subprocess management and arranging for the agent on the host to
write events to ``<workdir>/optio.log``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Awaitable, Callable

from optio_agents.context import HookContext
from optio_host.host import Host
from optio_agents.protocol.parser import (
    DeliverableEvent,
    DoneEvent,
    ErrorEvent,
    LogEvent,
    StatusEvent,
    UnknownLine,
    parse_log_line,
    relativize_deliverable_path,
    validate_deliverable_path,
)

if TYPE_CHECKING:
    from optio_core.context import ProcessContext


_LOG = logging.getLogger(__name__)


DELIVERABLE_QUEUE_BOUND = 64


# Public type aliases. ``HookContext`` is forward-quoted in these aliases
# so consumers don't need to import HookContext to type-check.
DeliverableCallback = Callable[["HookContext", str, str], Awaitable[None]]
"""Consumer callback invoked per fetched DELIVERABLE.

Arguments: ``(hook_ctx, deliverable_path, decoded_text)``.

``deliverable_path`` is the path of the deliverable file relative to
``<workdir>/deliverables/`` (e.g. ``"summary.md"`` or
``"sub/summary.md"``). It matches the value emitted in the
auto-generated ``"Deliverable: <path>"`` progress message.
"""


HookCallback = Callable[["HookContext"], Awaitable[None]]
"""Hook callback receiving a HookContext. Used by before_execute and
after_execute."""


class _SessionFailed(Exception):
    """Internal signal: drive the surrounding session to ``failed``.

    Re-raised by ``run_log_protocol_session`` when:
      * the agent emits ERROR
      * the body returns without DONE having fired

    Consumers catch this and translate to their own failure semantics.
    """


async def fetch_deliverable_text(host: Host, absolute_path: str) -> str:
    """Read the host file at ``absolute_path`` and decode it as UTF-8.

    Thin wrapper around ``host.fetch_bytes_from_host`` for the common
    text-deliverable case used by the protocol session driver.
    """
    data = await host.fetch_bytes_from_host(absolute_path)
    return data.decode("utf-8")


async def run_log_protocol_session(
    host: Host,
    ctx: "ProcessContext",
    *,
    body: Callable[[Host, HookContext], Awaitable[None]],
    on_deliverable: DeliverableCallback | None = None,
    before_execute: HookCallback | None = None,
    after_execute: HookCallback | None = None,
) -> None:
    """Run ``body`` against ``host`` while the log/deliverables protocol
    cooperates with it.

    Lifecycle:
      1. ``host.setup_workdir()`` (mkdir workdir).
      2. Create ``<workdir>/deliverables/`` and an empty
         ``<workdir>/optio.log``.
      3. ``before_execute(hook_ctx)`` if set.
      4. Spawn three concurrent tasks:
         - ``_tail_and_dispatch``: parse lines from ``optio.log``,
           emit progress / queue deliverables / set done/error flags.
         - ``_deliverable_fetch_loop``: drain queue, fetch + decode,
           invoke ``on_deliverable``.
         - ``body(host, hook_ctx)``: caller's work.
      5. Await ``{tail, body, cancel}`` with ``FIRST_COMPLETED``.
      6. Drain queue, cancel the still-running watchers.
      7. ``after_execute(hook_ctx)`` if set, with the same failure
         semantics: re-raises if the session was healthy, logged
         otherwise.

    Outcomes:
      * Agent emits ``DONE`` → returns clean.
      * Agent emits ``ERROR`` → raises ``_SessionFailed``.
      * Body returns without ``DONE`` having fired → raises
        ``_SessionFailed`` (the body finished prematurely; no
        successful completion signal observed).
      * Process cancellation → returns clean (caller decides what to
        do next).

    What this driver does NOT do:
      * Workdir teardown / ``host.cleanup_taskdir`` — caller's
        responsibility (caller may want to capture a snapshot first).
      * Subprocess termination — body owns its handles.
      * Snapshot / resume — caller brackets around this call.
    """
    hook_ctx = HookContext(ctx, host)

    # Workdir + protocol artifacts. ``setup_workdir`` mkdirs the workdir
    # only; the protocol-specific deliverables/ dir + empty optio.log
    # channel are owned by the protocol driver itself.
    await host.setup_workdir()
    deliverables_dir = f"{host.workdir}/deliverables"
    await host.run_command(f"mkdir -p {deliverables_dir}")
    await host.write_text("optio.log", "")

    session_error: BaseException | None = None
    cancelled = False
    fetch_task: asyncio.Task | None = None
    tail_task: asyncio.Task | None = None
    body_task: asyncio.Task | None = None
    cancel_task: asyncio.Task | None = None

    try:
        # before_execute runs inside the try so a failure here still
        # triggers the after_execute cleanup in the outer finally.
        if before_execute is not None:
            await before_execute(hook_ctx)

        deliverable_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(
            maxsize=DELIVERABLE_QUEUE_BOUND,
        )
        done_flag = asyncio.Event()
        error_flag: list[str | None] = []  # [message] or [] if not fired

        fetch_task = asyncio.create_task(
            _deliverable_fetch_loop(host, on_deliverable, deliverable_queue, ctx, hook_ctx),
        )
        tail_task = asyncio.create_task(
            _tail_and_dispatch(host, ctx, deliverable_queue, done_flag, error_flag),
        )
        body_task = asyncio.create_task(body(host, hook_ctx))
        cancel_task = asyncio.create_task(_watch_cancellation(ctx))

        done, _pending = await asyncio.wait(
            {tail_task, body_task, cancel_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        cancelled = (
            cancel_task in done
            and not cancel_task.cancelled()
            and cancel_task.exception() is None
            and cancel_task.result() is True
        )

        if error_flag:
            raise _SessionFailed(error_flag[0] or "agent reported ERROR")

        if body_task in done and not cancelled and not done_flag.is_set():
            # Body completed without DONE — premature exit.
            exc = body_task.exception()
            if exc is not None:
                raise exc
            raise _SessionFailed("body returned before DONE was observed")

        # Drain remaining deliverables before returning.
        await deliverable_queue.join()

    except BaseException as exc:
        session_error = exc
        raise

    finally:
        active_tasks = [
            t for t in (tail_task, body_task, cancel_task, fetch_task)
            if t is not None
        ]
        for t in active_tasks:
            if not t.done():
                t.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)

        if after_execute is not None:
            try:
                await after_execute(hook_ctx)
            except BaseException as after_exc:
                if session_error is None:
                    raise
                ctx.report_progress(
                    None,
                    f"after_execute callback raised: {after_exc!r}",
                )


# --- private helpers ---------------------------------------------------


async def _tail_and_dispatch(
    host: Host,
    ctx: "ProcessContext",
    deliverable_queue: asyncio.Queue[tuple[str, str]],
    done_flag: asyncio.Event,
    error_flag: list,
) -> None:
    """Consume tail_file(optio.log), parse each line, dispatch by keyword."""
    async for line in host.tail_file(f"{host.workdir}/optio.log"):
        ev: LogEvent = parse_log_line(line)
        if isinstance(ev, StatusEvent):
            ctx.report_progress(ev.percent, ev.message)
        elif isinstance(ev, DeliverableEvent):
            try:
                absolute = validate_deliverable_path(ev.path, host.workdir)
            except ValueError:
                ctx.report_progress(
                    None, f"invalid deliverable path {ev.path!r}, skipping",
                )
                continue
            try:
                display = relativize_deliverable_path(absolute, host.workdir)
            except ValueError:
                ctx.report_progress(
                    None,
                    f"deliverable {ev.path!r}: not under deliverables/, "
                    "skipping (malfunction)",
                )
                continue
            ctx.report_progress(None, f"Deliverable: {display}")
            item = (absolute, display)
            try:
                deliverable_queue.put_nowait(item)
            except asyncio.QueueFull:
                await deliverable_queue.put(item)
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
    callback: DeliverableCallback | None,
    queue: asyncio.Queue[tuple[str, str]],
    ctx: "ProcessContext",
    hook_ctx: HookContext,
) -> None:
    """Drain the deliverable queue: fetch each file, decode UTF-8,
    invoke the consumer callback."""
    while True:
        absolute, display = await queue.get()
        try:
            try:
                text = await fetch_deliverable_text(host, absolute)
            except UnicodeDecodeError:
                ctx.report_progress(
                    None,
                    f"Deliverable {display}: not valid UTF-8, skipping callback",
                )
                continue
            except FileNotFoundError:
                ctx.report_progress(None, f"Deliverable {display}: not found")
                continue
            except Exception as exc:  # noqa: BLE001
                ctx.report_progress(
                    None,
                    f"Deliverable {display}: fetch failed: {exc!r}, skipping",
                )
                continue

            if callback is None:
                continue
            try:
                await callback(hook_ctx, display, text)
            except Exception as exc:  # noqa: BLE001
                ctx.report_progress(
                    None, f"on_deliverable callback raised: {exc!r}",
                )
        finally:
            queue.task_done()


async def _watch_cancellation(ctx: "ProcessContext") -> bool:
    """Return True when the process is cancelled."""
    while ctx.should_continue():
        await asyncio.sleep(0.1)
    return True
```

- [ ] **Step 2: Add the session surface to optio_agents/protocol/__init__.py**

Replace `packages/optio-agents/src/optio_agents/protocol/__init__.py` with the full surface (parser + session), matching the original `optio_host/protocol/__init__.py`:

```python
"""optio-agents log/deliverables coordination protocol.

Built on top of optio_host.host primitives. Knows nothing about specific
consumer task types (opencode, recipe execution, ...).
"""

from optio_agents.protocol.parser import (
    DeliverableEvent,
    DoneEvent,
    ErrorEvent,
    LogEvent,
    StatusEvent,
    UnknownLine,
    parse_log_line,
    relativize_deliverable_path,
    validate_deliverable_path,
)
from optio_agents.protocol.session import (
    DELIVERABLE_QUEUE_BOUND,
    DeliverableCallback,
    HookCallback,
    fetch_deliverable_text,
    run_log_protocol_session,
)

__all__ = [
    # parser
    "parse_log_line",
    "DeliverableEvent",
    "DoneEvent",
    "ErrorEvent",
    "LogEvent",
    "StatusEvent",
    "UnknownLine",
    "validate_deliverable_path",
    "relativize_deliverable_path",
    # session
    "run_log_protocol_session",
    "DeliverableCallback",
    "HookCallback",
    "fetch_deliverable_text",
    "DELIVERABLE_QUEUE_BOUND",
]
```

- [ ] **Step 3: Verify the optio-agents protocol subpackage imports**

Run: `/home/csillag/deai/optio/.venv/bin/python -c "from optio_agents.protocol import run_log_protocol_session, fetch_deliverable_text, DeliverableCallback, HookCallback, DELIVERABLE_QUEUE_BOUND; from optio_agents.protocol.session import _SessionFailed, _deliverable_fetch_loop; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Delete the optio_host.protocol subpackage**

Now that the parser and session live in optio-agents, the old subpackage has no remaining purpose (its only callers — opencode — are repointed in Tasks 8-10, but nothing inside optio-host imports it).

```bash
git rm packages/optio-host/src/optio_host/protocol/__init__.py packages/optio-host/src/optio_host/protocol/parser.py packages/optio-host/src/optio_host/protocol/session.py
```

- [ ] **Step 5: Verify optio-host has no dangling references to the removed modules**

Run: `cd packages/optio-host && ../../.venv/bin/python -m pytest -q`
Expected: PASS. (optio-host tests no longer reference `optio_host.protocol` or `optio_host.context`. `test_download.py` still tests the factory + curl paths — covered by Task 7's split, but the factory tests already pass unchanged here.)

Run: `grep -rn "optio_host.protocol\|optio_host\.context" packages/optio-host/src`
Expected: no output.

- [ ] **Step 6: Run the full optio-agents suite (parser + context + session-importing tests)**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest -q`
Expected: PASS — `69 passed` (33 parser + 36 context; the download test added in Task 7 not yet present).

- [ ] **Step 7: Commit**

```bash
git add packages/optio-agents/src/optio_agents/protocol/session.py packages/optio-agents/src/optio_agents/protocol/__init__.py
git commit -m "feat(optio-agents): move session driver; delete optio_host.protocol"
```

---

## Task 6: Export HookContext + protocol surface from optio_agents top level

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/__init__.py`

`optio_opencode.session` does `from optio_agents import HookContext`, and the opencode tests do `from optio_agents import HookContext` / `_deliverable_fetch_loop`. The top-level package must export the coordination surface.

- [ ] **Step 1: Write a failing top-level export test**

Append to `packages/optio-agents/tests/test_context.py` (or create `packages/optio-agents/tests/test_package_exports.py`). Create the dedicated file `packages/optio-agents/tests/test_package_exports.py`:

```python
"""optio_agents top-level package exports the coordination surface."""


def test_top_level_exports_hook_context():
    import optio_agents
    assert hasattr(optio_agents, "HookContext")
    assert hasattr(optio_agents, "HookContextProtocol")


def test_top_level_exports_protocol_surface():
    import optio_agents
    for name in (
        "run_log_protocol_session",
        "fetch_deliverable_text",
        "DeliverableCallback",
        "HookCallback",
        "parse_log_line",
    ):
        assert hasattr(optio_agents, name), name
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest tests/test_package_exports.py -q`
Expected: FAIL — `assert hasattr(optio_agents, "HookContext")` is False (placeholder `__init__.py` exports nothing).

- [ ] **Step 3: Fill in optio_agents/__init__.py**

Replace `packages/optio-agents/src/optio_agents/__init__.py`:

```python
"""optio-agents — agent-coordination layer for optio task types.

Owns the optio.log keyword protocol (parser + session driver), the
``HookContext`` passed to agent task hooks, and the single source of
truth for the LLM-facing keyword-protocol documentation.
"""

from optio_agents.context import HookContext, HookContextProtocol
from optio_agents.protocol import (
    DELIVERABLE_QUEUE_BOUND,
    DeliverableCallback,
    DeliverableEvent,
    DoneEvent,
    ErrorEvent,
    HookCallback,
    LogEvent,
    StatusEvent,
    UnknownLine,
    fetch_deliverable_text,
    parse_log_line,
    relativize_deliverable_path,
    run_log_protocol_session,
    validate_deliverable_path,
)

__all__ = [
    "HookContext",
    "HookContextProtocol",
    "run_log_protocol_session",
    "fetch_deliverable_text",
    "DeliverableCallback",
    "HookCallback",
    "DELIVERABLE_QUEUE_BOUND",
    "parse_log_line",
    "DeliverableEvent",
    "DoneEvent",
    "ErrorEvent",
    "LogEvent",
    "StatusEvent",
    "UnknownLine",
    "validate_deliverable_path",
    "relativize_deliverable_path",
]
```

- [ ] **Step 4: Run the export test to verify it passes**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest tests/test_package_exports.py -q`
Expected: PASS — `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-agents/src/optio_agents/__init__.py packages/optio-agents/tests/test_package_exports.py
git commit -m "feat(optio-agents): export HookContext + protocol surface at package top level"
```

---

## Task 7: Split the download tests (HookContext.download_file routing → optio-agents)

**Files:**
- Create: `packages/optio-agents/tests/test_download.py`
- Modify: `packages/optio-host/tests/test_download.py`

The `HookContext.download_file` routing tests (current `test_download.py` lines 58-171) test a `HookContext` method, so they move to optio-agents. The `DownloadFailed`/`create_download_task`/curl/`_parse_trace_line`/`_build_curl_cmd` tests stay in optio-host (they test `download.py`). Note `test_download_file_appears_on_hook_context_protocol` and the `HookContextProtocol`-surface check move too.

- [ ] **Step 1: Create the moved routing tests in optio-agents (failing import)**

Create `packages/optio-agents/tests/test_download.py`. These are the `HookContext.download_file` routing tests, repointed: `from optio_host.context import HookContext` → `from optio_agents.context import HookContext`; `from optio_host.context import HookContextProtocol` → `from optio_agents.context import HookContextProtocol`; the monkeypatch test that uses `from optio_host import context as ctx_mod` repoints to `from optio_agents import context as ctx_mod` (the `create_download_task` spy attribute lives on `optio_agents.context` now, since context.py imports it at module top). The full file:

```python
"""Tests for HookContext.download_file routing (optio-agents owns HookContext)."""

import pytest


class _RoutingFakeCtx:
    """Fake ProcessContext for testing HookContext.download_file routing only."""

    def __init__(self, *, process_id="p"):
        self.process_id = process_id
        self._child_counter = {"next": 0}
        self.run_child_calls = []

    async def run_child_task(self, task, **kw):
        self.run_child_calls.append(
            (task.execute, task.process_id, task.name, task.description)
        )
        self._child_counter["next"] += 1
        return "done"


class _RoutingFakeHost:
    def __init__(self, *, workdir="/wd", host_home="/home/u"):
        self.workdir = workdir
        self._host_home = host_home

    async def resolve_host_home(self):
        return self._host_home


async def test_download_file_routes_through_run_child_with_generated_id_and_name():
    from optio_agents.context import HookContext

    ctx = _RoutingFakeCtx(process_id="root.parent")
    host = _RoutingFakeHost(workdir="/wd")
    h = HookContext(ctx, host)

    await h.download_file(
        "https://example/foo.bin",
        "downloads/foo.bin",
    )

    assert len(ctx.run_child_calls) == 1
    execute, pid, name, description = ctx.run_child_calls[0]
    assert pid == "root.parent.download-0"
    assert name == "download foo.bin"
    assert description is None


async def test_download_file_second_call_increments_counter():
    from optio_agents.context import HookContext

    ctx = _RoutingFakeCtx(process_id="root.parent")
    host = _RoutingFakeHost(workdir="/wd")
    h = HookContext(ctx, host)

    await h.download_file("https://example/a.bin", "a.bin")
    await h.download_file("https://example/b.bin", "b.bin")

    assert ctx.run_child_calls[0][1] == "root.parent.download-0"
    assert ctx.run_child_calls[1][1] == "root.parent.download-1"


async def test_download_file_passes_description_through():
    from optio_agents.context import HookContext

    ctx = _RoutingFakeCtx()
    host = _RoutingFakeHost()
    h = HookContext(ctx, host)

    await h.download_file(
        "https://example/foo.bin", "foo.bin",
        description="grab it",
    )
    assert ctx.run_child_calls[0][3] == "grab it"


async def test_download_file_resolves_workdir_relative_target_to_absolute(monkeypatch):
    """The factory should receive an already-resolved absolute target path."""
    from optio_agents import context as ctx_mod

    captured: dict = {}
    original = ctx_mod.create_download_task

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(ctx_mod, "create_download_task", spy)

    ctx = _RoutingFakeCtx()
    host = _RoutingFakeHost(workdir="/wd")
    h = ctx_mod.HookContext(ctx, host)
    await h.download_file("https://example/foo.bin", "sub/foo.bin")

    assert captured["target"] == "/wd/sub/foo.bin"
    assert captured["url"] == "https://example/foo.bin"
    assert captured["host"] is host


async def test_download_file_rejects_workdir_escape_without_spawning():
    from optio_agents.context import HookContext

    ctx = _RoutingFakeCtx()
    host = _RoutingFakeHost()
    h = HookContext(ctx, host)

    with pytest.raises(ValueError):
        await h.download_file("https://example/foo", "../escape")
    assert ctx.run_child_calls == []


def test_download_file_appears_on_hook_context_protocol():
    from optio_agents.context import HookContextProtocol
    methods = {m for m in dir(HookContextProtocol) if not m.startswith("_")}
    assert "download_file" in methods
```

- [ ] **Step 2: Run the moved download tests to verify they pass**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest tests/test_download.py -q`
Expected: PASS — `6 passed`.

- [ ] **Step 3: Remove the moved routing tests from optio-host's test_download.py**

In `packages/optio-host/tests/test_download.py`, delete the block from line 58 (`import pytest`) through line 178 — i.e. the `_RoutingFakeCtx`/`_RoutingFakeHost` classes and every `test_download_file_*` function plus `test_download_file_appears_on_hook_context_protocol`. **Keep**:
- lines 1-56 (`test_download_failed_fields_and_str`, `test_create_download_task_*`),
- `test_create_download_task_and_downloadfailed_exported_from_optio_host` (lines 174-177),
- the `_parse_trace_line` / `_build_curl_cmd` tests (lines 180-237),
- the real-curl integration tests (lines 240-547).

Because `import pytest` (line 58) is consumed by the integration tests below, re-add a single `import pytest` near the top of the file if it is not otherwise present. Concretely, the kept `test_download.py` should start:

```python
"""Tests for optio_host.download — the URL → file task factory and curl execution."""

import pytest


def test_download_failed_fields_and_str():
    from optio_host.download import DownloadFailed
    err = DownloadFailed(
        url="https://example/foo.bin",
        target="/tmp/foo.bin",
        exit_code=22,
        stderr_tail="curl: (22) The requested URL returned error: 404\n",
    )
    assert err.url == "https://example/foo.bin"
    assert err.target == "/tmp/foo.bin"
    assert err.exit_code == 22
    assert "curl" in err.stderr_tail
    s = str(err)
    assert "https://example/foo.bin" in s
    assert "22" in s
    assert "404" in s


def test_create_download_task_returns_taskinstance_with_fields():
    from optio_core.models import TaskInstance
    from optio_host.download import create_download_task

    t = create_download_task(
        process_id="p.download-0",
        name="download foo.bin",
        url="https://example.com/foo.bin",
        target="/tmp/foo.bin",
        host=None,
        description="grab the binary",
    )

    assert isinstance(t, TaskInstance)
    assert t.process_id == "p.download-0"
    assert t.name == "download foo.bin"
    assert t.description == "grab the binary"
    assert t.cancellable is True
    assert t.supports_resume is False
    assert t.auto_cancel_children is True
    assert t.ui_widget is None
    assert callable(t.execute)


def test_create_download_task_defaults_description():
    from optio_host.download import create_download_task

    t = create_download_task(
        process_id="p.download-0",
        name="download foo.bin",
        url="https://example.com/foo.bin",
        target="/tmp/foo.bin",
    )
    assert t.description is None


def test_create_download_task_and_downloadfailed_exported_from_optio_host():
    import optio_host
    assert hasattr(optio_host, "create_download_task")
    assert hasattr(optio_host, "DownloadFailed")


def test_parse_trace_line_content_length():
    from optio_host.download import _parse_trace_line
    out = _parse_trace_line(b"0000: content-length: 1048576\r\n")
    assert out == ("length", 1048576)
```

…with the rest of the file from `test_parse_trace_line_recv_data` (original line 186) onward kept exactly as-is. (You may edit in place rather than rewrite — delete lines 58-173 and 168-172's `test_download_file_appears_on_hook_context_protocol`, leaving everything else. The snippet above shows the resulting head.)

- [ ] **Step 4: Run optio-host's download tests to verify they still pass**

Run: `cd packages/optio-host && ../../.venv/bin/python -m pytest tests/test_download.py -q`
Expected: PASS. The routing tests are gone; the factory + curl integration tests pass (the integration tests touch the network loopback — they should already be green from the pre-refactor baseline).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-agents/tests/test_download.py packages/optio-host/tests/test_download.py
git commit -m "test(optio-agents): move HookContext.download_file routing tests from optio-host"
```

---

## Task 8: Repoint optio-opencode source imports

**Files:**
- Modify: `packages/optio-opencode/pyproject.toml:28-34`
- Modify: `packages/optio-opencode/src/optio_opencode/types.py:13`
- Modify: `packages/optio-opencode/src/optio_opencode/session.py:32,35`
- Modify: `packages/optio-opencode/src/optio_opencode/__init__.py:5-11,33-34`

opencode now consumes `HookContext`/protocol from optio-agents. `RunResult`/`HostCommandError`/`SSHConfig` still come from optio-host.

- [ ] **Step 1: Add optio-agents to opencode dependencies**

In `packages/optio-opencode/pyproject.toml`, add `optio-agents` to the `dependencies` list (after `optio-host`):

```toml
dependencies = [
    "optio-core>=0.1,<0.2",
    # 0.1.1 introduces the bind_addr kwarg on Host.establish_tunnel —
    # required for OPTIO_WIDGET_TUNNEL_BIND to work.
    "optio-host>=0.1,<0.2",
    "optio-agents>=0.1,<0.2",
    "asyncssh>=2.14",
]
```

- [ ] **Step 2: Repoint types.py**

In `packages/optio-opencode/src/optio_opencode/types.py`, change line 13:

```python
from optio_host.protocol.session import DeliverableCallback, HookCallback
```

to:

```python
from optio_agents.protocol.session import DeliverableCallback, HookCallback
```

Also update the module docstring's claim about ownership (lines 3-5 say optio-host owns these). Replace the docstring paragraph:

```python
"""Public data types for optio-opencode consumers.

The generic ``DeliverableCallback`` / ``HookCallback`` types are owned by
``optio-agents`` (they describe the log/deliverables protocol); ``SSHConfig``
is owned by ``optio-host``. This module re-exports them so existing
``from optio_opencode.types import ...`` imports keep working unchanged.
"""
```

- [ ] **Step 3: Repoint session.py**

In `packages/optio-opencode/src/optio_opencode/session.py`, change line 32:

```python
from optio_host.context import HookContext
```

to:

```python
from optio_agents import HookContext
```

and line 35:

```python
from optio_host.protocol.session import _SessionFailed, run_log_protocol_session
```

to:

```python
from optio_agents.protocol.session import _SessionFailed, run_log_protocol_session
```

(The `from optio_host.host import Host, LocalHost, ProcessHandle, RemoteHost` and `from optio_host.paths import task_dir` imports are unchanged.)

- [ ] **Step 4: Repoint __init__.py**

In `packages/optio-opencode/src/optio_opencode/__init__.py`, the top import block currently pulls `HookContext`/`HookContextProtocol` from `optio_host`. Split it so those two come from `optio_agents`:

```python
from optio_agents import HookContext, HookContextProtocol
from optio_host import (
    HostCommandError,
    RunResult,
    SSHConfig,
)
```

`__all__` is unchanged (still lists `HookContext`, `HookContextProtocol`, `HostCommandError`, `RunResult`, `SSHConfig`, etc.).

- [ ] **Step 5: Reinstall opencode editable (its dep set changed)**

Run: `/home/csillag/deai/optio/.venv/bin/pip install -e packages/optio-opencode`
Expected: ends with `Successfully installed` (or requirements already satisfied).

- [ ] **Step 6: Verify opencode imports resolve**

Run: `/home/csillag/deai/optio/.venv/bin/python -c "import optio_opencode; from optio_opencode import HookContext, HookContextProtocol, RunResult, HostCommandError, DeliverableCallback, HookCallback; print('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add packages/optio-opencode/pyproject.toml packages/optio-opencode/src/optio_opencode/types.py packages/optio-opencode/src/optio_opencode/session.py packages/optio-opencode/src/optio_opencode/__init__.py
git commit -m "refactor(optio-opencode): consume HookContext + protocol from optio-agents"
```

---

## Task 9: Repoint optio-opencode test imports

**Files:**
- Modify: `packages/optio-opencode/tests/test_smart_install.py` (RunResult → optio_host.host; HookContext → optio_agents)
- Modify: `packages/optio-opencode/tests/test_session_hooks.py` (RunResult → optio_host.host; HookContext, _deliverable_fetch_loop → optio_agents)
- Modify: `packages/optio-opencode/tests/test_host_local.py` (fetch_deliverable_text → optio_agents.protocol.session)

- [ ] **Step 1: Repoint test_smart_install.py**

In `packages/optio-opencode/tests/test_smart_install.py`:
- Line 5: change `from optio_host.context import RunResult` to `from optio_host.host import RunResult`.
- Every `from optio_host.context import HookContext` (lines 173, 215, 267, 298, 327, 397, 442) → `from optio_agents import HookContext`.

Run a sed-style verification after editing — there must be no remaining `optio_host.context` reference in this file.

- [ ] **Step 2: Repoint test_session_hooks.py**

In `packages/optio-opencode/tests/test_session_hooks.py`:
- Line 100: change `from optio_host.context import RunResult` to `from optio_host.host import RunResult`.
- Line 258: change `from optio_host.protocol.session import _deliverable_fetch_loop` to `from optio_agents.protocol.session import _deliverable_fetch_loop`.
- Line 259: change `from optio_host.context import HookContext` to `from optio_agents import HookContext`.

- [ ] **Step 3: Repoint test_host_local.py**

In `packages/optio-opencode/tests/test_host_local.py`, both occurrences (lines 139 and 149):

```python
from optio_host.protocol.session import fetch_deliverable_text
```

→

```python
from optio_agents.protocol.session import fetch_deliverable_text
```

- [ ] **Step 4: Verify no opencode test still references the old paths**

Run: `grep -rn "optio_host.protocol\|optio_host\.context" packages/optio-opencode`
Expected: no output.

- [ ] **Step 5: Run the three repointed opencode test files**

Run: `cd packages/optio-opencode && ../../.venv/bin/python -m pytest tests/test_smart_install.py tests/test_session_hooks.py tests/test_host_local.py -q`
Expected: PASS (all collected tests pass).

- [ ] **Step 6: Commit**

```bash
git add packages/optio-opencode/tests/test_smart_install.py packages/optio-opencode/tests/test_session_hooks.py packages/optio-opencode/tests/test_host_local.py
git commit -m "test(optio-opencode): repoint HookContext/protocol imports to optio-agents"
```

---

## Task 10: SSOT prompt block in optio-agents

**Files:**
- Create: `packages/optio-agents/src/optio_agents/protocol/prompt.py`
- Create: `packages/optio-agents/tests/test_prompt.py`

Define the canonical LLM-facing keyword block (the "## Log channel" + "## Deliverables" sections documenting the existing `STATUS:`/`DELIVERABLE:`/`DONE`/`ERROR` keywords, the trailing-newline requirement, and the deliverables convention). Expose it as a constant `LOG_CHANNEL_PROMPT`. The text is moved from opencode's `BASE_PROMPT_PRE` (lines 14-46), made framing-neutral (drop the opencode-specific title line, which the consumer re-adds in Task 11).

- [ ] **Step 1: Write a failing test for the SSOT block**

Create `packages/optio-agents/tests/test_prompt.py`:

```python
"""Tests for the SSOT LLM-facing keyword-protocol prompt block."""

from optio_agents.protocol.prompt import LOG_CHANNEL_PROMPT


def test_block_documents_all_four_keywords():
    for kw in ("STATUS:", "DELIVERABLE:", "DONE", "ERROR"):
        assert kw in LOG_CHANNEL_PROMPT


def test_block_mentions_log_and_deliverables_paths():
    assert "./optio.log" in LOG_CHANNEL_PROMPT
    assert "./deliverables/" in LOG_CHANNEL_PROMPT


def test_block_states_trailing_newline_requirement():
    # The newline rule is load-bearing — the tailer buffers lines without it.
    assert "newline" in LOG_CHANNEL_PROMPT
    assert "tail -c 1" in LOG_CHANNEL_PROMPT


def test_block_has_log_channel_and_deliverables_sections():
    assert "## Log channel" in LOG_CHANNEL_PROMPT
    assert "## Deliverables" in LOG_CHANNEL_PROMPT


def test_block_is_framing_neutral():
    # The SSOT block must NOT carry opencode-specific framing — that's the
    # consumer's job to wrap around it.
    assert "optio-opencode" not in LOG_CHANNEL_PROMPT
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest tests/test_prompt.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'optio_agents.protocol.prompt'`.

- [ ] **Step 3: Create prompt.py**

Create `packages/optio-agents/src/optio_agents/protocol/prompt.py`:

```python
"""Single source of truth for the LLM-facing keyword-protocol documentation.

This module lives next to ``parser.py`` so the prose that teaches an agent
how to speak the protocol cannot drift from the regexes that enforce it.
Consumers (e.g. optio-opencode) compose ``LOG_CHANNEL_PROMPT`` into their
own AGENTS.md framing.

Documents the keywords parsed by ``optio_agents.protocol.parser``:
``STATUS:`` / ``DELIVERABLE:`` / ``DONE`` / ``ERROR``.
"""


LOG_CHANNEL_PROMPT = """## Log channel

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

**Every entry must end with a newline character (`\\n`).** The host
reads `optio.log` with a line-oriented tailer that only emits a line
once it sees `\\n`; an entry written without a trailing newline (e.g.
via `printf 'DONE'`) will be buffered indefinitely and never reach the
host. Use `echo`, `>>` redirection of a heredoc, or any other mechanism
that guarantees a trailing newline. If unsure, double-check with
`tail -c 1 ./optio.log` — the result must be a newline.

After writing `DONE` or `ERROR`, the session will terminate. Do not
write further lines.

## Deliverables

Place files you want to hand back to the host under `./deliverables/`.
For each file, write a `DELIVERABLE:` log line *after* the file exists
and its contents are final. The host fetches files by reading these
log lines.
"""
```

- [ ] **Step 4: Run the prompt test to verify it passes**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest tests/test_prompt.py -q`
Expected: PASS — `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-agents/src/optio_agents/protocol/prompt.py packages/optio-agents/tests/test_prompt.py
git commit -m "feat(optio-agents): add SSOT LLM-facing keyword-protocol prompt block"
```

---

## Task 11: Rewrite opencode BASE_PROMPT_PRE to compose the SSOT block

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/prompt.py:10-46`
- Test: `packages/optio-opencode/tests/test_prompt.py` (existing, unchanged — proves equivalence)

`BASE_PROMPT_PRE` is rewritten to compose `LOG_CHANNEL_PROMPT` from optio-agents while keeping opencode-specific framing (the title + intro paragraph). The composed AGENTS.md must still contain all four keywords and both path references — guarded by the existing `test_prompt.py`.

- [ ] **Step 1: Confirm the existing opencode prompt tests pass before the rewrite**

Run: `cd packages/optio-opencode && ../../.venv/bin/python -m pytest tests/test_prompt.py -q`
Expected: PASS (these are the equivalence guards: keywords present, `./optio.log` + `./deliverables/` present, `## Task`, resume-section ordering, etc.).

- [ ] **Step 2: Rewrite BASE_PROMPT_PRE to compose the SSOT block**

In `packages/optio-opencode/src/optio_opencode/prompt.py`, replace the `BASE_PROMPT_PRE` string literal (lines 10-46) with a composed value. Add an import at the top of the file and define `BASE_PROMPT_PRE` by wrapping the SSOT block with opencode framing:

```python
from optio_agents.protocol.prompt import LOG_CHANNEL_PROMPT


_OPENCODE_INTRO = """# Coordination protocol with the host (optio-opencode)

You are running inside a coordination harness. Follow these conventions
throughout the session.

"""


BASE_PROMPT_PRE = _OPENCODE_INTRO + LOG_CHANNEL_PROMPT
```

Leave `BASE_PROMPT_POST`, `RESUME_SECTION_TEMPLATE`, `_render_resume_section`, and `compose_agents_md` unchanged. Note `compose_agents_md` builds the body as `f"{BASE_PROMPT_PRE}\n{resume_block}{BASE_PROMPT_POST}\n{body}\n"` — `LOG_CHANNEL_PROMPT` ends with a trailing `\n` (its closing `"""` is on its own line), so the existing single `\n` join still produces the same blank-line separation as the original literal (which ended `log lines.\n`). The ordering test `test_compose_agents_md_resume_section_between_deliverables_and_task` requires `## Deliverables` (now from the SSOT block) to appear before `## Resumes` — preserved because `BASE_PROMPT_PRE` (containing `## Deliverables`) precedes `resume_block` in the f-string.

- [ ] **Step 3: Run the opencode prompt tests to verify semantic equivalence**

Run: `cd packages/optio-opencode && ../../.venv/bin/python -m pytest tests/test_prompt.py -q`
Expected: PASS — all 12 tests, including `test_base_prompt_contains_all_keywords`, `test_base_prompt_mentions_log_and_deliverables_paths`, `test_compose_agents_md_resume_section_between_deliverables_and_task`.

- [ ] **Step 4: Spot-check the composed AGENTS.md contains the keyword section**

Run: `cd packages/optio-opencode && ../../.venv/bin/python -c "from optio_opencode.prompt import compose_agents_md; out = compose_agents_md('do X', workdir_exclude=None); assert '## Log channel' in out and 'STATUS:' in out and './optio.log' in out; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/prompt.py
git commit -m "refactor(optio-opencode): compose keyword-protocol section from optio-agents SSOT"
```

---

## Task 12: Wire optio-agents into the Makefile install loop

**Files:**
- Modify: `Makefile:4`

`make install` iterates `PY_PACKAGES` in order; optio-agents must sit after optio-host (its dependency) and before optio-opencode (its consumer).

- [ ] **Step 1: Add optio-agents to PY_PACKAGES**

In `Makefile`, change line 4:

```makefile
PY_PACKAGES := optio-core optio-host optio-opencode
```

to:

```makefile
PY_PACKAGES := optio-core optio-host optio-agents optio-opencode
```

- [ ] **Step 2: Verify install ordering by reinstalling the whole set**

Run: `make install`
Expected: editable-installs each package in order; ends without error. (Confirms optio-agents installs after optio-host and before optio-opencode.)

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "build: add optio-agents to PY_PACKAGES (after optio-host, before optio-opencode)"
```

---

## Task 13: README updates

**Files:**
- Create: `packages/optio-agents/README.md`
- Modify: `packages/optio-host/README.md`
- Modify: `packages/optio-host/pyproject.toml:8`

optio-host README drops the "+ log/deliverables protocol" framing and points to optio-agents; optio-agents README describes the coordination protocol + keyword SSOT.

- [ ] **Step 1: Write the optio-agents README**

Create `packages/optio-agents/README.md`:

```markdown
# optio-agents

The agent-coordination layer for [optio](https://github.com/deai-network/optio) task types.

`optio-agents` owns the **log/deliverables keyword protocol** that long-running on-host agents use to talk back to optio, the **session driver** that parses and dispatches it, the **`HookContext`** handle passed to agent task hooks, and the **single source of truth** for the LLM-facing keyword documentation.

## What's in the box

- **`optio_agents.protocol`** — a line-oriented session driver. A long-running agent on the host writes lines prefixed `STATUS:`, `DELIVERABLE:`, `DONE`, or `ERROR` to `./optio.log`. `run_log_protocol_session` tails the log, dispatches progress events, fetches deliverable files, and resolves the session on `DONE` / `ERROR`.
- **`optio_agents.protocol.parser`** — the keyword parser (`parse_log_line`, the typed `*Event` dataclasses, deliverable-path validation).
- **`optio_agents.protocol.prompt`** — `LOG_CHANNEL_PROMPT`, the canonical LLM-facing documentation of the keywords, co-located with the parser regexes it documents so the two cannot drift. Consumers compose it into their own agent-facing prompt.
- **`HookContext` / `HookContextProtocol`** — the handle passed into task hooks and `on_deliverable` callbacks, wrapping a `ProcessContext` plus host primitives (`run_on_host`, `copy_file`, `read_from_host`, `download_file`).

## Dependency direction

`optio-agents` depends on `optio-host` (host transport: running commands, file transfer, tunnels) and `optio-core`. It is consumed by agent task packages such as `optio-opencode`.

## Installation

```bash
pip install optio-agents
```

Python 3.11+.

## License

Apache-2.0.
```

- [ ] **Step 2: Reword the optio-host README**

In `packages/optio-host/README.md`:
- Line 1-3 header: replace the tagline. Change line 3 from
  `Local-or-remote host abstraction plus the log/deliverables coordination protocol used by optio task types.`
  to
  `Local-or-remote host abstraction for optio task types. The agent-coordination protocol that used to live here now lives in [optio-agents](../optio-agents).`
- In the intro paragraph (line 5), drop the "It also provides a small line-based protocol…" sentence.
- In "What's in the box", remove the `**HookContext**` bullet and the `**optio_host.protocol**` bullet (both moved to optio-agents); add a short note: `- For the log/deliverables coordination protocol, the keyword parser, and **HookContext**, see **[optio-agents](../optio-agents)**.`
- In "When to use it", change the "a structured way for the running process to talk back to optio (progress + deliverables)" bullet to point at optio-agents instead.

The resulting "What's in the box" section:

```markdown
## What's in the box

- **`Host` Protocol + `LocalHost` / `RemoteHost` / `make_host()`** — uniform interface for running commands, opening port forwards, transferring files, and tearing down workdirs. SSH details (auth, multiplexing, channel cleanup) are hidden behind `asyncssh`.
- **`RunResult` / `HostCommandError`** — the result and error types produced by `Host.run_command`.
- **`create_download_task(...)`** — a ready-made optio task that downloads a file from a remote host with progress reporting and integrity checks.
- For the log/deliverables coordination protocol, the keyword parser, and **HookContext**, see **[optio-agents](../optio-agents)**.
```

- [ ] **Step 3: Reword the optio-host pyproject description**

In `packages/optio-host/pyproject.toml`, change line 8:

```toml
description = "Local-or-remote host abstraction + log/deliverables coordination protocol for optio task types."
```

to:

```toml
description = "Local-or-remote host abstraction for optio task types (run commands local or remote, transfer files, tunnels)."
```

- [ ] **Step 4: Commit**

```bash
git add packages/optio-agents/README.md packages/optio-host/README.md packages/optio-host/pyproject.toml
git commit -m "docs: add optio-agents README; drop protocol framing from optio-host"
```

---

## Task 14: Full-suite acceptance verification

**Files:** none (verification only)

Behavior-unchanged refactor — acceptance is the full suite green from a clean editable reinstall, plus grep-clean of the old import paths.

- [ ] **Step 1: Clean editable reinstall of the full package set**

Run: `make install`
Expected: installs optio-core, optio-host, optio-agents, optio-opencode in order, no error.

- [ ] **Step 2: Run optio-host tests**

Run: `cd packages/optio-host && ../../.venv/bin/python -m pytest -q`
Expected: PASS (no `test_context.py` / `test_protocol_parser.py`; `test_download.py` keeps factory + curl tests).

- [ ] **Step 3: Run optio-agents tests**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest -q`
Expected: PASS — parser (33) + context (36) + download routing (6) + package exports (2) + prompt (5).

- [ ] **Step 4: Run optio-opencode tests**

Run: `cd packages/optio-opencode && ../../.venv/bin/python -m pytest -q`
Expected: PASS (all tests, against the repointed imports).

- [ ] **Step 5: Grep for residual old import paths (must be clean)**

Run: `grep -rn "optio_host\.protocol\|optio_host\.context" packages/ docs/ 2>/dev/null | grep -v "docs/2026-05-29-optio-agents-extraction"`
Expected: no output. (Excludes the design/plan docs that describe the move.)

- [ ] **Step 6: Confirm no leftover optio_host context/protocol source files**

Run: `ls packages/optio-host/src/optio_host/context.py packages/optio-host/src/optio_host/protocol 2>&1`
Expected: both report "No such file or directory".

- [ ] **Step 7: Confirm the composed AGENTS.md still carries the keyword section**

Run: `cd packages/optio-opencode && ../../.venv/bin/python -c "from optio_opencode.prompt import compose_agents_md; out = compose_agents_md('task', workdir_exclude=None); assert all(k in out for k in ('## Log channel','STATUS:','DELIVERABLE:','DONE','ERROR','./optio.log','./deliverables/')); print('AGENTS.md keyword section OK')"`
Expected: `AGENTS.md keyword section OK`

- [ ] **Step 8: Final commit (if any verification fixups were needed)**

Only if Steps 1-7 surfaced a fixup. Otherwise nothing to commit — the refactor is complete and verified.

---

## Self-Review

**Spec coverage:**
- "The boundary" — optio-agents gets parser.py (Task 3), session.py (Task 5), context.py (Task 4), prompt.py (Task 10). ✓
- `RunResult`/`HostCommandError` stay in optio-host, relocated into `host.py`; `context.py` deleted — Task 1 + Task 4. ✓
- Dependency direction `optio-agents → {optio-host, optio-core}` — pyproject in Task 2; session.py imports `optio_host.host.Host`, parser is pure stdlib. ✓
- "Symbols moved" — parser symbols (Task 3), session symbols incl. `_SessionFailed` (Task 5), `HookContext`/`HookContextProtocol` (Task 4); `optio_agents/protocol/__init__.py` + `optio_agents/__init__.py` re-export (Tasks 5-6). ✓
- "Call sites to repoint" table — types.py, session.py, opencode __init__.py, optio-host __init__.py, opencode pyproject, Makefile — Tasks 1, 8, 12. ✓
- "Tests to move / repoint" — test_protocol_parser.py (Task 3), test_context.py (Task 4), split test_download.py (Task 7), opencode test repoints (Task 9). ✓
- "SSOT prompt consolidation" — Task 10 (block) + Task 11 (opencode composes it). ✓
- "Packaging" — pyproject (Task 2), Makefile (Task 12), READMEs (Task 13). ✓
- "Testing / acceptance" — Task 14 (make install + per-package pytest + grep clean + AGENTS.md check). ✓

**Placeholder scan:** No TBD/TODO/"add error handling"/"similar to Task N" — every code step shows the full content. ✓

**Type consistency:** `LOG_CHANNEL_PROMPT` (Task 10) is the exact name imported in Task 11. `_SessionFailed`, `_deliverable_fetch_loop`, `fetch_deliverable_text`, `run_log_protocol_session`, `DeliverableCallback`, `HookCallback`, `DELIVERABLE_QUEUE_BOUND` names match across session.py (Task 5), protocol __init__ (Task 5), top-level __init__ (Task 6), and opencode repoints (Tasks 8-9). `HookContext`/`HookContextProtocol` consistent across Tasks 4, 6, 8, 9. `RunResult`/`HostCommandError` move target (`optio_host.host`) consistent across Tasks 1, 9. ✓
