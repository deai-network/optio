# optio-claudecode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `optio-claudecode`, a sibling Python package to `optio-opencode` that runs Anthropic's Claude Code CLI as an optio task and exposes the interactive TUI inside the optio dashboard via a `ttyd`-served iframe widget. The package mirrors opencode's public-API shape and reuses the `optio.log` keyword contract verbatim so consumer instructions are interchangeable between the two packages.

**Architecture:** A new Python package under `packages/optio-claudecode/` with one public factory (`create_claudecode_task`) and one config dataclass (`ClaudeCodeTaskConfig`). The task launches `ttyd -W ... -- env HOME=<workdir>/home bash -c 'cd <workdir> && exec <claude>'`. Each task has a private HOME inside its workdir, so the host user's real `~/.claude/` is never touched. Claude binary auto-install uses Anthropic's vendor `https://claude.ai/install.sh`. ttyd is auto-installed from `tsl0922/ttyd` GitHub Releases. The optio coordination prompt (`AGENTS.md`) is shared with `optio-opencode` via a new `optio_host.agents` module — a precursor refactor that lands first.

**Tech Stack:** Python 3.11+, `optio-host` (existing), `optio-core` (existing), `asyncssh`, `pytest`, `pytest-asyncio`, MongoDB-via-Docker for integration tests, `ttyd` (auto-installed; required at runtime), `claude` (auto-installed; required at runtime).

**Spec:** `docs/2026-05-28-optio-claudecode-design.md` (commit `8fb6438`).

**Base revision:** `9712ae281d29c9401d2e8c1a06abcc47695a9843` on branch `main` (from the spec).

**Branching strategy (per repo convention):** Create a single feature branch `feat/optio-claudecode` off `main`. Work happens in-place on that branch. Do not use a worktree. The plan deliberately groups the precursor `optio_host.agents` refactor into the same branch and PR; if reviewers want a split PR, the commit boundaries below make that easy after the fact.

---

## Phase 0 — Branch creation + precursor refactor (`optio_host.agents`)

### Task 0a: Create the feature branch

**Files:**
- (none — git only)

- [ ] **Step 1: Confirm clean working tree**

```bash
git status
```
Expected: `On branch main`, `nothing to commit, working tree clean` (or only untracked test scratch files outside the repo).

- [ ] **Step 2: Create and switch to feature branch**

```bash
git checkout -b feat/optio-claudecode
```
Expected: `Switched to a new branch 'feat/optio-claudecode'`.

- [ ] **Step 3: Verify the branch starts at the spec's base revision**

```bash
git rev-parse HEAD
```
Expected: `9712ae281d29c9401d2e8c1a06abcc47695a9843` OR a later main commit (if main has advanced; both are acceptable — drift handling is for the merge-back step, not the start).

---

### Task 0b: Move the shared optio.log/AGENTS.md prompt into `optio_host.agents`

The opencode prompt module currently owns the optio.log + deliverables coordination text. Claudecode needs the exact same text. Move the shared parts to `optio-host` and keep the resume-only bits opencode-private.

**Files:**
- Create: `packages/optio-host/src/optio_host/agents.py`
- Test: `packages/optio-host/tests/test_agents.py`

- [ ] **Step 1: Write the failing test**

Create `packages/optio-host/tests/test_agents.py`:
```python
"""Tests for the shared optio.log/AGENTS.md prompt composer."""

from optio_host.agents import (
    BASE_PROMPT_PRE,
    BASE_PROMPT_POST,
    compose_agents_md,
)


def test_base_prompt_pre_contains_log_keywords():
    assert "STATUS:" in BASE_PROMPT_PRE
    assert "DELIVERABLE:" in BASE_PROMPT_PRE
    assert "DONE" in BASE_PROMPT_PRE
    assert "ERROR" in BASE_PROMPT_PRE


def test_base_prompt_pre_documents_deliverables_dir():
    assert "./deliverables/" in BASE_PROMPT_PRE


def test_compose_with_no_resume_section():
    body = compose_agents_md("My consumer instructions")
    assert "My consumer instructions" in body
    assert "STATUS:" in body
    # No resume content when resume_section=None
    assert "resume.log" not in body
    assert "Resumes" not in body


def test_compose_with_resume_section():
    body = compose_agents_md(
        "Task body",
        resume_section="## Custom resume block\n\nDoes things.",
    )
    assert "Task body" in body
    assert "## Custom resume block" in body
    assert "Does things." in body
    # Resume section appears between PRE and POST
    assert body.index("## Custom resume block") < body.index("## Task")


def test_consumer_instructions_appended_verbatim():
    body = compose_agents_md("  Trailing whitespace   \n\n")
    # Trailing whitespace is stripped; body otherwise verbatim
    assert "Trailing whitespace" in body
    assert "Trailing whitespace   \n\n\n" not in body
```

- [ ] **Step 2: Run test to verify failure**

```bash
cd /home/csillag/deai/optio
python -m pytest packages/optio-host/tests/test_agents.py -v
```
Expected: `ModuleNotFoundError: No module named 'optio_host.agents'` (or `ImportError`).

- [ ] **Step 3: Create the `optio_host.agents` module**

Write `packages/optio-host/src/optio_host/agents.py`:
```python
"""Shared optio coordination prompt for log/deliverables-protocol agents.

Owned by ``optio-host`` so that ``optio-opencode`` and
``optio-claudecode`` (and any future agent package) compose the same
AGENTS.md base text from the same single source of truth. Consumer
packages stay responsible for their own resume-specific content (if any)
and pass it in via the ``resume_section`` parameter.
"""


BASE_PROMPT_PRE = """# Coordination protocol with the host (optio)

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


BASE_PROMPT_POST = """## Task

Here comes the description of your actual task to complete. Throughout
the task, you are encouraged to narrate progress — both on the normal
UI and in parallel using the `STATUS:` messages explained above — and
you are free to ask questions and dialogue with the human. They are
also working on the same task and will cooperate with you on achieving
the same goals. So:
"""


def compose_agents_md(
    consumer_instructions: str,
    *,
    resume_section: str | None = None,
) -> str:
    """Build the AGENTS.md body for an optio-coordinated agent task.

    Args:
      consumer_instructions: the task author's prompt, appended verbatim
        (trailing whitespace stripped).
      resume_section: optional pre-rendered resume-detection section to
        insert between ``BASE_PROMPT_PRE`` and ``BASE_PROMPT_POST``.
        ``None`` (default) omits the section, which is what packages
        that don't support resume should pass.
    """
    body = consumer_instructions.rstrip()
    resume_block = (resume_section + "\n") if resume_section else ""
    return f"{BASE_PROMPT_PRE}\n{resume_block}{BASE_PROMPT_POST}\n{body}\n"
```

- [ ] **Step 4: Run test to verify pass**

```bash
python -m pytest packages/optio-host/tests/test_agents.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Export from `optio_host.__init__`**

Edit `packages/optio-host/src/optio_host/__init__.py`. Append `compose_agents_md` (and the prompt constants for power users) to the existing imports + `__all__`:

```python
# (add to existing imports near the top, after the other from-imports)
from optio_host.agents import (
    BASE_PROMPT_PRE,
    BASE_PROMPT_POST,
    compose_agents_md,
)

# (add to __all__ list at end of file)
    "BASE_PROMPT_PRE",
    "BASE_PROMPT_POST",
    "compose_agents_md",
```

- [ ] **Step 6: Verify top-level import works**

```bash
python -c "from optio_host import compose_agents_md, BASE_PROMPT_PRE; print(len(BASE_PROMPT_PRE))"
```
Expected: a positive integer (length of the prompt body), no exception.

- [ ] **Step 7: Commit**

```bash
git add packages/optio-host/src/optio_host/agents.py \
        packages/optio-host/src/optio_host/__init__.py \
        packages/optio-host/tests/test_agents.py
git commit -m "feat(optio-host): add shared agents.md prompt composer

Moves the optio.log/deliverables coordination prompt out of
optio-opencode into optio-host so it can be shared between
optio-opencode and the new optio-claudecode package. The composer takes
a pre-rendered resume_section so resume-specific text stays in the
consumer package."
```

---

### Task 0c: Switch `optio-opencode` to consume `optio_host.agents`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/prompt.py`

- [ ] **Step 1: Read the current opencode prompt module**

```bash
wc -l packages/optio-opencode/src/optio_opencode/prompt.py
```
Note the length (≈166 lines) so the diff can be reviewed for size.

- [ ] **Step 2: Rewrite `prompt.py` to delegate**

Replace the contents of `packages/optio-opencode/src/optio_opencode/prompt.py` with:

```python
"""System-prompt composition for optio-opencode.

The base optio.log/deliverables text now lives in ``optio_host.agents``.
This module keeps only the opencode-specific resume section and a thin
wrapper that renders it and forwards to the shared composer.
"""

from optio_host.agents import (
    BASE_PROMPT_PRE,
    BASE_PROMPT_POST,
    compose_agents_md as _compose_agents_md_host,
)


# Re-export so existing `from optio_opencode.prompt import BASE_PROMPT_PRE`
# call sites keep working.
__all__ = ["BASE_PROMPT_PRE", "BASE_PROMPT_POST", "compose_agents_md"]


RESUME_SECTION_TEMPLATE = """## Resumes

This harness may pause your session, save your context to a database,
terminate the underlying process, and later rehydrate it. From your
point of view the conversation is fully continuous — you keep your
prior context and will not "notice" the resume.

**A resume can happen at any point, not only at the start.** The host
environment may have changed across a resume — different host,
different running processes, files outside this workdir gone — even
though your context remembers everything as alive and well.

**The workdir (this directory) is preserved across resumes, with two
caveats:**

- {excludes_clause}
- **Anything outside the workdir is not preserved.**

{outside_clause}

### Detecting a resume: `resume.log`

Each session start (fresh or resumed) appends one line to
`./resume.log`. Line format:

```
<ISO 8601 UTC timestamp>[ REFRESHED:<comma-separated filenames>]
```

The very first line is the original launch timestamp; each subsequent
line is a resume. The optional `REFRESHED:` suffix signals that the
harness rewrote the listed files on that resume (e.g.
`2026-05-28T13:15:42Z REFRESHED:AGENTS.md`) — your in-memory copy of
those files is stale and must be re-read before continuing.

**At the start of every new incoming user message, read
`./resume.log` first.** Compare the latest line to the value you
remembered last time you checked. If a new line has appeared, treat
the situation as a resume:

- Verify any tools, processes, or files you previously gathered
  outside the workdir are still where you left them.
- Re-establish anything that's gone (re-launch a server, re-fetch a
  file, etc.) before continuing.
- **If the latest line carries a `REFRESHED:` suffix, re-read each
  listed file** (e.g. `cat ./AGENTS.md`) — the harness updated it
  since your last context snapshot and the version you remember is
  out of date.
- Then resume the work you were doing.

If a resume slips past unnoticed, a failing tool call is the
next-best signal — re-check `./resume.log` then.
"""


def _render_resume_section(workdir_exclude: list[str] | None) -> str:
    """Render the RESUME_SECTION_TEMPLATE with the effective exclude list."""
    from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES
    effective = workdir_exclude if workdir_exclude is not None else DEFAULT_WORKDIR_EXCLUDES
    if not effective:
        excludes_clause = (
            "**No paths are excluded** — every file in the workdir is preserved."
        )
        outside_clause = (
            "If you need to stash large data, place it outside the workdir "
            "(e.g. `/tmp/`) — but remember it may be missing when you next look."
        )
    else:
        excludes_str = ", ".join(f"`{p}`" for p in effective)
        excludes_clause = (
            f"**Paths matching the snapshot exclude list are NOT preserved**, "
            f"even inside the workdir. The current exclude list is: {excludes_str}."
        )
        outside_clause = (
            "If you need to stash large data, place it outside the workdir "
            "(e.g. `/tmp/`) or inside an excluded subdirectory — but remember "
            "any such location may be missing when you next look."
        )
    return RESUME_SECTION_TEMPLATE.format(
        excludes_clause=excludes_clause,
        outside_clause=outside_clause,
    )


def compose_agents_md(
    consumer_instructions: str,
    *,
    workdir_exclude: list[str] | None,
    supports_resume: bool = True,
) -> str:
    """Build the full AGENTS.md body for an opencode task.

    Backwards-compatible wrapper around
    ``optio_host.agents.compose_agents_md``. Renders the
    opencode-specific resume section (when ``supports_resume`` is True)
    and forwards everything else.
    """
    resume_section = _render_resume_section(workdir_exclude) if supports_resume else None
    return _compose_agents_md_host(
        consumer_instructions, resume_section=resume_section,
    )
```

- [ ] **Step 3: Run the opencode test suite**

```bash
python -m pytest packages/optio-opencode/tests/test_prompt.py -v
```
Expected: all tests pass with no behavioural change.

- [ ] **Step 4: Run the full opencode suite for safety**

```bash
python -m pytest packages/optio-opencode/tests/ -x -q --no-header 2>&1 | tail -30
```
Expected: same pass/fail count as before this change. If a test that previously passed now fails, the refactor changed observable behavior — investigate and fix in this task before moving on.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/prompt.py
git commit -m "refactor(optio-opencode): delegate base prompt to optio_host.agents

prompt.py now imports BASE_PROMPT_PRE/POST and the shared composer from
optio_host.agents. RESUME_SECTION_TEMPLATE and resume-render logic stay
in opencode. Backwards-compatible: the public compose_agents_md
signature is unchanged."
```

---

## Phase 1 — `optio-claudecode` package skeleton

### Task 1: Create the package layout

**Files:**
- Create: `packages/optio-claudecode/pyproject.toml`
- Create: `packages/optio-claudecode/README.md` (placeholder; expanded in Task 19)
- Create: `packages/optio-claudecode/src/optio_claudecode/__init__.py`
- Create: `packages/optio-claudecode/tests/__init__.py` (empty)

- [ ] **Step 1: Create the directory structure**

```bash
mkdir -p packages/optio-claudecode/src/optio_claudecode
mkdir -p packages/optio-claudecode/tests
```

- [ ] **Step 2: Write `pyproject.toml`**

Create `packages/optio-claudecode/pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "optio-claudecode"
version = "0.1.0"
description = "Run Anthropic Claude Code as an optio task; local subprocess or remote via SSH; ttyd-served TUI iframe."
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
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
    "Topic :: Software Development :: Code Generators",
    "Framework :: AsyncIO",
]
dependencies = [
    "optio-core>=0.1,<0.2",
    "optio-host>=0.1,<0.2",
    "asyncssh>=2.14",
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

- [ ] **Step 3: Write placeholder README**

Create `packages/optio-claudecode/README.md`:
```markdown
# optio-claudecode

Run Anthropic's Claude Code CLI as an `optio` task. The interactive TUI
is served over `ttyd` and embedded in the optio dashboard via an iframe
widget. Local subprocess or remote host via SSH.

(Expanded in the public-release commit — see plan task 19.)
```

- [ ] **Step 4: Write the package `__init__.py` stub**

Create `packages/optio-claudecode/src/optio_claudecode/__init__.py`:
```python
"""optio-claudecode — run Anthropic Claude Code as an optio task."""

# Public API is added across plan tasks 2–18. This stub exists so the
# package can be pip-installed and imported even before the public
# surface is fully wired.
```

- [ ] **Step 5: Write the tests `__init__.py`**

Create `packages/optio-claudecode/tests/__init__.py`:
```python
```
(empty file — marks `tests/` as a package so pytest discovers it consistently.)

- [ ] **Step 6: Install in dev mode**

```bash
pip install -e packages/optio-claudecode[dev]
```
Expected: `Successfully installed optio-claudecode-0.1.0`.

- [ ] **Step 7: Smoke-test the import**

```bash
python -c "import optio_claudecode; print(optio_claudecode.__doc__)"
```
Expected: `optio-claudecode — run Anthropic Claude Code as an optio task.`

- [ ] **Step 8: Commit**

```bash
git add packages/optio-claudecode/pyproject.toml \
        packages/optio-claudecode/README.md \
        packages/optio-claudecode/src/optio_claudecode/__init__.py \
        packages/optio-claudecode/tests/__init__.py
git commit -m "feat(optio-claudecode): package skeleton

Empty package wired into pip's dev installation pipeline. Public API
follows in subsequent commits per docs/2026-05-28-optio-claudecode-plan.md."
```

---

## Phase 2 — `ClaudeCodeTaskConfig` and the public types

### Task 2: Define `ClaudeCodeTaskConfig`

**Files:**
- Create: `packages/optio-claudecode/src/optio_claudecode/types.py`
- Test: `packages/optio-claudecode/tests/test_types.py`

- [ ] **Step 1: Write the failing test**

Create `packages/optio-claudecode/tests/test_types.py`:
```python
"""Tests for ClaudeCodeTaskConfig defaults and validation."""

import pytest

from optio_claudecode.types import ClaudeCodeTaskConfig


def test_minimal_config_uses_defaults():
    cfg = ClaudeCodeTaskConfig(consumer_instructions="hi")
    assert cfg.consumer_instructions == "hi"
    assert cfg.credentials_json is None
    assert cfg.claude_config is None
    assert cfg.env is None
    assert cfg.permission_mode is None
    assert cfg.allowed_tools is None
    assert cfg.disallowed_tools is None
    assert cfg.ssh is None
    assert cfg.install_if_missing is True
    assert cfg.install_ttyd_if_missing is True
    assert cfg.claude_install_dir is None
    assert cfg.ttyd_install_dir is None
    assert cfg.before_execute is None
    assert cfg.after_execute is None
    assert cfg.on_deliverable is None


def test_permission_mode_invalid_value_rejected():
    with pytest.raises(ValueError) as exc_info:
        ClaudeCodeTaskConfig(
            consumer_instructions="hi",
            permission_mode="invalidMode",
        )
    assert "permission_mode" in str(exc_info.value)
    assert "invalidMode" in str(exc_info.value)


@pytest.mark.parametrize("mode", ["default", "plan", "acceptEdits", "bypassPermissions"])
def test_permission_mode_accepts_documented_values(mode: str):
    cfg = ClaudeCodeTaskConfig(consumer_instructions="hi", permission_mode=mode)
    assert cfg.permission_mode == mode


def test_install_dir_must_be_absolute_when_set():
    with pytest.raises(ValueError) as exc_info:
        ClaudeCodeTaskConfig(
            consumer_instructions="hi",
            claude_install_dir="relative/path",
        )
    assert "absolute" in str(exc_info.value).lower()

    with pytest.raises(ValueError):
        ClaudeCodeTaskConfig(
            consumer_instructions="hi",
            ttyd_install_dir="also-relative",
        )


def test_install_dir_accepts_absolute():
    cfg = ClaudeCodeTaskConfig(
        consumer_instructions="hi",
        claude_install_dir="/opt/claude",
        ttyd_install_dir="/opt/ttyd",
    )
    assert cfg.claude_install_dir == "/opt/claude"
    assert cfg.ttyd_install_dir == "/opt/ttyd"


def test_credentials_json_accepts_dict_bytes_str():
    ClaudeCodeTaskConfig(consumer_instructions="hi", credentials_json={"a": 1})
    ClaudeCodeTaskConfig(consumer_instructions="hi", credentials_json=b"{}")
    ClaudeCodeTaskConfig(consumer_instructions="hi", credentials_json='{"a":1}')
```

- [ ] **Step 2: Run test to verify failure**

```bash
python -m pytest packages/optio-claudecode/tests/test_types.py -v
```
Expected: `ModuleNotFoundError: No module named 'optio_claudecode.types'`.

- [ ] **Step 3: Write `types.py`**

Create `packages/optio-claudecode/src/optio_claudecode/types.py`:
```python
"""Public data types for optio-claudecode consumers.

The generic ``DeliverableCallback`` / ``HookCallback`` types and
``SSHConfig`` are owned by ``optio-host``. This module re-exports them
alongside the package-specific ``ClaudeCodeTaskConfig``.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from optio_host.protocol.session import DeliverableCallback, HookCallback
from optio_host.types import SSHConfig


__all__ = [
    "DeliverableCallback",
    "HookCallback",
    "SSHConfig",
    "ClaudeCodeTaskConfig",
    "PermissionMode",
]


PermissionMode = Literal["default", "plan", "acceptEdits", "bypassPermissions"]
_VALID_PERMISSION_MODES = {"default", "plan", "acceptEdits", "bypassPermissions"}


@dataclass
class ClaudeCodeTaskConfig:
    """Configuration for one optio-claudecode task instance.

    See ``docs/2026-05-28-optio-claudecode-design.md`` for full field
    semantics.
    """

    # The consumer's prompt body. Appended verbatim to AGENTS.md after
    # the optio coordination preamble.
    consumer_instructions: str

    # Credentials payload (any JSON-serializable dict, or pre-serialized
    # bytes/str). Written to <workdir>/home/.claude/.credentials.json on
    # task start with mode 0600. None = do not plant.
    credentials_json: dict[str, Any] | bytes | str | None = None

    # Claude settings file (e.g. permission allowlists, MCP servers).
    # Written to <workdir>/home/.claude/settings.json as JSON.
    # None = do not plant.
    claude_config: dict[str, Any] | None = None

    # Extra env vars injected when launching ttyd+claude (ANTHROPIC_BASE_URL,
    # Bedrock vars, etc.).
    env: dict[str, str] | None = None

    # Permission knobs — forwarded verbatim to the claude CLI. When all
    # are None, no related flags are passed and claude uses its own
    # default (interactive per-tool prompts inside the TUI).
    permission_mode: PermissionMode | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None

    # None = LocalHost; else RemoteHost via optio-host's SSHConfig.
    ssh: SSHConfig | None = None

    # Binary install knobs.
    install_if_missing: bool = True
    install_ttyd_if_missing: bool = True
    claude_install_dir: str | None = None
    ttyd_install_dir: str | None = None

    # Hooks (optio-host's HookContext).
    before_execute: HookCallback | None = None
    after_execute: HookCallback | None = None
    on_deliverable: DeliverableCallback | None = None

    def __post_init__(self) -> None:
        if self.permission_mode is not None and self.permission_mode not in _VALID_PERMISSION_MODES:
            raise ValueError(
                f"ClaudeCodeTaskConfig.permission_mode={self.permission_mode!r} "
                f"is not one of {sorted(_VALID_PERMISSION_MODES)}"
            )
        for field_name in ("claude_install_dir", "ttyd_install_dir"):
            val = getattr(self, field_name)
            if val is not None and not val.startswith("/") and not val.startswith("~"):
                raise ValueError(
                    f"ClaudeCodeTaskConfig.{field_name}={val!r} must be an "
                    f"absolute path (start with '/' or '~')."
                )
```

- [ ] **Step 4: Run test to verify pass**

```bash
python -m pytest packages/optio-claudecode/tests/test_types.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/types.py \
        packages/optio-claudecode/tests/test_types.py
git commit -m "feat(optio-claudecode): ClaudeCodeTaskConfig dataclass

Public config dataclass with caller-decides permission knobs (no optio
default), HOME-isolation credentials passthrough, and ssh/install
toggles that mirror optio-opencode's shape."
```

---

## Phase 3 — Prompt composer wrapper

### Task 3: Claudecode-specific prompt composer

Claudecode's composer is a thin alias to `optio_host.agents.compose_agents_md` with `resume_section=None` (no resume in v1). Keeping a package-local entry point makes it easy to add a resume section later without touching call sites.

**Files:**
- Create: `packages/optio-claudecode/src/optio_claudecode/prompt.py`
- Test: `packages/optio-claudecode/tests/test_prompt.py`

- [ ] **Step 1: Write the failing test**

Create `packages/optio-claudecode/tests/test_prompt.py`:
```python
"""Tests for the claudecode AGENTS.md composer."""

from optio_claudecode.prompt import compose_agents_md


def test_compose_includes_consumer_instructions():
    body = compose_agents_md("Please write a haiku about MongoDB.")
    assert "Please write a haiku about MongoDB." in body


def test_compose_includes_coordination_preamble():
    body = compose_agents_md("Whatever.")
    assert "STATUS:" in body
    assert "DELIVERABLE:" in body
    assert "DONE" in body
    assert "ERROR" in body
    assert "./deliverables/" in body


def test_compose_has_no_resume_section_in_v1():
    body = compose_agents_md("Whatever.")
    assert "resume.log" not in body
    assert "## Resumes" not in body
```

- [ ] **Step 2: Run test to verify failure**

```bash
python -m pytest packages/optio-claudecode/tests/test_prompt.py -v
```
Expected: `ModuleNotFoundError: No module named 'optio_claudecode.prompt'`.

- [ ] **Step 3: Write `prompt.py`**

Create `packages/optio-claudecode/src/optio_claudecode/prompt.py`:
```python
"""AGENTS.md composer for optio-claudecode.

v1 has no resume support, so this is currently a one-line forward to
``optio_host.agents.compose_agents_md`` with ``resume_section=None``.
When resume lands, render and pass the section here.
"""

from optio_host.agents import compose_agents_md as _host_compose_agents_md


__all__ = ["compose_agents_md"]


def compose_agents_md(consumer_instructions: str) -> str:
    """Render <workdir>/AGENTS.md for an optio-claudecode task."""
    return _host_compose_agents_md(consumer_instructions, resume_section=None)
```

- [ ] **Step 4: Run test to verify pass**

```bash
python -m pytest packages/optio-claudecode/tests/test_prompt.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/prompt.py \
        packages/optio-claudecode/tests/test_prompt.py
git commit -m "feat(optio-claudecode): AGENTS.md composer

Thin wrapper around optio_host.agents.compose_agents_md with no resume
section. When v2 adds resume, render the section in this wrapper."
```

---

## Phase 4 — `host_actions`: binary install + flag building

Single file holds the four host-side operations claudecode needs:
1. `ensure_claude_installed` — vendor `https://claude.ai/install.sh` end-to-end.
2. `ensure_ttyd_installed` — download per-platform static binary from `tsl0922/ttyd` releases.
3. `build_claude_flags` — translate `permission_mode` / `allowed_tools` / `disallowed_tools` to argv.
4. `launch_ttyd_with_claude` — exec ttyd wrapping a bash shim that exec's claude with `HOME=<workdir>/home`.

### Task 4: `ensure_claude_installed` (vendor install.sh)

**Files:**
- Create: `packages/optio-claudecode/src/optio_claudecode/host_actions.py` (initial — more functions appended in tasks 5–7)
- Test: `packages/optio-claudecode/tests/test_host_actions.py`

- [ ] **Step 1: Write the failing test**

Create `packages/optio-claudecode/tests/test_host_actions.py`:
```python
"""Tests for optio-claudecode host actions (claude/ttyd install, launch)."""

from __future__ import annotations

import shlex
from unittest.mock import AsyncMock, MagicMock

import pytest

from optio_claudecode import host_actions
from optio_host import RunResult


class _FakeHost:
    """Minimal Host shim that records run_command calls and returns scripted results.

    Each scripted result is either:
      - a RunResult, returned verbatim
      - a callable taking the command string, returning a RunResult
    The scripted_results list is consumed in order.
    """

    def __init__(self, scripted_results, host_home: str = "/root") -> None:
        self.commands: list[str] = []
        self._scripted = list(scripted_results)
        self._host_home = host_home

    async def resolve_host_home(self) -> str:
        return self._host_home

    async def run_command(self, cmd: str, *, check: bool = False) -> RunResult:
        self.commands.append(cmd)
        nxt = self._scripted.pop(0)
        if callable(nxt):
            return nxt(cmd)
        return nxt


def _hook_ctx(host) -> MagicMock:
    """Build a minimal HookContext-shaped mock with .report_progress and ._host."""
    ctx = MagicMock()
    ctx._host = host
    ctx.report_progress = MagicMock()
    return ctx


async def test_ensure_claude_installed_present():
    host = _FakeHost([
        # `[ -x /root/.local/bin/claude ] && /root/.local/bin/claude --version`
        RunResult(stdout="2.1.153 (Claude Code)\n", stderr="", exit_code=0),
    ])
    ctx = _hook_ctx(host)
    path = await host_actions.ensure_claude_installed(ctx, install_if_missing=True)
    assert path == "/root/.local/bin/claude"
    assert len(host.commands) == 1
    # The check command MUST quote the install dir
    assert "/root/.local/bin/claude" in host.commands[0]


async def test_ensure_claude_installed_missing_install_disabled_raises():
    host = _FakeHost([
        RunResult(stdout="", stderr="No such file", exit_code=1),
    ])
    ctx = _hook_ctx(host)
    with pytest.raises(RuntimeError) as exc_info:
        await host_actions.ensure_claude_installed(ctx, install_if_missing=False)
    assert "install_if_missing" in str(exc_info.value)
    assert "False" in str(exc_info.value)


async def test_ensure_claude_installed_missing_runs_vendor_install():
    host = _FakeHost([
        # Step 1: check absent
        RunResult(stdout="", stderr="No such file", exit_code=1),
        # Step 2: vendor install.sh succeeds
        RunResult(stdout="Installation complete\n", stderr="", exit_code=0),
        # Step 3: re-check now-present
        RunResult(stdout="2.1.153 (Claude Code)\n", stderr="", exit_code=0),
    ])
    ctx = _hook_ctx(host)
    path = await host_actions.ensure_claude_installed(
        ctx, install_if_missing=True,
    )
    assert path == "/root/.local/bin/claude"
    assert len(host.commands) == 3
    # The install command MUST pipe claude.ai/install.sh through bash
    assert "claude.ai/install.sh" in host.commands[1]
    assert "bash" in host.commands[1]


async def test_ensure_claude_installed_explicit_install_dir_used():
    host = _FakeHost([
        RunResult(stdout="2.1.153 (Claude Code)\n", stderr="", exit_code=0),
    ])
    ctx = _hook_ctx(host)
    path = await host_actions.ensure_claude_installed(
        ctx, install_if_missing=True, install_dir="/opt/claude",
    )
    assert path == "/opt/claude/claude"
    assert "/opt/claude/claude" in host.commands[0]


async def test_ensure_claude_installed_install_failure_propagates():
    host = _FakeHost([
        RunResult(stdout="", stderr="No such file", exit_code=1),
        RunResult(stdout="", stderr="curl: 404 Not Found", exit_code=22),
    ])
    ctx = _hook_ctx(host)
    with pytest.raises(RuntimeError) as exc_info:
        await host_actions.ensure_claude_installed(ctx, install_if_missing=True)
    assert "install" in str(exc_info.value).lower()
    assert "22" in str(exc_info.value) or "404" in str(exc_info.value)
```

- [ ] **Step 2: Run test to verify failure**

```bash
python -m pytest packages/optio-claudecode/tests/test_host_actions.py::test_ensure_claude_installed_present -v
```
Expected: `ModuleNotFoundError: No module named 'optio_claudecode.host_actions'`.

- [ ] **Step 3: Write the initial `host_actions.py` with `ensure_claude_installed`**

Create `packages/optio-claudecode/src/optio_claudecode/host_actions.py`:
```python
"""Claudecode-specific actions over a generic Host.

Free functions; each takes a Host or HookContext and uses only generic
primitives (run_command, resolve_host_home, etc.). No isinstance branches.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from optio_host import HookContextProtocol, Host


_DEFAULT_INSTALL_SUBDIR = ".local/bin"

_CLAUDE_INSTALL_URL = "https://claude.ai/install.sh"


async def _resolve_install_dir(host: "Host", install_dir: str | None) -> str:
    """Return ``install_dir`` if given, else ``<host_home>/<DEFAULT_INSTALL_SUBDIR>``."""
    if install_dir is not None:
        return install_dir
    host_home = await host.resolve_host_home()
    return f"{host_home}/{_DEFAULT_INSTALL_SUBDIR}"


async def _claude_present(host: "Host", claude_path: str) -> bool:
    """Return True iff ``claude_path`` is an executable file on the host
    that produces version output when invoked with --version."""
    cmd = f"[ -x {shlex.quote(claude_path)} ] && {shlex.quote(claude_path)} --version"
    result = await host.run_command(cmd)
    return result.exit_code == 0 and "Claude Code" in result.stdout


async def ensure_claude_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
) -> str:
    """Ensure the ``claude`` binary is present on the host behind ``hook_ctx``.

    The framework looks for a symlink at ``<install_dir>/claude``. When
    missing and ``install_if_missing=True``, it runs the vendor install
    script (``curl -fsSL https://claude.ai/install.sh | bash``) on the
    host. The script downloads + checksum-verifies + places the native
    binary under ``~/.local/share/claude/versions/<v>/`` and creates a
    symlink at ``~/.local/bin/claude``. The framework re-checks for the
    symlink after the install runs.

    Returns the absolute path of the ``claude`` symlink on the host.

    Raises RuntimeError when the binary is absent and either
    ``install_if_missing=False`` or the install fails.
    """
    host = hook_ctx._host
    resolved_install_dir = await _resolve_install_dir(host, install_dir)
    claude_path = f"{resolved_install_dir}/claude"

    hook_ctx.report_progress(None, "Checking claude installation…")
    if await _claude_present(host, claude_path):
        return claude_path

    if not install_if_missing:
        raise RuntimeError(
            f"claude not present at {claude_path!r} on host and "
            f"install_if_missing=False; nothing to do."
        )

    hook_ctx.report_progress(None, "Installing claude (vendor install.sh)…")
    install_cmd = f"curl -fsSL {shlex.quote(_CLAUDE_INSTALL_URL)} | bash"
    result = await host.run_command(install_cmd)
    if result.exit_code != 0:
        raise RuntimeError(
            f"claude install failed on host (exit {result.exit_code}): "
            f"{result.stderr.strip()[:300]}"
        )

    if not await _claude_present(host, claude_path):
        raise RuntimeError(
            f"claude install reported success but {claude_path!r} is still "
            f"not executable on the host. Inspect the host's "
            f"~/.local/bin and ~/.local/share/claude/versions for diagnostics."
        )
    return claude_path
```

- [ ] **Step 4: Run tests to verify pass**

```bash
python -m pytest packages/optio-claudecode/tests/test_host_actions.py -v -k claude_installed
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py \
        packages/optio-claudecode/tests/test_host_actions.py
git commit -m "feat(optio-claudecode): ensure_claude_installed via vendor install.sh

Detects the claude symlink at <install_dir>/claude. When absent and
install_if_missing=True, runs https://claude.ai/install.sh end-to-end
and re-verifies before returning."
```

---

### Task 5: `ensure_ttyd_installed` (GitHub Releases)

`tsl0922/ttyd` ships per-platform static binaries on its release page (e.g. `ttyd.x86_64`, `ttyd.aarch64`). The naming is `ttyd.<arch>` for Linux. For macOS, the upstream releases page provides separate assets — but for v1 we only target Linux hosts (which is the same platform optio-opencode currently supports). macOS auto-install can be added when an actual macOS host materialises.

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py`
- Modify: `packages/optio-claudecode/tests/test_host_actions.py`

- [ ] **Step 1: Add the failing tests**

Append to `packages/optio-claudecode/tests/test_host_actions.py`:
```python
async def test_ensure_ttyd_installed_present():
    host = _FakeHost([
        # `[ -x /root/.local/bin/ttyd ] && /root/.local/bin/ttyd --version`
        RunResult(stdout="ttyd version 1.7.7-9d2", stderr="", exit_code=0),
    ])
    ctx = _hook_ctx(host)
    ctx.download_file = AsyncMock()
    path = await host_actions.ensure_ttyd_installed(ctx, install_if_missing=True)
    assert path == "/root/.local/bin/ttyd"
    assert ctx.download_file.call_count == 0


async def test_ensure_ttyd_installed_missing_install_disabled_raises():
    host = _FakeHost([
        RunResult(stdout="", stderr="not found", exit_code=1),
    ])
    ctx = _hook_ctx(host)
    ctx.download_file = AsyncMock()
    with pytest.raises(RuntimeError) as exc_info:
        await host_actions.ensure_ttyd_installed(ctx, install_if_missing=False)
    assert "install_ttyd_if_missing" in str(exc_info.value)


async def test_ensure_ttyd_installed_downloads_from_github_releases():
    host = _FakeHost([
        # 1. ttyd not present
        RunResult(stdout="", stderr="not found", exit_code=1),
        # 2. uname -m to detect arch
        RunResult(stdout="x86_64\n", stderr="", exit_code=0),
        # 3. uname -s to detect OS (linux only in v1)
        RunResult(stdout="Linux\n", stderr="", exit_code=0),
        # 4. mkdir -p /root/.local/bin
        RunResult(stdout="", stderr="", exit_code=0),
        # 5. chmod +x /root/.local/bin/ttyd
        RunResult(stdout="", stderr="", exit_code=0),
        # 6. recheck: now present
        RunResult(stdout="ttyd version 1.7.7-9d2", stderr="", exit_code=0),
    ])
    ctx = _hook_ctx(host)
    ctx.download_file = AsyncMock()
    path = await host_actions.ensure_ttyd_installed(ctx, install_if_missing=True)
    assert path == "/root/.local/bin/ttyd"
    # Exactly one download was issued, to the GitHub Releases URL for x86_64 Linux
    assert ctx.download_file.call_count == 1
    download_url = ctx.download_file.call_args.args[0]
    assert "github.com/tsl0922/ttyd" in download_url
    assert "ttyd.x86_64" in download_url
    # The target path of the download is the destination ttyd binary
    download_target = ctx.download_file.call_args.args[1]
    assert download_target == "/root/.local/bin/ttyd"


async def test_ensure_ttyd_installed_unsupported_os_raises():
    host = _FakeHost([
        RunResult(stdout="", stderr="not found", exit_code=1),
        RunResult(stdout="x86_64\n", stderr="", exit_code=0),
        RunResult(stdout="Darwin\n", stderr="", exit_code=0),
    ])
    ctx = _hook_ctx(host)
    ctx.download_file = AsyncMock()
    with pytest.raises(RuntimeError) as exc_info:
        await host_actions.ensure_ttyd_installed(ctx, install_if_missing=True)
    assert "Darwin" in str(exc_info.value) or "darwin" in str(exc_info.value).lower()
    assert "macOS" in str(exc_info.value) or "unsupported" in str(exc_info.value).lower()
```

- [ ] **Step 2: Run the tests to verify failure**

```bash
python -m pytest packages/optio-claudecode/tests/test_host_actions.py -v -k ttyd_installed
```
Expected: `AttributeError: module 'optio_claudecode.host_actions' has no attribute 'ensure_ttyd_installed'`.

- [ ] **Step 3: Append `ensure_ttyd_installed` to `host_actions.py`**

Append to `packages/optio-claudecode/src/optio_claudecode/host_actions.py`:
```python
# Pinned ttyd version. Update with care; the URL pattern below is
# tsl0922/ttyd's release-asset convention as of 1.7.x.
_TTYD_VERSION = "1.7.7"
_TTYD_RELEASE_BASE = (
    f"https://github.com/tsl0922/ttyd/releases/download/{_TTYD_VERSION}"
)


async def _ttyd_present(host: "Host", ttyd_path: str) -> bool:
    cmd = f"[ -x {shlex.quote(ttyd_path)} ] && {shlex.quote(ttyd_path)} --version"
    result = await host.run_command(cmd)
    # ttyd writes its version banner to stdout OR stderr depending on
    # version — accept either.
    blob = (result.stdout or "") + (result.stderr or "")
    return result.exit_code == 0 and "ttyd" in blob.lower()


async def _detect_ttyd_asset_name(host: "Host") -> str:
    """Return the upstream release-asset filename for the host's arch/OS.

    Raises RuntimeError on unsupported (OS, arch) combinations.
    """
    r_arch = await host.run_command("uname -m")
    if r_arch.exit_code != 0:
        raise RuntimeError(
            f"uname -m failed on host (exit {r_arch.exit_code}): "
            f"{r_arch.stderr.strip()[:200]}"
        )
    arch = r_arch.stdout.strip()
    r_os = await host.run_command("uname -s")
    if r_os.exit_code != 0:
        raise RuntimeError(
            f"uname -s failed on host (exit {r_os.exit_code}): "
            f"{r_os.stderr.strip()[:200]}"
        )
    os_name = r_os.stdout.strip()
    if os_name != "Linux":
        raise RuntimeError(
            f"unsupported host OS {os_name!r} for ttyd auto-install "
            f"(v1 supports Linux only; macOS support requires uploading "
            f"a Darwin binary or pre-installing ttyd manually)."
        )
    if arch not in {"x86_64", "aarch64", "armv7l"}:
        raise RuntimeError(
            f"unsupported host arch {arch!r} for ttyd auto-install. "
            f"See https://github.com/tsl0922/ttyd/releases for available "
            f"prebuilt assets."
        )
    return f"ttyd.{arch}"


async def ensure_ttyd_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
) -> str:
    """Ensure ``ttyd`` is present on the host behind ``hook_ctx``.

    When missing and ``install_if_missing=True``, downloads the
    appropriate static prebuilt asset from ``tsl0922/ttyd`` GitHub
    Releases via ``hook_ctx.download_file`` (so byte-progress shows in
    the dashboard).

    Returns the absolute path of the ``ttyd`` binary on the host.

    Raises RuntimeError on (a) absent binary with
    ``install_if_missing=False``; (b) unsupported (OS, arch); (c) any
    install sub-step failing.
    """
    host = hook_ctx._host
    resolved_install_dir = await _resolve_install_dir(host, install_dir)
    ttyd_path = f"{resolved_install_dir}/ttyd"

    hook_ctx.report_progress(None, "Checking ttyd installation…")
    if await _ttyd_present(host, ttyd_path):
        return ttyd_path

    if not install_if_missing:
        raise RuntimeError(
            f"ttyd not present at {ttyd_path!r} on host and "
            f"install_ttyd_if_missing=False; nothing to do."
        )

    hook_ctx.report_progress(None, "Detecting ttyd release asset…")
    asset = await _detect_ttyd_asset_name(host)
    url = f"{_TTYD_RELEASE_BASE}/{asset}"

    r = await host.run_command(f"mkdir -p {shlex.quote(resolved_install_dir)}")
    if r.exit_code != 0:
        raise RuntimeError(
            f"mkdir -p {resolved_install_dir!r} failed (exit {r.exit_code}): "
            f"{r.stderr.strip()[:200]}"
        )

    hook_ctx.report_progress(None, f"Downloading ttyd ({asset})…")
    await hook_ctx.download_file(url, ttyd_path)

    r = await host.run_command(f"chmod +x {shlex.quote(ttyd_path)}")
    if r.exit_code != 0:
        raise RuntimeError(
            f"chmod +x {ttyd_path!r} failed (exit {r.exit_code}): "
            f"{r.stderr.strip()[:200]}"
        )

    if not await _ttyd_present(host, ttyd_path):
        raise RuntimeError(
            f"ttyd install completed but {ttyd_path!r} is still not "
            f"executable on the host. Check the downloaded asset and "
            f"chmod result."
        )
    return ttyd_path
```

- [ ] **Step 4: Run the ttyd tests to verify pass**

```bash
python -m pytest packages/optio-claudecode/tests/test_host_actions.py -v -k ttyd_installed
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py \
        packages/optio-claudecode/tests/test_host_actions.py
git commit -m "feat(optio-claudecode): ensure_ttyd_installed from GitHub Releases

Downloads the per-arch static ttyd binary as an optio child task via
hook_ctx.download_file so the dashboard gets byte-progress. Linux only
for v1; macOS and Windows raise an explicit unsupported-OS error."
```

---

### Task 6: `build_claude_flags` — config-to-argv translation

Pure function. Tested with parametrize. Used by the launch action.

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py`
- Modify: `packages/optio-claudecode/tests/test_host_actions.py`

- [ ] **Step 1: Add the failing tests**

Append to `packages/optio-claudecode/tests/test_host_actions.py`:
```python
def test_build_claude_flags_all_none():
    flags = host_actions.build_claude_flags(
        permission_mode=None, allowed_tools=None, disallowed_tools=None,
    )
    assert flags == []


def test_build_claude_flags_permission_mode_only():
    flags = host_actions.build_claude_flags(
        permission_mode="bypassPermissions",
        allowed_tools=None, disallowed_tools=None,
    )
    assert flags == ["--permission-mode", "bypassPermissions"]


def test_build_claude_flags_allowed_disallowed_joined_with_commas():
    flags = host_actions.build_claude_flags(
        permission_mode=None,
        allowed_tools=["Read", "Write"],
        disallowed_tools=["Bash"],
    )
    assert flags == [
        "--allowed-tools", "Read,Write",
        "--disallowed-tools", "Bash",
    ]


def test_build_claude_flags_all_three():
    flags = host_actions.build_claude_flags(
        permission_mode="acceptEdits",
        allowed_tools=["Read"],
        disallowed_tools=["Bash", "Write"],
    )
    assert flags == [
        "--permission-mode", "acceptEdits",
        "--allowed-tools", "Read",
        "--disallowed-tools", "Bash,Write",
    ]


def test_build_claude_flags_empty_list_treated_as_none():
    """An empty list is equivalent to None: no flag emitted."""
    flags = host_actions.build_claude_flags(
        permission_mode=None,
        allowed_tools=[],
        disallowed_tools=[],
    )
    assert flags == []
```

- [ ] **Step 2: Run the tests to verify failure**

```bash
python -m pytest packages/optio-claudecode/tests/test_host_actions.py -v -k build_claude_flags
```
Expected: `AttributeError: module 'optio_claudecode.host_actions' has no attribute 'build_claude_flags'`.

- [ ] **Step 3: Append `build_claude_flags` to `host_actions.py`**

Append:
```python
def build_claude_flags(
    *,
    permission_mode: str | None,
    allowed_tools: list[str] | None,
    disallowed_tools: list[str] | None,
) -> list[str]:
    """Translate ClaudeCodeTaskConfig permission knobs to an argv list.

    Empty lists are treated as None: no flag is emitted.
    Validation of ``permission_mode`` values lives in
    ``ClaudeCodeTaskConfig.__post_init__``.
    """
    out: list[str] = []
    if permission_mode is not None:
        out += ["--permission-mode", permission_mode]
    if allowed_tools:
        out += ["--allowed-tools", ",".join(allowed_tools)]
    if disallowed_tools:
        out += ["--disallowed-tools", ",".join(disallowed_tools)]
    return out
```

- [ ] **Step 4: Run the tests to verify pass**

```bash
python -m pytest packages/optio-claudecode/tests/test_host_actions.py -v -k build_claude_flags
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py \
        packages/optio-claudecode/tests/test_host_actions.py
git commit -m "feat(optio-claudecode): build_claude_flags helper

Pure function translating permission_mode / allowed_tools /
disallowed_tools into an argv list for the claude CLI. Empty lists are
no-ops; permission_mode validation lives upstream in the dataclass."
```

---

### Task 7: HOME-isolation file writers (`plant_home_files`)

Three things to plant under `<workdir>/home/.claude/`:
1. `.credentials.json` (chmod 600) — from `credentials_json` config.
2. `settings.json` — from `claude_config`.
3. (Nothing else; claude creates the rest on first run.)

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py`
- Modify: `packages/optio-claudecode/tests/test_host_actions.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_host_actions.py`:
```python
async def test_plant_home_files_credentials_dict():
    host = MagicMock()
    host.write_text = AsyncMock()
    host.run_command = AsyncMock(return_value=RunResult(stdout="", stderr="", exit_code=0))
    host.workdir = "/tmp/optio-claudecode-abc"

    await host_actions.plant_home_files(
        host,
        credentials_json={"oauth_token": "secret"},
        claude_config=None,
    )

    # write_text was called for credentials.json (workdir-relative path)
    paths_written = [c.args[0] for c in host.write_text.call_args_list]
    assert "home/.claude/.credentials.json" in paths_written
    # The credentials.json content is the JSON encoding of the dict
    cred_call = [c for c in host.write_text.call_args_list
                 if c.args[0] == "home/.claude/.credentials.json"][0]
    import json
    assert json.loads(cred_call.args[1]) == {"oauth_token": "secret"}

    # chmod 600 was applied via run_command
    chmod_cmds = [c.args[0] for c in host.run_command.call_args_list
                  if "chmod" in c.args[0]]
    assert any("600" in c and "credentials.json" in c for c in chmod_cmds)


async def test_plant_home_files_credentials_bytes_kept_verbatim():
    host = MagicMock()
    host.write_text = AsyncMock()
    host.run_command = AsyncMock(return_value=RunResult(stdout="", stderr="", exit_code=0))
    host.workdir = "/tmp/x"

    raw = b'{"opaque":"blob"}'
    await host_actions.plant_home_files(
        host, credentials_json=raw, claude_config=None,
    )

    cred_call = [c for c in host.write_text.call_args_list
                 if c.args[0] == "home/.claude/.credentials.json"][0]
    # bytes payload is decoded as UTF-8 verbatim — no re-serialization
    assert cred_call.args[1] == raw.decode("utf-8")


async def test_plant_home_files_settings_json():
    host = MagicMock()
    host.write_text = AsyncMock()
    host.run_command = AsyncMock(return_value=RunResult(stdout="", stderr="", exit_code=0))
    host.workdir = "/tmp/x"

    settings = {"permissions": {"allow": ["Read"]}}
    await host_actions.plant_home_files(
        host, credentials_json=None, claude_config=settings,
    )

    paths_written = [c.args[0] for c in host.write_text.call_args_list]
    assert "home/.claude/settings.json" in paths_written
    settings_call = [c for c in host.write_text.call_args_list
                     if c.args[0] == "home/.claude/settings.json"][0]
    import json
    assert json.loads(settings_call.args[1]) == settings


async def test_plant_home_files_none_writes_nothing():
    host = MagicMock()
    host.write_text = AsyncMock()
    host.run_command = AsyncMock(return_value=RunResult(stdout="", stderr="", exit_code=0))
    host.workdir = "/tmp/x"

    await host_actions.plant_home_files(
        host, credentials_json=None, claude_config=None,
    )

    # No files written, but mkdir -p still ran for home/.claude
    assert host.write_text.call_count == 0
```

- [ ] **Step 2: Run the tests to verify failure**

```bash
python -m pytest packages/optio-claudecode/tests/test_host_actions.py -v -k plant_home
```
Expected: `AttributeError: module 'optio_claudecode.host_actions' has no attribute 'plant_home_files'`.

- [ ] **Step 3: Append `plant_home_files` to `host_actions.py`**

Append:
```python
import json
from typing import Any


async def plant_home_files(
    host: "Host",
    *,
    credentials_json: dict[str, Any] | bytes | str | None,
    claude_config: dict[str, Any] | None,
) -> None:
    """Plant per-task claude state under <workdir>/home/.claude/.

    Creates <workdir>/home/.claude/ (mkdir -p), writes the credentials
    payload and settings.json when supplied, and chmod-600s the
    credentials file. ``credentials_json`` accepts a dict (re-encoded as
    JSON), bytes (decoded as UTF-8 verbatim), or a string (written
    verbatim).
    """
    workdir = host.workdir.rstrip("/")
    home_claude_rel = "home/.claude"
    home_claude_abs = f"{workdir}/{home_claude_rel}"

    r = await host.run_command(f"mkdir -p {shlex.quote(home_claude_abs)}")
    if r.exit_code != 0:
        raise RuntimeError(
            f"mkdir -p {home_claude_abs!r} failed (exit {r.exit_code}): "
            f"{r.stderr.strip()[:200]}"
        )

    if credentials_json is not None:
        if isinstance(credentials_json, dict):
            payload = json.dumps(credentials_json)
        elif isinstance(credentials_json, bytes):
            payload = credentials_json.decode("utf-8")
        else:
            payload = credentials_json
        cred_rel = f"{home_claude_rel}/.credentials.json"
        await host.write_text(cred_rel, payload)
        # chmod 600 (workdir-relative path resolved via absolute join)
        cred_abs = f"{workdir}/{cred_rel}"
        r = await host.run_command(f"chmod 600 {shlex.quote(cred_abs)}")
        if r.exit_code != 0:
            raise RuntimeError(
                f"chmod 600 {cred_abs!r} failed (exit {r.exit_code}): "
                f"{r.stderr.strip()[:200]}"
            )

    if claude_config is not None:
        settings_rel = f"{home_claude_rel}/settings.json"
        await host.write_text(settings_rel, json.dumps(claude_config, indent=2))
```

- [ ] **Step 4: Run the tests to verify pass**

```bash
python -m pytest packages/optio-claudecode/tests/test_host_actions.py -v -k plant_home
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py \
        packages/optio-claudecode/tests/test_host_actions.py
git commit -m "feat(optio-claudecode): plant_home_files for HOME-isolation

Writes <workdir>/home/.claude/.credentials.json (chmod 600) and
settings.json when supplied. Each task gets its own private HOME inside
the workdir tempdir — the host user's real ~/.claude is never read or
modified."
```

---

### Task 8: `launch_ttyd_with_claude`

Builds the ttyd argv from the resolved binary paths and `claude` argv, launches via `host.launch_subprocess`, and probes the bound port for readiness. Returns `(ProcessHandle, port)` so the caller can establish the tunnel.

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py`
- Modify: `packages/optio-claudecode/tests/test_host_actions.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_host_actions.py`:
```python
def test_build_ttyd_argv_basic():
    argv = host_actions.build_ttyd_argv(
        ttyd_path="/usr/bin/ttyd",
        claude_path="/opt/claude/claude",
        workdir="/tmp/optio-claudecode-x",
        bind_iface="127.0.0.1",
        port=8765,
        extra_env={"ANTHROPIC_BASE_URL": "https://api.example.com"},
        claude_flags=["--permission-mode", "bypassPermissions"],
    )
    # ttyd flags
    assert argv[0] == "/usr/bin/ttyd"
    assert "-W" in argv
    assert "-i" in argv and "127.0.0.1" in argv
    assert "-p" in argv and "8765" in argv
    assert "-m" in argv and "1" in argv
    assert "-T" in argv and "xterm-256color" in argv
    # -- separator
    assert "--" in argv
    sep_idx = argv.index("--")
    # After --, we should have env+bash -c '...'
    assert argv[sep_idx + 1] == "env"
    # The env list should include HOME assignment and the extra env
    assert any(a.startswith("HOME=") for a in argv[sep_idx + 1:])
    assert "HOME=/tmp/optio-claudecode-x/home" in argv
    assert "ANTHROPIC_BASE_URL=https://api.example.com" in argv
    # The trailing bash -c command must cd into workdir and exec claude
    # with the supplied flags.
    bash_idx = argv.index("bash", sep_idx)
    assert argv[bash_idx + 1] == "-c"
    bash_payload = argv[bash_idx + 2]
    assert "cd /tmp/optio-claudecode-x" in bash_payload
    assert "exec /opt/claude/claude" in bash_payload
    assert "--permission-mode bypassPermissions" in bash_payload
```

- [ ] **Step 2: Run the test to verify failure**

```bash
python -m pytest packages/optio-claudecode/tests/test_host_actions.py -v -k build_ttyd_argv
```
Expected: `AttributeError: ... 'build_ttyd_argv'`.

- [ ] **Step 3: Append `build_ttyd_argv` (pure function) and `launch_ttyd_with_claude` to `host_actions.py`**

Append:
```python
def build_ttyd_argv(
    *,
    ttyd_path: str,
    claude_path: str,
    workdir: str,
    bind_iface: str,
    port: int,
    extra_env: dict[str, str] | None,
    claude_flags: list[str],
) -> list[str]:
    """Construct the full argv for the ttyd subprocess.

    Layout:
      <ttyd_path> -W -i <iface> -p <port> -m 1 -T xterm-256color --
      env HOME=<workdir>/home [<extra-env...>]
      bash -c 'cd <workdir> && exec <claude_path> [<claude_flags...>]'
    """
    workdir_clean = workdir.rstrip("/")
    home_dir = f"{workdir_clean}/home"
    env_assignments: list[str] = [f"HOME={home_dir}"]
    if extra_env:
        for k, v in extra_env.items():
            env_assignments.append(f"{k}={v}")
    claude_argv = " ".join(shlex.quote(c) for c in [claude_path, *claude_flags])
    bash_payload = f"cd {shlex.quote(workdir_clean)} && exec {claude_argv}"
    return [
        ttyd_path,
        "-W",
        "-i", bind_iface,
        "-p", str(port),
        "-m", "1",
        "-T", "xterm-256color",
        "--",
        "env",
        *env_assignments,
        "bash", "-c", bash_payload,
    ]


async def launch_ttyd_with_claude(
    host: "Host",
    *,
    ttyd_path: str,
    claude_path: str,
    bind_iface: str,
    port: int,
    extra_env: dict[str, str] | None,
    claude_flags: list[str],
    ready_timeout_s: float = 30.0,
) -> "ProcessHandle":
    """Spawn ttyd wrapping claude under HOME-isolation.

    Returns the ProcessHandle from ``host.launch_subprocess``. Does NOT
    probe readiness — port readiness is handled by the caller via the
    existing optio-host tunnel/probe flow.
    """
    argv = build_ttyd_argv(
        ttyd_path=ttyd_path,
        claude_path=claude_path,
        workdir=host.workdir,
        bind_iface=bind_iface,
        port=port,
        extra_env=extra_env,
        claude_flags=claude_flags,
    )
    handle = await host.launch_subprocess(argv)
    return handle
```

- [ ] **Step 4: Add an import for `ProcessHandle` at the top of `host_actions.py`**

Edit the `if TYPE_CHECKING:` block:
```python
if TYPE_CHECKING:
    from optio_host import HookContextProtocol, Host
    from optio_host.host import ProcessHandle
```

- [ ] **Step 5: Run the test to verify pass**

```bash
python -m pytest packages/optio-claudecode/tests/test_host_actions.py -v -k build_ttyd_argv
```
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py \
        packages/optio-claudecode/tests/test_host_actions.py
git commit -m "feat(optio-claudecode): build_ttyd_argv + launch_ttyd_with_claude

Composes the ttyd argv (-W -i -p -m 1 -T xterm-256color -- env HOME=...
bash -c 'cd workdir && exec claude'). HOME isolation lives entirely in
this argv. Pure-function builder tested independently of the launch
coroutine."
```

---

## Phase 5 — Session runner

### Task 9: `_build_host` and `run_claudecode_session` skeleton

This mirrors opencode's pattern: `_build_host` is extracted so tests can monkeypatch it with a fake. `run_claudecode_session` is the entry point that connects, installs both binaries, plants the home files, writes AGENTS.md, then delegates to `optio_host.protocol.session.run_log_protocol_session` for the protocol-driven body.

**Files:**
- Create: `packages/optio-claudecode/src/optio_claudecode/session.py`

- [ ] **Step 1: Write the session module**

Create `packages/optio-claudecode/src/optio_claudecode/session.py`:
```python
"""State machine for one optio-claudecode session.

Orchestrates a Host (local or remote) through:
  1. Ensure claude + ttyd binaries are installed on the host.
  2. Plant per-task HOME files (credentials.json, settings.json).
  3. Write AGENTS.md (consumer instructions + optio coordination prompt).
  4. Fire ``before_execute`` hook.
  5. Launch ttyd wrapping claude.
  6. Open the SSH tunnel and register the iframe widget.
  7. Hand off to ``run_log_protocol_session`` which tails ``optio.log``,
     dispatches DELIVERABLE / DONE / ERROR, and runs ``after_execute``.

Most of the per-session protocol plumbing lives in optio-host. This
module only does the claudecode-specific orchestration.
"""

from __future__ import annotations

import logging
import os
from typing import Callable

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance

from optio_host.context import HookContext
from optio_host.host import Host, LocalHost, ProcessHandle, RemoteHost
from optio_host.paths import task_dir
from optio_host.protocol.session import _SessionFailed, run_log_protocol_session

from optio_claudecode import host_actions
from optio_claudecode.prompt import compose_agents_md
from optio_claudecode.types import ClaudeCodeTaskConfig


_LOG = logging.getLogger(__name__)

# How long we wait for ttyd's bound port to be reachable after launch.
READY_TIMEOUT_S = 30.0

# Port chosen by the host for ttyd. Picked by an OS hint (port 0) and
# then resolved via ``host.allocate_port`` which optio-host already
# exposes for opencode.
_DEFAULT_TTYD_PORT = 0  # 0 = pick a free port


def _build_host(config: ClaudeCodeTaskConfig, process_id: str) -> Host:
    """Construct the appropriate Host for the given config.

    Extracted so tests monkeypatch ``session._build_host`` to inject a
    fake host (mirrors the opencode pattern).
    """
    taskdir = task_dir(
        ssh=config.ssh, process_id=process_id, consumer_name="optio-claudecode",
    )
    if config.ssh is None:
        os.makedirs(taskdir, exist_ok=True)
        host: Host = LocalHost(taskdir=taskdir)
        os.makedirs(host.workdir, exist_ok=True)
        return host
    return RemoteHost(ssh_config=config.ssh, taskdir=taskdir)


async def run_claudecode_session(
    ctx: ProcessContext, config: ClaudeCodeTaskConfig,
) -> None:
    """Execute function body for one optio-claudecode task instance."""
    host: Host = _build_host(config, ctx.process_id)
    launched_handle: ProcessHandle | None = None
    cancelled = False

    await host.connect()
    await host.setup_workdir()

    hook_ctx_outer = HookContext(ctx, host)
    claude_path = await host_actions.ensure_claude_installed(
        hook_ctx_outer,
        install_if_missing=config.install_if_missing,
        install_dir=config.claude_install_dir,
    )
    ttyd_path = await host_actions.ensure_ttyd_installed(
        hook_ctx_outer,
        install_if_missing=config.install_ttyd_if_missing,
        install_dir=config.ttyd_install_dir,
    )

    async def _claudecode_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle

        # Fresh start: the protocol driver has created workdir,
        # deliverables/, and an empty optio.log already. Plant
        # per-task HOME files and AGENTS.md before launching ttyd.
        await host_actions.plant_home_files(
            host,
            credentials_json=config.credentials_json,
            claude_config=config.claude_config,
        )
        await host.write_text(
            "AGENTS.md",
            compose_agents_md(config.consumer_instructions),
        )

        if config.before_execute is not None:
            await config.before_execute(hook_ctx)

        # Network binding (same env handling as opencode for multi-container deploys)
        bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
        upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
        ttyd_iface = bind_addr if isinstance(host, LocalHost) else "127.0.0.1"

        port = await host.allocate_port()
        claude_flags = host_actions.build_claude_flags(
            permission_mode=config.permission_mode,
            allowed_tools=config.allowed_tools,
            disallowed_tools=config.disallowed_tools,
        )
        ctx.report_progress(None, "Launching claude (ttyd)…")
        handle = await host_actions.launch_ttyd_with_claude(
            host,
            ttyd_path=ttyd_path,
            claude_path=claude_path,
            bind_iface=ttyd_iface,
            port=port,
            extra_env=config.env,
            claude_flags=claude_flags,
            ready_timeout_s=READY_TIMEOUT_S,
        )
        launched_handle = handle

        worker_port = await host.establish_tunnel(port, bind_addr=bind_addr)
        await ctx.set_widget_upstream(f"http://{upstream_host}:{worker_port}")
        await ctx.set_widget_data({
            "iframeSrc": "{widgetProxyUrl}/",
        })
        ctx.report_progress(None, "claude is live")

        # Await ttyd subprocess exit. The protocol driver cancels this
        # body when it sees DONE/ERROR; otherwise we get here only on a
        # premature exit, which the driver detects as failure.
        proc = launched_handle.pid_like
        await proc.wait()  # type: ignore[union-attr]

    session_error: BaseException | None = None
    try:
        await run_log_protocol_session(
            host, ctx,
            body=_claudecode_body,
            on_deliverable=config.on_deliverable,
            after_execute=config.after_execute,
        )
    except _SessionFailed as fail:
        session_error = fail
        raise RuntimeError(str(fail)) from None
    except BaseException as exc:
        session_error = exc
        raise
    finally:
        if not ctx.should_continue():
            cancelled = True
        if launched_handle is not None:
            try:
                await host.terminate_subprocess(launched_handle, aggressive=cancelled)
            except Exception:
                _LOG.exception("terminate_subprocess failed")
        try:
            await host.cleanup_taskdir(aggressive=cancelled)
        except Exception:
            _LOG.exception("cleanup_taskdir failed")
        try:
            await host.disconnect()
        except Exception:
            _LOG.exception("host.disconnect failed")


def create_claudecode_task(
    process_id: str,
    name: str,
    config: ClaudeCodeTaskConfig,
    description: str | None = None,
) -> TaskInstance:
    """Return a TaskInstance that runs one optio-claudecode session."""

    async def _execute(ctx: ProcessContext) -> None:
        await run_claudecode_session(ctx, config)

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        ui_widget="iframe",
        supports_resume=False,
    )
```

- [ ] **Step 2: Verify it imports cleanly**

```bash
python -c "from optio_claudecode.session import create_claudecode_task, run_claudecode_session; print('ok')"
```
Expected: `ok`.

If this fails with `AttributeError: 'Host' object has no attribute 'allocate_port'`, see Task 9b below: optio-host exposes the same port-allocation primitive opencode already uses; you may need to adjust the call to match (`host.pick_free_port()` etc.). Run `grep -n "def allocate_port\|def pick_free_port\|def reserve_port" packages/optio-host/src/optio_host/host.py` to find the correct method name and update the call in `session.py` before continuing.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/session.py
git commit -m "feat(optio-claudecode): run_claudecode_session + create_claudecode_task

Wires HOME-isolation, AGENTS.md composition, binary install, hook
firing, ttyd launch, tunnel + widget registration, and protocol-driver
delegation. Tests follow."
```

---

## Phase 6 — Local integration test (fake claude)

### Task 10: Fake `claude` shim + ttyd-shim for tests

Tests cannot rely on the real claude binary (it would require live credentials and consume API budget). Instead they substitute claude with a small Python script that emits the optio.log keywords. Tests substitute ttyd similarly so they do not depend on ttyd being installed during CI.

**Files:**
- Create: `packages/optio-claudecode/tests/fake_claude.py`
- Create: `packages/optio-claudecode/tests/ttyd-shim.sh`
- Create: `packages/optio-claudecode/tests/claude-shim.sh`

- [ ] **Step 1: Write `fake_claude.py`**

Create `packages/optio-claudecode/tests/fake_claude.py`:
```python
"""Stand-in for the `claude` CLI during integration tests.

Reads the scenario name from the env var ``FAKE_CLAUDE_SCENARIO``
(default ``happy``) and runs a deterministic script of optio.log writes
+ sleeps + (optionally) deliverable writes. Stays alive until DONE or
ERROR has been emitted; the framework will signal SIGTERM to terminate
the wrapping ttyd process at that point.
"""

import argparse
import os
import sys
import time
from pathlib import Path


SCENARIOS = ("happy", "deliverable", "error", "long")


def _log(line: str) -> None:
    log = Path.cwd() / "optio.log"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")
        fh.flush()


def _scenario_happy() -> None:
    time.sleep(0.05)
    _log("STATUS: 10% fake claude alive")
    time.sleep(0.05)
    _log("STATUS: 50% pretending to work")
    time.sleep(0.05)
    _log("DONE: scenario completed")
    # Hang around — the framework should SIGTERM ttyd which kills us.
    time.sleep(30.0)


def _scenario_deliverable() -> None:
    workdir = Path.cwd()
    (workdir / "deliverables").mkdir(exist_ok=True)
    (workdir / "deliverables" / "greeting.txt").write_text(
        "hello from fake claude\n", encoding="utf-8",
    )
    time.sleep(0.05)
    _log("DELIVERABLE: greeting.txt")
    time.sleep(0.05)
    _log("DONE")
    time.sleep(30.0)


def _scenario_error() -> None:
    time.sleep(0.05)
    _log("ERROR: scenario asked for failure")
    time.sleep(30.0)


def _scenario_long() -> None:
    # Stays alive indefinitely — used to test cancellation paths.
    while True:
        time.sleep(0.5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--permission-mode", default=None)
    parser.add_argument("--allowed-tools", default=None)
    parser.add_argument("--disallowed-tools", default=None)
    parser.add_argument("--print", default=None, nargs="?", const="")
    args, _unknown = parser.parse_known_args()
    if args.version:
        print("2.1.153 (Claude Code) [fake_claude.py]")
        return 0
    scenario = os.environ.get("FAKE_CLAUDE_SCENARIO", "happy").strip()
    if scenario not in SCENARIOS:
        print(f"unknown FAKE_CLAUDE_SCENARIO={scenario!r}", file=sys.stderr)
        return 2
    {
        "happy": _scenario_happy,
        "deliverable": _scenario_deliverable,
        "error": _scenario_error,
        "long": _scenario_long,
    }[scenario]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Write `claude-shim.sh`**

Create `packages/optio-claudecode/tests/claude-shim.sh`:
```bash
#!/bin/bash
# Substitutes the real claude binary during tests. Forwards all args to
# fake_claude.py from this directory.
exec python3 "$(dirname "$0")/fake_claude.py" "$@"
```

- [ ] **Step 3: Write `ttyd-shim.sh`**

Create `packages/optio-claudecode/tests/ttyd-shim.sh`:
```bash
#!/bin/bash
# Substitutes the real ttyd binary during tests.
#
# Real ttyd binds a port and serves a WS; tests don't need any of that.
# This shim parses ttyd's flags, ignores the network ones, and exec's
# the inner command after the `--` separator.
#
# Args layout (from build_ttyd_argv):
#   ttyd -W -i <iface> -p <port> -m 1 -T xterm-256color --
#        env HOME=... bash -c '...'
#
# We just skip everything up to and including `--` and exec the rest.
#
# Special: `--version` is supported so ensure_ttyd_installed can detect
# us as a working ttyd binary.
if [ "$1" = "--version" ]; then
    echo "ttyd 1.0.0-test-shim"
    exit 0
fi
while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
    shift
done
if [ "$1" = "--" ]; then
    shift
fi
exec "$@"
```

- [ ] **Step 4: Set executable bits**

```bash
chmod +x packages/optio-claudecode/tests/claude-shim.sh \
         packages/optio-claudecode/tests/ttyd-shim.sh
```

- [ ] **Step 5: Smoke-test fake claude in isolation**

```bash
cd /tmp && mkdir -p sm && cd sm && rm -f optio.log && \
    FAKE_CLAUDE_SCENARIO=happy timeout 2 python3 \
    /home/csillag/deai/optio/packages/optio-claudecode/tests/fake_claude.py \
    || true
cat /tmp/sm/optio.log
cd /home/csillag/deai/optio
```
Expected: `optio.log` contains three lines (`STATUS: 10%`, `STATUS: 50%`, `DONE: scenario completed`).

- [ ] **Step 6: Commit**

```bash
git add packages/optio-claudecode/tests/fake_claude.py \
        packages/optio-claudecode/tests/claude-shim.sh \
        packages/optio-claudecode/tests/ttyd-shim.sh
git commit -m "test(optio-claudecode): fake claude + ttyd shims

fake_claude.py emits scripted optio.log lines (happy / deliverable /
error / long scenarios). claude-shim.sh / ttyd-shim.sh expose those
behaviors as drop-in binaries the framework can launch via the normal
host_actions.launch path."
```

---

### Task 11: Conftest fixtures (shim install dirs, MongoDB-backed ProcessContext)

The fixture pattern is borrowed verbatim from `packages/optio-opencode/tests/conftest.py` and `packages/optio-opencode/tests/test_session_local.py::ctx_and_captures`. Inlined below so this plan is self-contained.

**Files:**
- Create: `packages/optio-claudecode/tests/conftest.py`

- [ ] **Step 1: Write `conftest.py`**

Create `packages/optio-claudecode/tests/conftest.py`:
```python
"""Shared pytest fixtures for optio-claudecode integration tests.

Fixtures:

* ``shim_install_dir`` — a tmp_path subdir containing symlinks named
  ``claude`` and ``ttyd`` pointing at the package-shipped shim scripts.
  Pass this as both ``claude_install_dir`` and ``ttyd_install_dir`` in
  ``ClaudeCodeTaskConfig`` to bypass real binary detection.
* ``mongo_db`` — a per-test isolated Mongo db (matches opencode's
  conftest verbatim).
* ``ctx_and_captures`` — a ``ProcessContext`` backed by ``mongo_db`` with
  ``report_progress`` / ``set_widget_upstream`` / ``set_widget_data``
  intercepted into a ``Captured`` dataclass so tests can assert on
  observed state.

The fixture body is a direct port of opencode's pattern. Update both in
lockstep if the ProcessContext constructor signature changes.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import shutil
import tempfile
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from optio_core.context import ProcessContext


TESTS_DIR = pathlib.Path(__file__).parent


@pytest.fixture
def shim_install_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Tmp dir containing symlinks to the claude + ttyd shims.

    Both symlinks land at the path the framework expects
    (``<dir>/claude`` and ``<dir>/ttyd``) and the shim sources are made
    executable.
    """
    target = tmp_path / "shims"
    target.mkdir()
    for name, source in (
        ("claude", TESTS_DIR / "claude-shim.sh"),
        ("ttyd", TESTS_DIR / "ttyd-shim.sh"),
    ):
        link = target / name
        os.symlink(source, link)
        os.chmod(source, 0o755)
    return target


@pytest.fixture
def tmp_workdir():
    """A temporary directory removed after the test."""
    path = tempfile.mkdtemp(prefix="optio-claudecode-test-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest_asyncio.fixture
async def mongo_db():
    """Per-test MongoDB database, dropped after each test."""
    client = AsyncIOMotorClient(
        os.environ.get("MONGO_URL", "mongodb://localhost:27017"),
    )
    db_name = f"optio_claudecode_test_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


@dataclass
class Captured:
    progress: list[tuple[float | None, str | None]] = field(default_factory=list)
    widget_upstream: list[tuple[str, object]] = field(default_factory=list)
    widget_data: list[object] = field(default_factory=list)


@pytest_asyncio.fixture
async def ctx_and_captures(mongo_db, monkeypatch):
    """ProcessContext bound to ``mongo_db`` with capture hooks.

    Yields ``(ctx, captured, cancellation_flag)``. Tests pass ``ctx``
    into ``run_claudecode_session`` (or to a TaskInstance's execute fn),
    assert against ``captured.progress`` / ``.widget_upstream`` /
    ``.widget_data``, and set ``cancellation_flag`` to exercise the
    cancel path.
    """
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
```

- [ ] **Step 2: Verify the fixtures collect**

```bash
python -m pytest packages/optio-claudecode/tests/ --collect-only 2>&1 | tail -20
```
Expected: pytest reports the fixture definitions; no collection errors. (Test files may not exist yet; ignore "no tests collected".)

- [ ] **Step 3: Commit**

```bash
git add packages/optio-claudecode/tests/conftest.py
git commit -m "test(optio-claudecode): conftest with shim_install_dir + ctx_and_captures

Ports opencode's mongo_db + ProcessContext-with-captures fixture pattern
verbatim. shim_install_dir provides a tmp dir with claude/ttyd shim
symlinks the framework detects as real binaries and execs into
fake_claude.py."
```

---

### Task 12: Local session smoke test (happy path)

This test wires the full session against the LocalHost path, asserting:
- AGENTS.md is written to the workdir with the consumer instructions and the coordination preamble
- HOME-isolation files land under `<workdir>/home/.claude/`
- The ttyd-shim execs fake claude which emits DONE → session terminates
- After the session, the real `~/.claude/` is untouched

**Files:**
- Create: `packages/optio-claudecode/tests/test_session_local.py`

- [ ] **Step 1: Write the test**

Create `packages/optio-claudecode/tests/test_session_local.py`:
```python
"""Integration test: full optio-claudecode session against a LocalHost.

Uses the shim binaries from conftest. The session should:

  * write AGENTS.md, HOME-isolation files, and (via fake_claude) hit DONE.
  * never touch the host user's real ~/.claude/.
"""

from __future__ import annotations

import json
import os
import pathlib

import pytest

from optio_claudecode import (
    ClaudeCodeTaskConfig,
    create_claudecode_task,
)


@pytest.mark.asyncio
async def test_local_happy_path_writes_agents_md_and_home_files(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    """Assert AGENTS.md / credentials.json / settings.json placement.

    Reads them inside ``before_execute`` because the session's
    ``finally`` calls ``cleanup_taskdir`` after the run, removing the
    workdir before this test body resumes. ``before_execute`` fires
    AFTER all four files are planted and BEFORE ttyd launches — the
    exact assertion point we want.
    """
    ctx, _captures, _cancellation_flag = ctx_and_captures
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")

    observed: dict[str, object] = {}

    async def assert_in_before(hook_ctx):
        workdir = pathlib.Path(hook_ctx._host.workdir)
        observed["agents_md"] = (workdir / "AGENTS.md").read_text()
        cred_path = workdir / "home" / ".claude" / ".credentials.json"
        observed["cred_json"] = json.loads(cred_path.read_text())
        observed["cred_mode"] = oct(cred_path.stat().st_mode)[-3:]
        settings_path = workdir / "home" / ".claude" / "settings.json"
        observed["settings_json"] = json.loads(settings_path.read_text())

    task = create_claudecode_task(
        process_id="cc-local-happy",
        name="Local happy",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="Hello from the test.",
            credentials_json={"oauth_token": "test-token"},
            claude_config={"permissions": {"allow": ["Read"]}},
            permission_mode="bypassPermissions",
            claude_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            before_execute=assert_in_before,
        ),
    )
    await task.execute(ctx)

    assert "Hello from the test." in observed["agents_md"]
    assert "STATUS:" in observed["agents_md"]
    assert observed["cred_json"] == {"oauth_token": "test-token"}
    assert observed["cred_mode"] == "600"
    assert observed["settings_json"] == {"permissions": {"allow": ["Read"]}}
```

- [ ] **Step 2: Run the test to verify pass**

```bash
python -m pytest packages/optio-claudecode/tests/test_session_local.py::test_local_happy_path_writes_agents_md_and_home_files -v
```
Expected: 1 passed. (If `mongo_ctx` is missing, factor the Mongo fixture out of `optio-opencode`'s conftest first — copy the same `mongo_ctx` fixture body verbatim into claudecode's conftest. The fixture pattern is documented in opencode's `tests/conftest.py`.)

- [ ] **Step 3: Commit**

```bash
git add packages/optio-claudecode/tests/test_session_local.py \
        packages/optio-claudecode/tests/conftest.py
git commit -m "test(optio-claudecode): local happy-path session smoke test

Asserts AGENTS.md content, HOME-isolation file placement, mode 600 on
the credentials file, and clean termination on the fake-claude DONE."
```

---

### Task 13: Local deliverable + on_deliverable callback test

**Files:**
- Modify: `packages/optio-claudecode/tests/test_session_local.py`

- [ ] **Step 1: Append the test**

Append to `test_session_local.py`:
```python
@pytest.mark.asyncio
async def test_local_deliverable_callback_fired(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    ctx, _captures, _cancellation_flag = ctx_and_captures
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "deliverable")

    captured: list[tuple[str, str]] = []

    async def on_deliverable(hook_ctx, path, text):
        captured.append((path, text))

    task = create_claudecode_task(
        process_id="cc-local-deliverable",
        name="Local deliverable",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="Hand back a file.",
            claude_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            on_deliverable=on_deliverable,
        ),
    )
    await task.execute(ctx)

    assert len(captured) == 1
    path, text = captured[0]
    assert path == "greeting.txt"
    assert text == "hello from fake claude\n"
```

- [ ] **Step 2: Run the test**

```bash
python -m pytest packages/optio-claudecode/tests/test_session_local.py::test_local_deliverable_callback_fired -v
```
Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-claudecode/tests/test_session_local.py
git commit -m "test(optio-claudecode): deliverable callback fires with decoded text

Asserts the framework SFTPs the deliverable back from <workdir>/
deliverables/ and invokes on_deliverable with the workdir-relative path
and decoded UTF-8 text."
```

---

### Task 14: Local error path test

**Files:**
- Modify: `packages/optio-claudecode/tests/test_session_local.py`

- [ ] **Step 1: Append the test**

```python
@pytest.mark.asyncio
async def test_local_error_keyword_propagates(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    ctx, _captures, _cancellation_flag = ctx_and_captures
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "error")
    task = create_claudecode_task(
        process_id="cc-local-error",
        name="Local error",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="Fail please.",
            claude_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
        ),
    )
    with pytest.raises(RuntimeError) as exc_info:
        await task.execute(ctx)
    assert "scenario asked for failure" in str(exc_info.value)
```

- [ ] **Step 2: Run the test**

```bash
python -m pytest packages/optio-claudecode/tests/test_session_local.py::test_local_error_keyword_propagates -v
```
Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-claudecode/tests/test_session_local.py
git commit -m "test(optio-claudecode): ERROR keyword surfaces as RuntimeError"
```

---

### Task 15: HOME-isolation assertion test

**Files:**
- Create: `packages/optio-claudecode/tests/test_home_isolation.py`

- [ ] **Step 1: Write the test**

Create `packages/optio-claudecode/tests/test_home_isolation.py`:
```python
"""Verify that an optio-claudecode session never reads or modifies the
host user's real ~/.claude/ directory.

Strategy: set HOME to a controlled tmp_path *for the test process and
all its children*, pre-populate that fake-real-home with a sentinel
~/.claude/.credentials.json file, run a session, and verify the
sentinel is byte-identical after the session ran.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from optio_claudecode import ClaudeCodeTaskConfig, create_claudecode_task


@pytest.mark.asyncio
async def test_real_home_credentials_untouched(
    tmp_path: pathlib.Path,
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    ctx, _captures, _cancellation_flag = ctx_and_captures
    fake_real_home = tmp_path / "real_home"
    fake_real_home.mkdir()
    (fake_real_home / ".claude").mkdir()
    sentinel = fake_real_home / ".claude" / ".credentials.json"
    sentinel.write_text('{"sentinel": true}', encoding="utf-8")
    sentinel_mtime = sentinel.stat().st_mtime_ns
    sentinel_content = sentinel.read_text(encoding="utf-8")

    monkeypatch.setenv("HOME", str(fake_real_home))
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")

    task = create_claudecode_task(
        process_id="cc-home-isolation",
        name="Home isolation",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="Hi.",
            credentials_json={"injected": True},
            claude_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
        ),
    )
    await task.execute(ctx)

    # Sentinel byte-identical
    assert sentinel.read_text(encoding="utf-8") == sentinel_content
    # mtime unchanged (allow tolerance for FS quirks: must NOT have been written to)
    assert sentinel.stat().st_mtime_ns == sentinel_mtime
```

- [ ] **Step 2: Run the test**

```bash
python -m pytest packages/optio-claudecode/tests/test_home_isolation.py -v
```
Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-claudecode/tests/test_home_isolation.py
git commit -m "test(optio-claudecode): real ~/.claude is untouched

Pre-populates a fake-real-HOME with a sentinel credentials file, runs a
session that plants its own credentials under the workdir HOME, and
verifies the sentinel's content and mtime are unchanged."
```

---

### Task 16: Hook firing tests (`before_execute`, `after_execute`)

**Files:**
- Create: `packages/optio-claudecode/tests/test_session_hooks.py`

- [ ] **Step 1: Write the tests**

Create `packages/optio-claudecode/tests/test_session_hooks.py`:
```python
"""before_execute and after_execute hook semantics."""

from __future__ import annotations

import pathlib

import pytest

from optio_claudecode import ClaudeCodeTaskConfig, create_claudecode_task


@pytest.mark.asyncio
async def test_before_execute_called_after_home_files_planted(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    """before_execute must run after credentials/AGENTS.md exist but
    before claude launches."""
    ctx, _captures, _cancellation_flag = ctx_and_captures
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")
    observed: dict[str, bool] = {}

    async def before(hook_ctx):
        workdir = pathlib.Path(hook_ctx._host.workdir)
        observed["agents_md_exists"] = (workdir / "AGENTS.md").exists()
        observed["cred_exists"] = (workdir / "home" / ".claude" / ".credentials.json").exists()
        observed["called"] = True

    task = create_claudecode_task(
        process_id="cc-before-hook",
        name="Before hook",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="Hi.",
            credentials_json={"a": 1},
            claude_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            before_execute=before,
        ),
    )
    await task.execute(ctx)
    assert observed == {
        "called": True,
        "agents_md_exists": True,
        "cred_exists": True,
    }


@pytest.mark.asyncio
async def test_after_execute_called_on_success(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    ctx, _captures, _cancellation_flag = ctx_and_captures
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")
    observed: dict[str, bool] = {}

    async def after(hook_ctx):
        observed["called"] = True

    task = create_claudecode_task(
        process_id="cc-after-hook-ok",
        name="After hook ok",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="Hi.",
            claude_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            after_execute=after,
        ),
    )
    await task.execute(ctx)
    assert observed == {"called": True}


@pytest.mark.asyncio
async def test_after_execute_called_on_error(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    ctx, _captures, _cancellation_flag = ctx_and_captures
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "error")
    observed: dict[str, bool] = {}

    async def after(hook_ctx):
        observed["called"] = True

    task = create_claudecode_task(
        process_id="cc-after-hook-err",
        name="After hook err",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="Hi.",
            claude_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            after_execute=after,
        ),
    )
    with pytest.raises(RuntimeError):
        await task.execute(ctx)
    assert observed == {"called": True}
```

- [ ] **Step 2: Run the tests**

```bash
python -m pytest packages/optio-claudecode/tests/test_session_hooks.py -v
```
Expected: 3 passed.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-claudecode/tests/test_session_hooks.py
git commit -m "test(optio-claudecode): before/after_execute hook semantics

before_execute observes a fully-planted workdir; after_execute fires on
both the success and ERROR paths."
```

---

## Phase 7 — Remote (SSH) verification

The session code from Task 9 already supports `SSHConfig` through `optio-host`'s `RemoteHost`. A full automated remote-integration test would need a sizable Docker-SSH fixture port from opencode (an `sshd_container` fixture, an `shim_install_dir_on_host` SCP helper, and a Dockerfile that installs python3 so `fake_claude.py` can run on the container).

To keep this branch scoped, the remote story for v1 is covered by:

1. **Code-level review** — confirm `session.py` uses `RemoteHost` only via the abstractions opencode already exercises in production. No claudecode-specific SSH code paths exist.
2. **A manual smoke step** below.
3. **Follow-up plan** — full Docker-SSH integration test ported from opencode (see "Open follow-ups" at the bottom of this plan).

### Task 17: Manual remote smoke (one-shot, not committed)

- [ ] **Step 1: Set up a reachable SSH host with claude pre-installed**

Either reuse `packages/optio-opencode/tests/docker-compose.sshd.yml` (start it, note the port/user/key path), or use any SSH-reachable VM you control. Pre-install `claude` on the host (`curl -fsSL https://claude.ai/install.sh | bash`) and `ttyd` (download a binary from `https://github.com/tsl0922/ttyd/releases` matching the host's arch and `chmod +x`).

- [ ] **Step 2: Run a one-off smoke script**

Create a throwaway `/tmp/cc_smoke.py`:
```python
import asyncio

from optio_claudecode import ClaudeCodeTaskConfig, create_claudecode_task
from optio_host import SSHConfig

# Replace with your reachable host's values
SSH = SSHConfig(host="127.0.0.1", port=2222, user="root", key_path="/path/to/key")

task = create_claudecode_task(
    process_id="cc-remote-smoke",
    name="Smoke",
    config=ClaudeCodeTaskConfig(
        consumer_instructions=(
            "Write 'STATUS: 50% smoke alive' to ./optio.log, then write "
            "'DONE: smoke done' to ./optio.log, then stop."
        ),
        credentials_json=None,
        ssh=SSH,
        install_if_missing=False,
        install_ttyd_if_missing=False,
        permission_mode="bypassPermissions",
    ),
)

async def main():
    # In a real engine, the framework would provide a ProcessContext via
    # task.execute. For smoke, you'd need to attach to a running optio
    # engine instead of running this standalone.
    print("Smoke task created:", task.process_id)

asyncio.run(main())
```

Manual verification only — this is not part of the test suite. Run it against your live engine, observe that the iframe widget materialises in the dashboard and that the session terminates cleanly after `DONE` is emitted.

- [ ] **Step 3: Do NOT commit `/tmp/cc_smoke.py`**

It's a throwaway. Manual verification done.

---

## Phase 8 — Public API + docs

### Task 18: Wire the public API in `__init__.py` + sanity test

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/__init__.py`
- Create: `packages/optio-claudecode/tests/test_sanity.py`

- [ ] **Step 1: Write the failing test**

Create `packages/optio-claudecode/tests/test_sanity.py`:
```python
"""Public-API surface tests."""

import optio_claudecode


def test_top_level_exports_factory_and_config():
    assert hasattr(optio_claudecode, "create_claudecode_task")
    assert hasattr(optio_claudecode, "ClaudeCodeTaskConfig")
    assert hasattr(optio_claudecode, "run_claudecode_session")


def test_re_exports_from_optio_host():
    # Hook + SSH types are sourced from optio-host but conveniently
    # re-exported so callers can `from optio_claudecode import SSHConfig`.
    assert hasattr(optio_claudecode, "SSHConfig")
    assert hasattr(optio_claudecode, "HookContext")
    assert hasattr(optio_claudecode, "HookContextProtocol")
    assert hasattr(optio_claudecode, "HostCommandError")
    assert hasattr(optio_claudecode, "RunResult")


def test_re_exports_callable_types():
    assert hasattr(optio_claudecode, "HookCallback")
    assert hasattr(optio_claudecode, "DeliverableCallback")
```

- [ ] **Step 2: Run the test to verify failure**

```bash
python -m pytest packages/optio-claudecode/tests/test_sanity.py -v
```
Expected: 3 failures (AttributeError on the top-level exports).

- [ ] **Step 3: Replace `__init__.py` with the full public API**

Edit `packages/optio-claudecode/src/optio_claudecode/__init__.py`:
```python
"""optio-claudecode — run Anthropic Claude Code as an optio task."""

import logging as _logging

from optio_host import (
    HookContext,
    HookContextProtocol,
    HostCommandError,
    RunResult,
    SSHConfig,
)

from optio_claudecode.session import create_claudecode_task, run_claudecode_session
from optio_claudecode.types import (
    ClaudeCodeTaskConfig,
    DeliverableCallback,
    HookCallback,
    PermissionMode,
)


# asyncssh emits per-connection INFO lines that flood the worker stdout
# once an SSH-backed session starts. Quiet by default; callers can opt
# back in.
_logging.getLogger("asyncssh").setLevel(_logging.WARNING)


__all__ = [
    "create_claudecode_task",
    "run_claudecode_session",
    "ClaudeCodeTaskConfig",
    "DeliverableCallback",
    "HookCallback",
    "PermissionMode",
    "SSHConfig",
    "HookContext",
    "HookContextProtocol",
    "HostCommandError",
    "RunResult",
]
```

- [ ] **Step 4: Run the test to verify pass**

```bash
python -m pytest packages/optio-claudecode/tests/test_sanity.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/__init__.py \
        packages/optio-claudecode/tests/test_sanity.py
git commit -m "feat(optio-claudecode): wire public API

Top-level exports: create_claudecode_task, ClaudeCodeTaskConfig,
permission/callback types, and re-exports of optio-host's HookContext /
SSHConfig / RunResult / HostCommandError."
```

---

### Task 19: Package README

**Files:**
- Modify: `packages/optio-claudecode/README.md`

- [ ] **Step 1: Replace the placeholder README**

Edit `packages/optio-claudecode/README.md`:
```markdown
# optio-claudecode

Run Anthropic Claude Code as an `optio` task — either as a local
subprocess or on a remote host over SSH — with the interactive TUI
embedded in the optio dashboard via an iframe widget served by `ttyd`.

## Install

```bash
pip install optio-claudecode
```

Requires Python 3.11+. Pulls `optio-core`, `optio-host`, and `asyncssh`.

On task start the package auto-installs the host binaries it needs
unless told otherwise:

* `claude` — via Anthropic's vendor script (`https://claude.ai/install.sh`)
* `ttyd` — static binary from `tsl0922/ttyd` GitHub Releases

## Quick start

```python
from optio_claudecode import (
    ClaudeCodeTaskConfig,
    create_claudecode_task,
)

def get_tasks():
    return [
        create_claudecode_task(
            process_id="example-task",
            name="Example",
            config=ClaudeCodeTaskConfig(
                consumer_instructions="Please write a haiku about MongoDB.",
                credentials_json=load_user_creds_from_db(user_id),
                # Optional: skip interactive permission prompts for autonomous flows.
                permission_mode="bypassPermissions",
            ),
        )
    ]
```

`credentials_json` is treated as an opaque payload and written verbatim
to `<workdir>/home/.claude/.credentials.json` (mode 0600) before claude
launches. Format follows whatever Anthropic's CLI currently expects.

## How it works

Each task gets a workdir tempdir (`/tmp/optio-claudecode-<uuid>/`). The
ttyd process is launched with `HOME=<workdir>/home`, so claude reads
all its state — credentials, settings, session history — strictly from
the per-task workdir and never touches the host user's real
`~/.claude/`. Two tasks on the same host can run concurrently without
shared-state races.

The agent is given a `<workdir>/AGENTS.md` that includes the
`optio.log` coordination protocol — `STATUS:` / `DELIVERABLE:` /
`DONE` / `ERROR` — verbatim from `optio_host.agents`. The same protocol
is used by `optio-opencode`, so the same `consumer_instructions` can be
swapped between the two packages.

See `docs/2026-05-28-optio-claudecode-design.md` for the full design.
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-claudecode/README.md
git commit -m "docs(optio-claudecode): full README"
```

---

### Task 20: Package-level AGENTS.md cheatsheet

**Files:**
- Create: `packages/optio-claudecode/AGENTS.md`

- [ ] **Step 1: Write `AGENTS.md`**

Create `packages/optio-claudecode/AGENTS.md`:
```markdown
# optio-claudecode — Agent Cheatsheet

Run Anthropic Claude Code as an optio task — local subprocess or remote
host via SSH — with the interactive TUI exposed in the dashboard via a
ttyd-served iframe.

Full design: `docs/2026-05-28-optio-claudecode-design.md`.

## Public API

```python
from optio_claudecode import ClaudeCodeTaskConfig, create_claudecode_task

create_claudecode_task(
    process_id="my-task",
    name="My task",
    config=ClaudeCodeTaskConfig(
        consumer_instructions="...",
        credentials_json=...,        # opaque dict/bytes/str → ~/.claude/.credentials.json
        claude_config=...,           # dict → ~/.claude/settings.json
        env={"ANTHROPIC_BASE_URL": "..."},
        permission_mode=None,        # default | plan | acceptEdits | bypassPermissions
        allowed_tools=None,
        disallowed_tools=None,
        ssh=None,
        install_if_missing=True,
        install_ttyd_if_missing=True,
        claude_install_dir=None,     # default ~/.local/bin (per host)
        ttyd_install_dir=None,
        before_execute=None,
        after_execute=None,
        on_deliverable=None,
    ),
)
```

`TaskInstance` returned has `ui_widget="iframe"` and `supports_resume=False`
baked in.

## ClaudeCodeTaskConfig field semantics

(See the design doc for full details; key callouts only here.)

* `credentials_json` — opaque payload; planted at `<workdir>/home/.claude/
  .credentials.json` with mode 0600. dict → JSON-encoded; bytes → UTF-8
  decoded verbatim; str → written verbatim.
* `claude_config` — JSON-encoded to `<workdir>/home/.claude/settings.json`.
* `permission_mode` — forwarded verbatim to `claude --permission-mode`.
  Validation happens in `__post_init__`.
* HOME isolation: every task sees `HOME=<workdir>/home` so concurrent
  tasks on one host never share `~/.claude/` state.

## Hooks

`before_execute(hook_ctx)`, `after_execute(hook_ctx)`,
`on_deliverable(hook_ctx, relative_path, decoded_text)`. Identical
signatures + failure semantics to optio-opencode.

`before_execute` fires **after** AGENTS.md and HOME files are planted
and **before** ttyd launches.

`after_execute` fires after claude exits (or after cancellation), on
both success and ERROR paths.

## Log-file contract

Same as opencode. AGENTS.md tells claude to append to `./optio.log`:

- `STATUS: [N%] <msg>`
- `DELIVERABLE: <path>`
- `DONE[: summary]`
- `ERROR[: message]`

DONE / ERROR terminate the session.

## Binary install

* claude — `curl -fsSL https://claude.ai/install.sh | bash`. Vendor
  script places binaries under `~/.local/share/claude/versions/<v>/`
  and a symlink at `~/.local/bin/claude`. The framework always exec's
  the absolute symlink path; no PATH mutation needed.
* ttyd — downloaded from `tsl0922/ttyd` GitHub Releases (pinned
  version). Linux x86_64/aarch64/armv7l only in v1.

Override install locations via `claude_install_dir` /
`ttyd_install_dir` (absolute paths).

## Testing

```
pytest packages/optio-claudecode/tests/
```

Needs MongoDB via Docker for the local + remote session tests. Remote
SSH tests use the `docker-compose.sshd.yml` setup; require
`docker` + `docker-compose` on PATH.

Fake binaries (`claude-shim.sh`, `ttyd-shim.sh`, `fake_claude.py`) live
in `tests/` and substitute the real ones during integration tests.
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-claudecode/AGENTS.md
git commit -m "docs(optio-claudecode): package-level AGENTS.md"
```

---

### Task 21: Update root `AGENTS.md` package table

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Read the existing root AGENTS.md package table**

```bash
grep -n "optio-opencode\|optio-api\|optio-ui" AGENTS.md | head -10
```
Note the line with the opencode entry in the package table.

- [ ] **Step 2: Add a row for optio-claudecode in the package table**

Open `AGENTS.md`. In the integration-levels table, add a new row after the `optio-opencode` row:

| Level | Package | Language | Install |
|-------|---------|----------|---------|
| ... | ... | ... | ... |
| 1+ — Opencode runner | `optio-opencode` | Python | workspace; runs `opencode web` as an optio task (local subprocess or remote via SSH) |
| 1+ — Claude Code runner | `optio-claudecode` | Python | workspace; runs `claude` as an optio task via ttyd-served iframe (local subprocess or remote via SSH) |

(The exact markdown around the table varies; preserve the existing column structure and only add the one row.)

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "docs(optio): list optio-claudecode in the root package table"
```

---

## Phase 9 — Verification

### Task 22: Full-suite sanity run

- [ ] **Step 1: Run the full optio-claudecode test suite**

```bash
python -m pytest packages/optio-claudecode/ -v --tb=short 2>&1 | tail -50
```
Expected: all tests pass. Note any skips (e.g. remote SSH tests skipped because Docker not available — acceptable).

- [ ] **Step 2: Run the optio-host test suite (sanity-check the refactor)**

```bash
python -m pytest packages/optio-host/ -v --tb=short 2>&1 | tail -30
```
Expected: all tests pass.

- [ ] **Step 3: Run the optio-opencode test suite (regression-check the prompt delegation)**

```bash
python -m pytest packages/optio-opencode/ -x --tb=short 2>&1 | tail -30
```
Expected: same pass count as before this branch.

- [ ] **Step 4: Verify the branch graph**

```bash
git log --oneline main..HEAD
```
Expected: a clear linear history of the commits this plan produced (one per task).

- [ ] **Step 5: Hand off to finishing-a-development-branch**

At this point, follow `superpowers:finishing-a-development-branch` to decide how to integrate this branch (merge, PR, etc.). Do not auto-merge.

---

## File responsibility summary

| File | Responsibility |
|------|----------------|
| `packages/optio-host/src/optio_host/agents.py` | Shared optio.log/AGENTS.md base prompt + composer (new) |
| `packages/optio-opencode/src/optio_opencode/prompt.py` | Resume-section composition; wraps optio_host.agents (modified) |
| `packages/optio-claudecode/src/optio_claudecode/types.py` | `ClaudeCodeTaskConfig` dataclass + permission validation |
| `packages/optio-claudecode/src/optio_claudecode/prompt.py` | Thin wrapper around the shared composer (no resume in v1) |
| `packages/optio-claudecode/src/optio_claudecode/host_actions.py` | Free-function host actions: ensure_claude/ttyd_installed, plant_home_files, build_claude_flags, build_ttyd_argv, launch_ttyd_with_claude |
| `packages/optio-claudecode/src/optio_claudecode/session.py` | `_build_host`, `run_claudecode_session`, `create_claudecode_task` |
| `packages/optio-claudecode/src/optio_claudecode/__init__.py` | Public API re-exports |
| `packages/optio-claudecode/tests/conftest.py` | `shim_install_dir`, `mongo_db`, `ctx_and_captures` fixtures |
| `packages/optio-claudecode/tests/fake_claude.py` | Scripted stand-in for the real claude binary |
| `packages/optio-claudecode/tests/claude-shim.sh` | Drop-in claude binary that exec's fake_claude.py |
| `packages/optio-claudecode/tests/ttyd-shim.sh` | Drop-in ttyd binary that skips network flags and exec's the inner command |
| `packages/optio-claudecode/tests/test_*` | Test modules per phase |
| `packages/optio-claudecode/AGENTS.md` | Package-level cheatsheet |
| `packages/optio-claudecode/README.md` | Public package README |
| `AGENTS.md` | Root project: add optio-claudecode to package table |

## Spec coverage cross-check

(Self-review map — every spec section has a corresponding task.)

* Spec §"Summary" → Task 1 (pyproject + skeleton)
* Spec §"Goals" — interchangeability of consumer instructions → Tasks 0b, 0c (prompt extraction)
* Spec §"Non-goals" → omission of resume from `ClaudeCodeTaskConfig` (Task 2)
* Spec §"Architecture" — LocalHost path → Task 9 (code) + Tasks 12–16 (automated tests); RemoteHost path → Task 9 (code) + Task 17 (manual smoke); automated RemoteHost test deferred to follow-ups
* Spec §"Public API" → Tasks 2, 9, 18
* Spec §"HOME isolation" → Tasks 7, 8, 15
* Spec §"ttyd launch" → Task 8 (`build_ttyd_argv`, `launch_ttyd_with_claude`); `-o` deliberately absent
* Spec §"Network binding" → Task 9 (env-derived bind_addr / upstream_host)
* Spec §"optio.log contract + AGENTS.md" → Tasks 0b, 3 (composer), 9 (writes the file)
* Spec §"Termination" → Task 9 (terminate_subprocess, cleanup_taskdir, disconnect) + Tasks 12, 13, 14 (assert DONE / DELIVERABLE / ERROR paths)
* Spec §"Hooks" → Task 16
* Spec §"Shared refactor precursor: `optio_host.agents`" → Tasks 0b, 0c
* Spec §"Binary install details — claude" → Task 4
* Spec §"Binary install details — ttyd" → Task 5
* Spec §"Testing" → Tasks 10–17, 22

## Open follow-ups (not in this plan)

* **Automated SSH-in-Docker remote test** — port opencode's `Dockerfile.sshd` + `docker-compose.sshd.yml` + `sshd_container` fixture + `shim_install_dir_on_host` SCP helper into claudecode's tests. Phase 7 in this plan only does a manual smoke. The code under test already supports SSH via `RemoteHost`; the missing piece is the test infrastructure.
* **Byte-progress for the claude vendor install** — would require re-implementing install.sh in Python; track if user feedback says download time is noticeable.
* **macOS auto-install for ttyd** — add a `Darwin` branch in `_detect_ttyd_asset_name` when a macOS host materialises.
* **Resume support** — add `RESUME_SECTION` to claudecode's `prompt.py`, snapshot machinery similar to opencode's `snapshots.py`, `resume.log` write, `on_resume_refresh` hook.
* **Excavator integration** — separate consumer-side change; pass `permission_mode="bypassPermissions"` per the [[excavator-claudecode-perm]] memory.
