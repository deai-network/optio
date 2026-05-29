# Protocol Variation API + Seed-Engine Relocation — Phase-3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land both remaining phase-3 deltas in one pass: (1) the optio-agents `get_protocol(browser=…)` protocol-variation API (docs + parser + browser-shim behavior bound to one per-agent decision), adopted by claudecode (`redirect`), opencode (`suppress`), and the demo; and (2) the relocation of the generic seed engine from `optio_host/seeds.py` to `optio_agents/seeds.py`.

**Architecture:** A frozen `Protocol` value object — built by `get_protocol(*, browser: BrowserMode)` in optio-agents — carries the mode-specific keyword documentation, the parser variant, and a `prepare_browser_shims(host)` method. The session driver (`run_log_protocol_session`) takes the `Protocol`, uses its parser, and installs the shims itself after `setup_workdir`; the resulting launch env is delivered to the agent body via `HookContext.browser_launch_env` (A1). Three shim implementations (claudecode capture + opencode suppression + none) collapse into one mode-switch. Separately, the seed engine module moves package with no behavior change.

**Tech Stack:** Python 3.11+, `dataclass` + `functools.partial`, `typing.Literal`, pytest + pytest-asyncio (`asyncio_mode = auto`), motor + GridFS, `tar`/`gzip` over the optio-host `Host` abstraction, MongoDB-via-Docker.

**Source specs:**
- `docs/2026-05-29-optio-protocol-variation-design.md` (the `get_protocol` API)
- `docs/2026-05-29-optio-claudecode-seed-design.md` (seed support; its `browser_capture` flag is superseded — see that doc's 2026-05-29 addendum)

---

## ⚠️ Execution model: PARALLEL-SHAPED, verification deferred

**This plan deliberately departs from per-task TDD ordering**, at the user's explicit and standing instruction (write plans for concurrent/swarm execution; defer all verification to the end so parallel agents can freely break intermediate states without building primitives first).

Concretely:
- **Phases 1–4 tasks run NO tests and NO type-checks.** Each task creates/edits exactly one file (or one tightly-coupled file pair) to its **final** content and commits. Tasks within a phase touch disjoint files and may be executed by parallel agents in any order.
- **All verification — every pytest suite + the grep guards — happens once in Phase 5.** The tree is expected to be import-broken *between* phases (e.g. after the engine moves but before importers are retargeted). That is fine and intended.
- Because no task asserts an intermediate RED/GREEN, every code step below shows the **complete final content** of what it writes. There are no "write failing test → run → implement" cycles.

**Cross-task contracts are frozen in the "Shared contracts" section below.** Every task references those exact signatures/names so parallel authors stay consistent.

---

## Shared contracts (every task conforms to these exact signatures)

```python
# optio_agents/browser_shims.py
BrowserMode = Literal["ignore", "suppress", "redirect"]
async def prepare_browser_shims(host: Host, browser: BrowserMode) -> dict[str, str] | None: ...

# optio_agents/protocol/parser.py
def parse_log_line(line: str, *, recognize_browser: bool = True) -> LogEvent: ...

# optio_agents/protocol/prompt.py
def build_log_channel_prompt(browser: BrowserMode = "ignore") -> str: ...

# optio_agents/protocol/protocol.py
@dataclass(frozen=True)
class Protocol:
    documentation: str
    parse_log_line: Callable[[str], LogEvent]
    browser: BrowserMode
    async def prepare_browser_shims(self, host: Host) -> dict[str, str] | None: ...
def get_protocol(*, browser: BrowserMode = "ignore") -> Protocol: ...

# optio_agents/protocol/session.py
async def run_log_protocol_session(host, ctx, *, body, on_deliverable=None,
                                    before_execute=None, after_execute=None,
                                    protocol: "Protocol | None" = None) -> None: ...
async def _tail_and_dispatch(host, ctx, deliverable_queue, done_flag, error_flag,
                             parse_line: "Callable[[str], LogEvent]") -> None: ...

# optio_agents/context.py
class HookContext:
    browser_launch_env: dict[str, str] | None   # default None, set by the driver

# optio_agents/prompt.py  (shared AGENTS.md composer; used by claudecode)
def compose_agents_md(consumer_instructions: str, *, documentation: str,
                      resume_section: str | None = None) -> str: ...

# optio_agents/seeds.py  (moved from optio_host/seeds.py; API unchanged)
#   SeedManifest, insert_seed, load_seed, delete_seed, list_seeds,
#   capture_seed, merge_seed   — all signatures identical to the optio_host version
```

Per-agent mode is decided **once** in each agent's session module:
`get_protocol(browser="redirect")` (claudecode), `get_protocol(browser="suppress")` (opencode).

---

## File Structure

**optio-agents — create / rename:**
- `src/optio_agents/browser_shims.py` — NEW (replaces `browser_capture.py`): `BrowserMode`, capture/suppress shim bodies, `prepare_browser_shims`.
- `src/optio_agents/protocol/protocol.py` — NEW: `Protocol`, `get_protocol`.
- `src/optio_agents/seeds.py` — MOVED from optio-host (generic seed engine).

**optio-agents — modify:**
- `src/optio_agents/browser_capture.py` — DELETED (moved to `browser_shims.py`).
- `src/optio_agents/protocol/parser.py` — `parse_log_line` gains `recognize_browser`.
- `src/optio_agents/protocol/prompt.py` — `LOG_CHANNEL_PROMPT` → `build_log_channel_prompt(browser)`.
- `src/optio_agents/protocol/session.py` — `protocol` param; install shims; inject parser into `_tail_and_dispatch`.
- `src/optio_agents/context.py` — `HookContext.browser_launch_env`.
- `src/optio_agents/prompt.py` — `compose_agents_md(documentation=…)`; drop `BASE_PROMPT_PRE`/`LOG_CHANNEL_PROMPT`.
- `src/optio_agents/protocol/__init__.py` — export `Protocol`, `get_protocol`, `BrowserMode`, `build_log_channel_prompt`.
- `src/optio_agents/__init__.py` — export `get_protocol`/`Protocol`/`BrowserMode`/`seeds`; drop `browser_capture`.
- `tests/conftest.py` — add `mongo_db` fixture (for the moved seed tests).
- `tests/test_seeds.py` — MOVED from optio-host; retarget import.
- `tests/test_browser_capture.py` → `tests/test_browser_shims.py` — retarget to `prepare_browser_shims`.
- `tests/test_prompt.py` — retarget to `build_log_channel_prompt`.
- `tests/test_protocol_parser.py` — add `recognize_browser` cases.
- `tests/test_protocol.py` — NEW: `get_protocol` per mode.
- `tests/test_client_directed_dispatch.py` — pass parser; add driver shim-install cases.
- `tests/test_package_exports.py` — assert the new top-level exports.

**optio-host — modify:**
- `src/optio_host/seeds.py` — DELETED (moved).
- `tests/test_seeds.py` — DELETED (moved).
- `tests/conftest.py` — remove the now-dead `mongo_db` fixture + its imports.

**optio-claudecode — modify:**
- `src/optio_claudecode/session.py` — retarget seed import; adopt `get_protocol(browser="redirect")`.
- `src/optio_claudecode/prompt.py` — thread `documentation`; drop `BASE_PROMPT_PRE` re-export.
- `src/optio_claudecode/seed_manifest.py` — retarget seed import + docstring.
- `tests/test_session_seed_capture.py` — retarget seed import.

**optio-opencode — modify:**
- `src/optio_opencode/session.py` — adopt `get_protocol(browser="suppress")`; merge browser env into launch.
- `src/optio_opencode/prompt.py` — thread `documentation`; default suppress docs; drop `BASE_PROMPT_PRE`/`LOG_CHANNEL_PROMPT`.
- `src/optio_opencode/host_actions.py` — delete hand-rolled suppression stubs; `launch_opencode` accepts `extra_env`.
- `tests/test_prompt.py` — assert `BROWSER:` omitted + suppress note present.

**optio-demo — modify:**
- `src/optio_demo/tasks/client_directed.py` — `get_protocol(browser="redirect")`; drop manual `browser_capture.enable`.

---

# Phase 1 — optio-agents primitives (parallel; disjoint files)

## Task 1: `browser_shims.py` (replaces `browser_capture.py`)

**Files:**
- Create: `packages/optio-agents/src/optio_agents/browser_shims.py`
- Delete: `packages/optio-agents/src/optio_agents/browser_capture.py`

- [ ] **Step 1: Create `browser_shims.py`**

```python
"""Browser-open shims for the agent launch environment, by mode.

There is no real browser on the worker. Each agent decides what should
happen when its child process tries to open one — encoded as a
``BrowserMode``:

- ``ignore``    — install nothing; the real opener (if any) runs.
- ``suppress``  — shadow the openers with silent no-op stubs.
- ``redirect``  — shadow the openers with capture stubs that append a
  ``BROWSER: "<url>"`` marker to ``optio.log`` (surfaced to the operator
  via ``ctx.request_browser_open``).

``prepare_browser_shims`` writes the stubs (if any) under
``<workdir>/bin`` and returns the env additions to merge into the agent
launch env (``BROWSER`` + a ``<workdir>/bin`` PATH prepend), or ``None``
for ``ignore``. Both returned values are absolute, so the stub wins
regardless of HOME isolation or local-vs-SSH.
"""

from __future__ import annotations

import os
from typing import Literal

from optio_host.host import Host


BrowserMode = Literal["ignore", "suppress", "redirect"]

_SHIM_NAMES = ("xdg-open", "gio", "open", "sensible-browser", "www-browser")

_SUPPRESS_BODY = "#!/bin/sh\nexit 0\n"


def _redirect_body(host: Host) -> str:
    # $1 is the URL the opener was invoked with. Quote it so the captured
    # marker is unambiguous even if the URL contains spaces.
    return (
        "#!/bin/sh\n"
        f'printf \'BROWSER: "%s"\\n\' "$1" >> {host.workdir}/optio.log\n'
        "exit 0\n"
    )


async def _write_shims(host: Host, body: str) -> dict[str, str]:
    for name in _SHIM_NAMES:
        await host.write_text(f"bin/{name}", body)
    await host.run_command(f"chmod +x {host.workdir}/bin/*")
    workdir_bin = f"{host.workdir}/bin"
    extra_path = workdir_bin + ":" + os.environ.get(
        "PATH", "/usr/local/bin:/usr/bin:/bin",
    )
    return {"BROWSER": f"{workdir_bin}/xdg-open", "PATH": extra_path}


async def prepare_browser_shims(
    host: Host, browser: BrowserMode,
) -> dict[str, str] | None:
    """Install the browser shims for ``browser`` and return env additions.

    ``ignore`` → no shims, returns ``None``. ``suppress`` → silent no-op
    stubs. ``redirect`` → capture stubs emitting the ``BROWSER:`` marker.
    """
    if browser == "ignore":
        return None
    if browser == "suppress":
        return await _write_shims(host, _SUPPRESS_BODY)
    if browser == "redirect":
        return await _write_shims(host, _redirect_body(host))
    raise ValueError(f"unknown browser mode: {browser!r}")
```

- [ ] **Step 2: Delete the old module**

```bash
cd /home/csillag/deai/optio
git rm packages/optio-agents/src/optio_agents/browser_capture.py
```

- [ ] **Step 3: Commit**

```bash
git add packages/optio-agents/src/optio_agents/browser_shims.py
git commit -m "feat(optio-agents): browser_shims with ignore/suppress/redirect modes"
```

---

## Task 2: `parser.py` — `recognize_browser` toggle

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/protocol/parser.py`

- [ ] **Step 1: Make `BROWSER:` recognition conditional**

In `packages/optio-agents/src/optio_agents/protocol/parser.py`, change the `parse_log_line` signature and the `_RE_BROWSER` block. Replace the function header:

```python
def parse_log_line(line: str) -> LogEvent:
    """Classify one line from optio.log into a LogEvent."""
```

with:

```python
def parse_log_line(line: str, *, recognize_browser: bool = True) -> LogEvent:
    """Classify one line from optio.log into a LogEvent.

    ``recognize_browser`` (default True) controls whether a ``BROWSER:``
    line yields a ``BrowserEvent``. When False (the ``ignore`` / ``suppress``
    protocol modes), a ``BROWSER:`` line falls through to ``UnknownLine`` —
    an agent cannot trigger a browser-open it has no shim for.
    """
```

And replace the `_RE_BROWSER` match block:

```python
    m = _RE_BROWSER.match(stripped)
    if m:
        return BrowserEvent(url=m.group(1))
```

with:

```python
    if recognize_browser:
        m = _RE_BROWSER.match(stripped)
        if m:
            return BrowserEvent(url=m.group(1))
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-agents/src/optio_agents/protocol/parser.py
git commit -m "feat(optio-agents): parse_log_line gains recognize_browser toggle"
```

---

## Task 3: `protocol/prompt.py` — `build_log_channel_prompt(browser)`

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/protocol/prompt.py`

- [ ] **Step 1: Replace the static prompt with a mode-aware builder**

Replace the **entire** contents of `packages/optio-agents/src/optio_agents/protocol/prompt.py` with:

```python
"""Single source of truth for the LLM-facing keyword-protocol documentation.

This module lives next to ``parser.py`` so the prose that teaches an agent
how to speak the protocol cannot drift from the regexes that enforce it.

``build_log_channel_prompt(browser)`` assembles the mode-specific block:
the ``BROWSER:`` keyword is documented only for ``redirect``; a trailing
"no browser here" paragraph is appended only for ``suppress``. All other
keywords (``STATUS:`` / ``DELIVERABLE:`` / ``DONE`` / ``ERROR`` /
``ATTENTION:`` / ``DOMAIN_MESSAGE:``) are documented in every mode.
"""

from __future__ import annotations

from optio_agents.browser_shims import BrowserMode


_HEADER = """## Log channel

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
"""

_BROWSER_BULLET = """- `BROWSER:` — ask the operator's browser to open a URL, e.g.
  `BROWSER: https://example.com/login`. Use for flows that require the
  human to visit a page (e.g. an auth/login URL).
"""

_TAIL_BULLETS = """- `ATTENTION:` — request human attention with a short reason, e.g.
  `ATTENTION: waiting for your approval`.
- `DOMAIN_MESSAGE:` — push an application-specific message: a keyword
  token followed by single-line JSON, e.g.
  `DOMAIN_MESSAGE: build-finished {"artifact":"app.zip"}`. The JSON must
  be valid and on one line; malformed JSON is dropped.
"""

_SUPPRESS_NOTE = """
In this environment, it's impossible to launch a browser, so don't try to
run `xdg-open` or similar.
"""

_RULES = """
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


def build_log_channel_prompt(browser: BrowserMode = "ignore") -> str:
    """Build the keyword-protocol documentation block for ``browser`` mode."""
    browser_bullet = _BROWSER_BULLET if browser == "redirect" else ""
    suppress_note = _SUPPRESS_NOTE if browser == "suppress" else ""
    return _HEADER + browser_bullet + _TAIL_BULLETS + suppress_note + _RULES
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-agents/src/optio_agents/protocol/prompt.py
git commit -m "feat(optio-agents): build_log_channel_prompt replaces static LOG_CHANNEL_PROMPT"
```

---

## Task 4: `protocol/protocol.py` — `Protocol` + `get_protocol`

**Files:**
- Create: `packages/optio-agents/src/optio_agents/protocol/protocol.py`

- [ ] **Step 1: Create the value object + factory**

```python
"""The Protocol value object and its factory.

``get_protocol(browser=…)`` is the single decision point binding the three
facets that vary per agent — the keyword documentation, the parser
variant, and the browser-open shim behavior — to one ``BrowserMode``. The
returned ``Protocol`` carries them so they cannot drift apart.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from optio_agents.browser_shims import BrowserMode, prepare_browser_shims
from optio_agents.protocol.parser import LogEvent, parse_log_line
from optio_agents.protocol.prompt import build_log_channel_prompt

if TYPE_CHECKING:
    from optio_host.host import Host


@dataclass(frozen=True)
class Protocol:
    """Per-agent protocol variation: docs + parser + browser-shim behavior."""

    documentation: str
    parse_log_line: Callable[[str], LogEvent]
    browser: BrowserMode

    async def prepare_browser_shims(self, host: "Host") -> dict[str, str] | None:
        """Install this mode's browser shims; return launch-env additions.

        Returns ``None`` for ``ignore`` (no shims). The session driver calls
        this after ``setup_workdir`` and stashes the result on
        ``HookContext.browser_launch_env``; the agent body merges it into
        the launched subprocess's env.
        """
        return await prepare_browser_shims(host, self.browser)


def get_protocol(*, browser: BrowserMode = "ignore") -> Protocol:
    """Build the ``Protocol`` for ``browser`` mode."""
    recognize_browser = browser == "redirect"
    return Protocol(
        documentation=build_log_channel_prompt(browser),
        parse_log_line=functools.partial(
            parse_log_line, recognize_browser=recognize_browser,
        ),
        browser=browser,
    )
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-agents/src/optio_agents/protocol/protocol.py
git commit -m "feat(optio-agents): Protocol value object + get_protocol factory"
```

---

## Task 5: `protocol/session.py` — driver takes the Protocol

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/protocol/session.py`

- [ ] **Step 1: Import `get_protocol` (and keep the parser import for the type alias)**

In `packages/optio-agents/src/optio_agents/protocol/session.py`, after the existing parser import block (ends line 40), add:

```python
from optio_agents.protocol.protocol import Protocol, get_protocol
```

- [ ] **Step 2: Add the `protocol` parameter to `run_log_protocol_session`**

Change the signature (lines 92-100) to add the parameter:

```python
async def run_log_protocol_session(
    host: Host,
    ctx: "ProcessContext",
    *,
    body: Callable[[Host, HookContext], Awaitable[None]],
    on_deliverable: DeliverableCallback | None = None,
    before_execute: HookCallback | None = None,
    after_execute: HookCallback | None = None,
    protocol: "Protocol | None" = None,
) -> None:
```

- [ ] **Step 3: Resolve the protocol and install shims after `setup_workdir`**

Replace the workdir-setup block (lines 136-144):

```python
    hook_ctx = HookContext(ctx, host)

    # Workdir + protocol artifacts. ``setup_workdir`` mkdirs the workdir
    # only; the protocol-specific deliverables/ dir + empty optio.log
    # channel are owned by the protocol driver itself.
    await host.setup_workdir()
    deliverables_dir = f"{host.workdir}/deliverables"
    await host.run_command(f"mkdir -p {deliverables_dir}")
    await host.write_text("optio.log", "")
```

with:

```python
    if protocol is None:
        protocol = get_protocol()
    hook_ctx = HookContext(ctx, host)

    # Workdir + protocol artifacts. ``setup_workdir`` mkdirs the workdir
    # only; the protocol-specific deliverables/ dir + empty optio.log
    # channel are owned by the protocol driver itself.
    await host.setup_workdir()
    deliverables_dir = f"{host.workdir}/deliverables"
    await host.run_command(f"mkdir -p {deliverables_dir}")
    await host.write_text("optio.log", "")

    # Install the per-agent browser-open shims (if any) and expose the
    # resulting launch-env additions on the HookContext. The agent body
    # merges hook_ctx.browser_launch_env into the env it launches with.
    hook_ctx.browser_launch_env = await protocol.prepare_browser_shims(host)
```

- [ ] **Step 4: Pass the protocol's parser into `_tail_and_dispatch`**

Change the `tail_task` creation (line 168-170):

```python
        tail_task = asyncio.create_task(
            _tail_and_dispatch(host, ctx, deliverable_queue, done_flag, error_flag),
        )
```

to:

```python
        tail_task = asyncio.create_task(
            _tail_and_dispatch(
                host, ctx, deliverable_queue, done_flag, error_flag,
                protocol.parse_log_line,
            ),
        )
```

- [ ] **Step 5: Make `_tail_and_dispatch` use the injected parser**

Change the `_tail_and_dispatch` signature (lines 229-235) and the parse call (line 238). Replace:

```python
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
```

with:

```python
async def _tail_and_dispatch(
    host: Host,
    ctx: "ProcessContext",
    deliverable_queue: asyncio.Queue[tuple[str, str]],
    done_flag: asyncio.Event,
    error_flag: list,
    parse_line: "Callable[[str], LogEvent]",
) -> None:
    """Consume tail_file(optio.log), parse each line, dispatch by keyword."""
    async for line in host.tail_file(f"{host.workdir}/optio.log"):
        ev: LogEvent = parse_line(line)
```

(The module-level `parse_log_line` import stays — it remains the default for `get_protocol()` and is still referenced by the contract; only `_tail_and_dispatch`'s call site changes to the injected `parse_line`.)

- [ ] **Step 6: Commit**

```bash
git add packages/optio-agents/src/optio_agents/protocol/session.py
git commit -m "feat(optio-agents): driver takes Protocol, installs shims, injects parser"
```

---

## Task 6: `context.py` — `HookContext.browser_launch_env`

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/context.py`

- [ ] **Step 1: Initialize the attribute in `__init__`**

In `packages/optio-agents/src/optio_agents/context.py`, change `HookContext.__init__` (lines 22-25):

```python
    def __init__(self, ctx, host) -> None:
        # Use object.__setattr__ to avoid __getattr__ recursion in __init__.
        object.__setattr__(self, "_ctx", ctx)
        object.__setattr__(self, "_host", host)
```

to:

```python
    def __init__(self, ctx, host) -> None:
        # Use object.__setattr__ to avoid __getattr__ recursion in __init__.
        object.__setattr__(self, "_ctx", ctx)
        object.__setattr__(self, "_host", host)
        # Set by run_log_protocol_session from the protocol's browser shims
        # (None when the mode is "ignore"). The agent body merges this into
        # the launched subprocess's env.
        object.__setattr__(self, "browser_launch_env", None)
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-agents/src/optio_agents/context.py
git commit -m "feat(optio-agents): HookContext carries browser_launch_env"
```

---

## Task 7: `prompt.py` — `compose_agents_md(documentation=…)`

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/prompt.py`

- [ ] **Step 1: Replace the module so the composer takes documentation**

Replace the **entire** contents of `packages/optio-agents/src/optio_agents/prompt.py` with:

```python
"""Shared AGENTS.md composer for optio coordination-protocol agents.

Owned by ``optio-agents`` so that ``optio-opencode`` and
``optio-claudecode`` (and any future agent package) compose the same
AGENTS.md framing. The keyword-protocol documentation is **passed in** by
the caller (built via ``optio_agents.protocol.build_log_channel_prompt`` /
``get_protocol().documentation``) so it reflects the caller's protocol
mode rather than a fixed all-keywords block.

Consumer packages stay responsible for their own resume-specific content
(if any) and pass it in via the ``resume_section`` parameter.
"""


_INTRO = """# Coordination protocol with the host (optio)

You are running inside a coordination harness. Follow these conventions
throughout the session.

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
    documentation: str,
    resume_section: str | None = None,
) -> str:
    """Build the AGENTS.md body for an optio-coordinated agent task.

    Args:
      consumer_instructions: the task author's prompt, appended verbatim
        (trailing whitespace stripped).
      documentation: the keyword-protocol documentation block for the
        caller's protocol mode (e.g. ``get_protocol(browser=…).documentation``).
      resume_section: optional pre-rendered resume-detection section to
        insert between the protocol docs and ``BASE_PROMPT_POST``.
        ``None`` (default) omits the section.
    """
    pre = _INTRO + documentation
    body = consumer_instructions.rstrip()
    resume_block = (resume_section + "\n") if resume_section else ""
    return f"{pre}\n{resume_block}{BASE_PROMPT_POST}\n{body}\n"
```

(`BASE_PROMPT_PRE` and the `LOG_CHANNEL_PROMPT` import are removed — the
pre-block now depends on the per-call `documentation`.)

- [ ] **Step 2: Commit**

```bash
git add packages/optio-agents/src/optio_agents/prompt.py
git commit -m "feat(optio-agents): compose_agents_md takes documentation, drops static pre"
```

---

## Task 8: optio-agents exports (`protocol/__init__.py` + `__init__.py`)

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/protocol/__init__.py`
- Modify: `packages/optio-agents/src/optio_agents/__init__.py`

> Note: this task adds the `seeds` export too (the `seeds.py` module arrives via Task 9's `git mv`; export order across tasks does not matter since no tests run until Phase 5).

- [ ] **Step 1: Export the protocol surface**

In `packages/optio-agents/src/optio_agents/protocol/__init__.py`, add after the `from optio_agents.protocol.session import (...)` block (line 27):

```python
from optio_agents.protocol.protocol import BrowserMode, Protocol, get_protocol
from optio_agents.protocol.prompt import build_log_channel_prompt
```

And add to `__all__` (after `"DELIVERABLE_QUEUE_BOUND",`):

```python
    # protocol variation
    "get_protocol",
    "Protocol",
    "BrowserMode",
    "build_log_channel_prompt",
```

- [ ] **Step 2: Update the top-level package exports**

In `packages/optio-agents/src/optio_agents/__init__.py`, replace the import block + `__all__` so it drops `browser_capture` and adds `browser_shims`, `seeds`, and the protocol-variation surface. Replace lines 8-45 (the imports + `__all__`) with:

```python
from optio_agents.context import HookContext, HookContextProtocol
from optio_agents.protocol import (
    DELIVERABLE_QUEUE_BOUND,
    BrowserMode,
    DeliverableCallback,
    DeliverableEvent,
    DoneEvent,
    ErrorEvent,
    HookCallback,
    LogEvent,
    Protocol,
    StatusEvent,
    UnknownLine,
    build_log_channel_prompt,
    fetch_deliverable_text,
    get_protocol,
    parse_log_line,
    relativize_deliverable_path,
    run_log_protocol_session,
    validate_deliverable_path,
)
from optio_agents import browser_shims
from optio_agents import seeds

__all__ = [
    "HookContext",
    "HookContextProtocol",
    "browser_shims",
    "seeds",
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
    "get_protocol",
    "Protocol",
    "BrowserMode",
    "build_log_channel_prompt",
]
```

(The existing `__init__.py` imports `AttentionEvent`/`BrowserEvent`/`DomainMessageEvent` only indirectly; they are still exported from `optio_agents.protocol`. They are intentionally not re-exported at the top level here — they were not in the original top-level `__all__` either.)

- [ ] **Step 3: Commit**

```bash
git add packages/optio-agents/src/optio_agents/protocol/__init__.py packages/optio-agents/src/optio_agents/__init__.py
git commit -m "feat(optio-agents): export get_protocol/Protocol/BrowserMode/browser_shims/seeds"
```

---

# Phase 2 — seed-engine relocation (parallel; independent of Phase 1)

## Task 9: Move `seeds.py` optio-host → optio-agents

**Files:**
- Move: `packages/optio-host/src/optio_host/seeds.py` → `packages/optio-agents/src/optio_agents/seeds.py`

- [ ] **Step 1: `git mv` the engine source**

```bash
cd /home/csillag/deai/optio
git mv packages/optio-host/src/optio_host/seeds.py packages/optio-agents/src/optio_agents/seeds.py
```

- [ ] **Step 2: Update the module docstring to reflect its new home**

In `packages/optio-agents/src/optio_agents/seeds.py`, replace the docstring paragraph (lines 9-16, from "Seeds are stored" through the closing `"""`):

```python
Seeds are stored in a Mongo collection `{prefix}{suffix}` (the agent
package owns `suffix`) with the encrypted blob in GridFS. Each capture
mints a new, opaque, optio-generated id (an `ObjectId` hex string).

optio-host depends on optio-core, so importing `ProcessContext` for
typing is allowed; we keep `bson`/`motor` as local/TYPE_CHECKING imports
to keep the hard import surface minimal.
"""
```

with:

```python
Seeds are stored in a Mongo collection `{prefix}{suffix}` (the agent
package owns `suffix`) with the encrypted blob in GridFS. Each capture
mints a new, opaque, optio-generated id (an `ObjectId` hex string).

This is agent-coordination work, so it lives in optio-agents; it drives
the optio-host `Host` transport (tar/extract/fetch/put). optio-agents
depends on optio-core and optio-host, so importing `ProcessContext` and
`Host` for typing is allowed; we keep `bson`/`motor` as local/
TYPE_CHECKING imports to keep the hard import surface minimal.
"""
```

(The code below — `SeedManifest`, the Mongo helpers, `capture_seed`/`merge_seed`, tar helpers, and the `TYPE_CHECKING` imports of `optio_host.host.Host` + `optio_core.context.ProcessContext` — is unchanged and stays valid.)

- [ ] **Step 3: Commit**

```bash
git add packages/optio-agents/src/optio_agents/seeds.py packages/optio-host/src/optio_host/seeds.py
git commit -m "refactor: move seed engine optio-host -> optio-agents"
```

---

## Task 10: Move the seed tests + relocate the `mongo_db` fixture

**Files:**
- Move: `packages/optio-host/tests/test_seeds.py` → `packages/optio-agents/tests/test_seeds.py`
- Modify: `packages/optio-agents/tests/conftest.py`
- Modify: `packages/optio-host/tests/conftest.py`

- [ ] **Step 1: `git mv` the test file**

```bash
cd /home/csillag/deai/optio
git mv packages/optio-host/tests/test_seeds.py packages/optio-agents/tests/test_seeds.py
```

- [ ] **Step 2: Retarget the moved test's import + docstring**

In `packages/optio-agents/tests/test_seeds.py`, change line 1 and line 9.

Line 1:

```python
"""Tests for the generic optio-agents seed engine."""
```

Line 9:

```python
from optio_agents import seeds
```

(Line 10 `from optio_host.host import LocalHost` stays.)

- [ ] **Step 3: Add the `mongo_db` fixture to the optio-agents conftest**

Replace the entire contents of `packages/optio-agents/tests/conftest.py` with:

```python
"""Shared test fixtures for optio-agents."""

import os
import shutil
import tempfile

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient


@pytest.fixture
def tmp_workdir():
    """A temporary directory that is removed after the test."""
    path = tempfile.mkdtemp(prefix="optio-agents-test-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest_asyncio.fixture
async def mongo_db():
    """Per-test MongoDB database, dropped after each test."""
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_agents_test_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()
```

- [ ] **Step 4: Remove the now-dead `mongo_db` fixture from the optio-host conftest**

Replace the entire contents of `packages/optio-host/tests/conftest.py` with:

```python
"""Shared test fixtures for optio-host."""

import shutil
import tempfile

import pytest


@pytest.fixture
def tmp_workdir():
    """A temporary directory that is removed after the test."""
    path = tempfile.mkdtemp(prefix="optio-host-test-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
```

- [ ] **Step 5: Commit**

```bash
git add packages/optio-agents/tests/test_seeds.py packages/optio-agents/tests/conftest.py packages/optio-host/tests/conftest.py
git commit -m "test: relocate seed engine tests + mongo_db fixture to optio-agents"
```

---

## Task 11: Retarget the claudecode seed adapter + seed test imports

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/seed_manifest.py`
- Modify: `packages/optio-claudecode/tests/test_session_seed_capture.py`

> The claudecode `session.py` seed import is retargeted in Task 13 (same file as the protocol adoption).

- [ ] **Step 1: Retarget `seed_manifest.py` import + docstring**

In `packages/optio-claudecode/src/optio_claudecode/seed_manifest.py`:

Change line 1 (docstring first line):

```python
"""claudecode adopter of the generic optio-agents seed engine.
```

Change line 14 (import):

```python
from optio_agents import seeds
```

(Line 15 `from optio_host.host import Host` stays.)

- [ ] **Step 2: Retarget the seed-capture test import**

In `packages/optio-claudecode/tests/test_session_seed_capture.py`, change line 19:

```python
from optio_agents import seeds
```

- [ ] **Step 3: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/seed_manifest.py packages/optio-claudecode/tests/test_session_seed_capture.py
git commit -m "refactor(optio-claudecode): import seed engine from optio-agents"
```

---

# Phase 3 — adoption (parallel; disjoint files)

## Task 12: claudecode `prompt.py` — thread `documentation`

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/prompt.py`

- [ ] **Step 1: Update imports and `compose_agents_md`**

In `packages/optio-claudecode/src/optio_claudecode/prompt.py`, replace the import block (lines 10-17):

```python
from optio_agents.prompt import (
    BASE_PROMPT_PRE,
    BASE_PROMPT_POST,
    compose_agents_md as _compose_agents_md_host,
)


__all__ = ["BASE_PROMPT_PRE", "BASE_PROMPT_POST", "compose_agents_md"]
```

with:

```python
from optio_agents.prompt import (
    BASE_PROMPT_POST,
    compose_agents_md as _compose_agents_md_host,
)
from optio_agents.protocol import build_log_channel_prompt


__all__ = ["BASE_PROMPT_POST", "compose_agents_md"]
```

Then replace `compose_agents_md` (lines 109-123):

```python
def compose_agents_md(
    consumer_instructions: str,
    *,
    workdir_exclude: list[str] | None = None,
    supports_resume: bool = True,
) -> str:
    """Render <workdir>/AGENTS.md for an optio-claudecode task.

    Renders the claudecode resume section when ``supports_resume`` is
    True and forwards everything else to the shared host composer.
    """
    resume_section = _render_resume_section(workdir_exclude) if supports_resume else None
    return _compose_agents_md_host(
        consumer_instructions, resume_section=resume_section,
    )
```

with:

```python
def compose_agents_md(
    consumer_instructions: str,
    *,
    documentation: str | None = None,
    workdir_exclude: list[str] | None = None,
    supports_resume: bool = True,
) -> str:
    """Render <workdir>/AGENTS.md for an optio-claudecode task.

    Renders the claudecode resume section when ``supports_resume`` is
    True and forwards everything else to the shared host composer.

    ``documentation`` is the keyword-protocol block; the session passes
    ``get_protocol(browser="redirect").documentation``. Defaults (for
    unit tests / standalone callers) to claudecode's ``redirect`` docs.
    """
    if documentation is None:
        documentation = build_log_channel_prompt("redirect")
    resume_section = _render_resume_section(workdir_exclude) if supports_resume else None
    return _compose_agents_md_host(
        consumer_instructions,
        documentation=documentation,
        resume_section=resume_section,
    )
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/prompt.py
git commit -m "feat(optio-claudecode): thread protocol documentation into AGENTS.md"
```

---

## Task 13: claudecode `session.py` — adopt `get_protocol("redirect")`

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py`

- [ ] **Step 1: Retarget the seed import + add protocol imports**

In `packages/optio-claudecode/src/optio_claudecode/session.py`, change line 30:

```python
from optio_agents import seeds as _seeds
```

to:

```python
from optio_agents import seeds as _seeds
from optio_agents import get_protocol
```

- [ ] **Step 2: Build the protocol once, near the top of `run_claudecode_session`**

In `run_claudecode_session`, immediately after `host: Host = _build_host(config, ctx.process_id)` (line 81), add:

```python
    protocol = get_protocol(browser="redirect")
```

- [ ] **Step 3: Pass the protocol to the driver**

Change the `run_log_protocol_session(...)` call (lines 223-228):

```python
        await run_log_protocol_session(
            host, ctx,
            body=_claudecode_body,
            on_deliverable=config.on_deliverable,
            after_execute=config.after_execute,
        )
```

to:

```python
        await run_log_protocol_session(
            host, ctx,
            body=_claudecode_body,
            on_deliverable=config.on_deliverable,
            after_execute=config.after_execute,
            protocol=protocol,
        )
```

- [ ] **Step 4: Pass the protocol documentation into `compose_agents_md`**

Change the fresh-start AGENTS.md write (lines 167-174):

```python
            await host.write_text(
                "AGENTS.md",
                compose_agents_md(
                    config.consumer_instructions,
                    workdir_exclude=config.workdir_exclude,
                    supports_resume=config.supports_resume,
                ),
            )
```

to:

```python
            await host.write_text(
                "AGENTS.md",
                compose_agents_md(
                    config.consumer_instructions,
                    documentation=protocol.documentation,
                    workdir_exclude=config.workdir_exclude,
                    supports_resume=config.supports_resume,
                ),
            )
```

- [ ] **Step 5: Merge the browser launch env into the ttyd launch**

Change the `launch_ttyd_with_claude(...)` call's `extra_env` argument (line 203 `extra_env=config.env,`). Replace the whole call (lines 197-206):

```python
        ctx.report_progress(None, "Launching claude (ttyd)…")
        handle, ttyd_port = await host_actions.launch_ttyd_with_claude(
            host,
            ttyd_path=ttyd_path,
            claude_path=claude_path,
            bind_iface=ttyd_iface,
            extra_env=config.env,
            claude_flags=claude_flags,
            ready_timeout_s=READY_TIMEOUT_S,
        )
```

with:

```python
        launch_env = {
            **(config.env or {}),
            **(hook_ctx.browser_launch_env or {}),
        }
        ctx.report_progress(None, "Launching claude (ttyd)…")
        handle, ttyd_port = await host_actions.launch_ttyd_with_claude(
            host,
            ttyd_path=ttyd_path,
            claude_path=claude_path,
            bind_iface=ttyd_iface,
            extra_env=launch_env,
            claude_flags=claude_flags,
            ready_timeout_s=READY_TIMEOUT_S,
        )
```

(Note: `_claudecode_body(host, hook_ctx)` already receives `hook_ctx`; the driver has set `hook_ctx.browser_launch_env` before the body runs.)

- [ ] **Step 6: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/session.py
git commit -m "feat(optio-claudecode): adopt get_protocol(redirect) — docs, parser, capture shims"
```

---

## Task 14: opencode `prompt.py` — thread `documentation`, default suppress

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/prompt.py`

- [ ] **Step 1: Replace the imports + constants + `compose_agents_md`**

In `packages/optio-opencode/src/optio_opencode/prompt.py`, replace the top import + constants block (lines 10-21):

```python
from optio_agents.protocol.prompt import LOG_CHANNEL_PROMPT


_OPENCODE_INTRO = """# Coordination protocol with the host (optio-opencode)

You are running inside a coordination harness. Follow these conventions
throughout the session.

"""


BASE_PROMPT_PRE = _OPENCODE_INTRO + LOG_CHANNEL_PROMPT
```

with:

```python
from optio_agents.protocol import build_log_channel_prompt


_OPENCODE_INTRO = """# Coordination protocol with the host (optio-opencode)

You are running inside a coordination harness. Follow these conventions
throughout the session.

"""
```

Then replace the `compose_agents_md` body's final return + signature (lines 119-141):

```python
def compose_agents_md(
    consumer_instructions: str,
    *,
    workdir_exclude: list[str] | None,
    supports_resume: bool = True,
) -> str:
    """Build the full AGENTS.md body.

    Parameters:
      consumer_instructions: the task author's prompt, appended verbatim.
      workdir_exclude: the snapshot exclude list for this task. Mandatory:
        callers must pass it explicitly to prevent silent desync between
        archive.py defaults and the prompt's claims about what's preserved.
        Pass None to render with the framework defaults.
      supports_resume: when False, the resume-detection section is omitted
        from the prompt. Default True.
    """
    body = consumer_instructions.rstrip()
    if supports_resume:
        resume_block = _render_resume_section(workdir_exclude) + "\n"
    else:
        resume_block = ""
    return f"{BASE_PROMPT_PRE}\n{resume_block}{BASE_PROMPT_POST}\n{body}\n"
```

with:

```python
def compose_agents_md(
    consumer_instructions: str,
    *,
    workdir_exclude: list[str] | None,
    documentation: str | None = None,
    supports_resume: bool = True,
) -> str:
    """Build the full AGENTS.md body.

    Parameters:
      consumer_instructions: the task author's prompt, appended verbatim.
      workdir_exclude: the snapshot exclude list for this task. Mandatory:
        callers must pass it explicitly to prevent silent desync between
        archive.py defaults and the prompt's claims about what's preserved.
        Pass None to render with the framework defaults.
      documentation: the keyword-protocol block; the session passes
        ``get_protocol(browser="suppress").documentation``. Defaults (for
        unit tests / standalone callers) to opencode's ``suppress`` docs.
      supports_resume: when False, the resume-detection section is omitted
        from the prompt. Default True.
    """
    if documentation is None:
        documentation = build_log_channel_prompt("suppress")
    base_prompt_pre = _OPENCODE_INTRO + documentation
    body = consumer_instructions.rstrip()
    if supports_resume:
        resume_block = _render_resume_section(workdir_exclude) + "\n"
    else:
        resume_block = ""
    return f"{base_prompt_pre}\n{resume_block}{BASE_PROMPT_POST}\n{body}\n"
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/prompt.py
git commit -m "feat(optio-opencode): thread protocol documentation, default suppress docs"
```

---

## Task 15: opencode `host_actions.py` — drop hand-rolled suppression

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/host_actions.py`

- [ ] **Step 1: Add an `extra_env` parameter to `launch_opencode`**

In `packages/optio-opencode/src/optio_opencode/host_actions.py`, change the `launch_opencode` signature (lines 325-332) to add `extra_env`:

```python
async def launch_opencode(
    host: "Host",
    password: str,
    *,
    ready_timeout_s: float = 30.0,
    opencode_executable: str = "opencode",
    hostname: str = "127.0.0.1",
    extra_env: dict[str, str] | None = None,
) -> tuple[ProcessHandle, int]:
```

- [ ] **Step 2: Update the docstring's browser paragraph**

Replace the docstring paragraph (lines 339-342):

```python
    Lays down no-op browser-opener stubs (xdg-open, gio, open,
    sensible-browser) under ``<workdir>/bin`` and prepends that
    directory to PATH so opencode's automatic browser-launch is
    suppressed.
```

with:

```python
    Browser-open suppression is handled by the optio-agents protocol
    driver (``get_protocol(browser="suppress")``), which installs no-op
    opener stubs under ``<workdir>/bin`` and returns the ``BROWSER`` /
    ``PATH`` env additions; the caller passes those in via ``extra_env``.
```

- [ ] **Step 3: Remove the stub-writing loop and inline `BROWSER=true`; merge `extra_env`**

Replace the block from the password file through the env dict (lines 353-399):

```python
    pw_file = ".opencode-password"
    await host.write_text(pw_file, password)
    await host.run_command(f"chmod 600 {host.workdir}/{pw_file}")

    # Browser-suppression bin shadow.
    for noop in ("xdg-open", "gio", "open", "sensible-browser"):
        await host.write_text(f"bin/{noop}", "#!/bin/sh\nexit 0\n")
    chmod_result = await host.run_command(f"chmod +x {host.workdir}/bin/*")
    if chmod_result.exit_code != 0:
        # Non-fatal: the noop scripts may fail to be executable, but worst
        # case opencode tries to open a browser and we just live with it.
        pass

    # Build cmd: read password from file via $(cat), set BROWSER=true,
    # cd to workdir so opencode picks up opencode.json.
    #
    # NOTE: do NOT wrap in `bash -lc` / `bash -l`. A login shell sources
    # the user's profile (~/.profile, ~/.bash_profile, /etc/profile),
    # which on most Linux installs rewrites PATH from scratch and
    # therefore wipes the workdir/bin prefix we set in `env` below. With
    # the prefix gone, the noop xdg-open / sensible-browser / gio / open
    # shadows below stop hiding the real ones and opencode succeeds at
    # opening a real browser window. opencode_executable is an absolute
    # path (resolved by ensure_opencode_installed), so login-shell PATH
    # lookup is not needed to find the binary. Let LocalHost / RemoteHost
    # launch_subprocess do the shell wrapping; we just need the env-var
    # prefix and $(cat ...) substitution, which any POSIX sh handles.
    cmd = (
        f"exec env "
        f"OPENCODE_SERVER_PASSWORD=\"$(cat {shlex.quote(host.workdir + '/' + pw_file)})\" "
        f"BROWSER=true "
        f"{opencode_executable} web --port=0 --hostname={shlex.quote(hostname)}"
    )

    # Prepend the noop-browsers bin dir to PATH via env on launch_subprocess.
    workdir_bin = f"{host.workdir}/bin"
    extra_path = workdir_bin + ":" + os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    # OPENCODE_DB must point at the same per-task db file used by the
    # subsequent export/import CLI calls. Without this, the server falls
    # back to opencode's global default db while export/import target the
    # taskdir-local file — causing snapshot capture to "Session not found"
    # against an empty file. Convention: opencode.db is a sibling of the
    # workdir under taskdir (session.py: opencode_db = f"{taskdir}/opencode.db").
    env = {
        "PATH": extra_path,
        "OPENCODE_DB": f"{host.taskdir}/opencode.db",
    }
```

with:

```python
    pw_file = ".opencode-password"
    await host.write_text(pw_file, password)
    await host.run_command(f"chmod 600 {host.workdir}/{pw_file}")

    # Build cmd: read password from file via $(cat), cd to workdir so
    # opencode picks up opencode.json. Browser suppression (the BROWSER
    # env + the <workdir>/bin PATH prepend that shadows the openers) comes
    # from the protocol driver's suppress shims, passed in via extra_env.
    #
    # NOTE: do NOT wrap in `bash -lc` / `bash -l`. A login shell sources
    # the user's profile (~/.profile, ~/.bash_profile, /etc/profile),
    # which on most Linux installs rewrites PATH from scratch and
    # therefore wipes the workdir/bin prefix carried in `env` below. With
    # the prefix gone, the suppress stubs stop hiding the real openers and
    # opencode succeeds at opening a real browser window. opencode_executable
    # is an absolute path (resolved by ensure_opencode_installed), so
    # login-shell PATH lookup is not needed to find the binary.
    cmd = (
        f"exec env "
        f"OPENCODE_SERVER_PASSWORD=\"$(cat {shlex.quote(host.workdir + '/' + pw_file)})\" "
        f"{opencode_executable} web --port=0 --hostname={shlex.quote(hostname)}"
    )

    # OPENCODE_DB must point at the same per-task db file used by the
    # subsequent export/import CLI calls. Without this, the server falls
    # back to opencode's global default db while export/import target the
    # taskdir-local file — causing snapshot capture to "Session not found"
    # against an empty file. Convention: opencode.db is a sibling of the
    # workdir under taskdir (session.py: opencode_db = f"{taskdir}/opencode.db").
    # The browser-suppression env (PATH prepend + BROWSER) comes from extra_env.
    env = {
        "OPENCODE_DB": f"{host.taskdir}/opencode.db",
        **(extra_env or {}),
    }
```

- [ ] **Step 4: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/host_actions.py
git commit -m "refactor(optio-opencode): suppression via protocol shims, launch_opencode takes extra_env"
```

---

## Task 16: opencode `session.py` — adopt `get_protocol("suppress")`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/session.py`

> Entry function is `run_opencode_session(ctx, config)` (line 70); the host is built at line 73; the body `_opencode_body(host, hook_ctx)` (line 155) receives `hook_ctx`; the `launch_opencode` call is at lines 239-244; the `run_log_protocol_session` call at ≈306; the `compose_agents_md` call at ≈172. Mirrors claudecode Task 13.

- [ ] **Step 1: Import `get_protocol`**

Add near the other `optio_agents` imports (after line 37 `from optio_opencode.prompt import compose_agents_md`):

```python
from optio_agents import get_protocol
```

- [ ] **Step 2: Build the protocol once**

In `run_opencode_session`, immediately after `host: Host = _build_host(config, ctx.process_id)` (line 73), add:

```python
    protocol = get_protocol(browser="suppress")
```

- [ ] **Step 3: Pass the protocol to the driver**

Change the `run_log_protocol_session(...)` call (≈306-311):

```python
        await run_log_protocol_session(
            host, ctx,
            body=_opencode_body,
            on_deliverable=config.on_deliverable,
            after_execute=config.after_execute,
        )
```

to:

```python
        await run_log_protocol_session(
            host, ctx,
            body=_opencode_body,
            on_deliverable=config.on_deliverable,
            after_execute=config.after_execute,
            protocol=protocol,
        )
```

- [ ] **Step 4: Pass documentation into `compose_agents_md`**

Change the AGENTS.md write (≈172-178):

```python
                compose_agents_md(
                    config.consumer_instructions,
                    workdir_exclude=config.workdir_exclude,
                    supports_resume=config.supports_resume,
                ),
```

to:

```python
                compose_agents_md(
                    config.consumer_instructions,
                    documentation=protocol.documentation,
                    workdir_exclude=config.workdir_exclude,
                    supports_resume=config.supports_resume,
                ),
```

- [ ] **Step 5: Pass the browser env into `launch_opencode`**

The `launch_opencode` call is inside `_opencode_body`, which has `hook_ctx` in scope. Change the call (lines 239-244):

```python
        handle, opencode_port = await host_actions.launch_opencode(
            host, password,
            ready_timeout_s=READY_TIMEOUT_S,
            opencode_executable=opencode_exec,
            hostname=opencode_hostname,
        )
```

to:

```python
        handle, opencode_port = await host_actions.launch_opencode(
            host, password,
            ready_timeout_s=READY_TIMEOUT_S,
            opencode_executable=opencode_exec,
            hostname=opencode_hostname,
            extra_env=hook_ctx.browser_launch_env,
        )
```

- [ ] **Step 6: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/session.py
git commit -m "feat(optio-opencode): adopt get_protocol(suppress) — docs + suppression shims"
```

---

## Task 17: demo `client_directed.py` — use `get_protocol("redirect")`

**Files:**
- Modify: `packages/optio-demo/src/optio_demo/tasks/client_directed.py`

- [ ] **Step 1: Replace the manual `browser_capture.enable` with the protocol**

In `packages/optio-demo/src/optio_demo/tasks/client_directed.py`, change the import (line 22):

```python
from optio_agents import run_log_protocol_session, get_protocol
```

Then replace `_open_browser_via_tool` (lines 34-57):

```python
async def _open_browser_via_tool(ctx) -> None:
    """Host-bridge capture test: run a Python opener under capture shims."""
    taskdir = f"/tmp/optio-demo-browser-{os.getpid()}-{ctx.process_id}"
    os.makedirs(taskdir, exist_ok=True)
    host = LocalHost(taskdir=taskdir)
    os.makedirs(host.workdir, exist_ok=True)

    async def body(host, hook_ctx) -> None:
        env_add = await browser_capture.enable(host)
        # A trivial opener: webbrowser.open routes through xdg-open (our shim),
        # which appends the BROWSER: marker to optio.log. Then signal DONE.
        script = (
            "import webbrowser; "
            f"webbrowser.open({OPTIO_REPO_URL!r}); "
        )
        await host.run_command(
            f"python3 -c {script!r}",
            env=env_add,
            cwd=host.workdir,
        )
        # The shim has appended BROWSER: by now; close out the session.
        await host.run_command(f"echo DONE >> {host.workdir}/optio.log")

    await run_log_protocol_session(host, ctx, body=body)
```

with:

```python
async def _open_browser_via_tool(ctx) -> None:
    """Host-bridge capture test: run a Python opener under capture shims."""
    taskdir = f"/tmp/optio-demo-browser-{os.getpid()}-{ctx.process_id}"
    os.makedirs(taskdir, exist_ok=True)
    host = LocalHost(taskdir=taskdir)
    os.makedirs(host.workdir, exist_ok=True)

    async def body(host, hook_ctx) -> None:
        # The driver installed the redirect (capture) shims before the body
        # ran and exposed their env on hook_ctx.browser_launch_env. A trivial
        # opener: webbrowser.open routes through xdg-open (the shim), which
        # appends the BROWSER: marker to optio.log. Then signal DONE.
        script = (
            "import webbrowser; "
            f"webbrowser.open({OPTIO_REPO_URL!r}); "
        )
        await host.run_command(
            f"python3 -c {script!r}",
            env=hook_ctx.browser_launch_env,
            cwd=host.workdir,
        )
        # The shim has appended BROWSER: by now; close out the session.
        await host.run_command(f"echo DONE >> {host.workdir}/optio.log")

    await run_log_protocol_session(
        host, ctx, body=body, protocol=get_protocol(browser="redirect"),
    )
```

- [ ] **Step 2: Update the module docstring's stale `browser_capture.enable` mention**

In the same file, change the docstring line (line 9) referring to `browser_capture.enable`:

```python
    optio-agents session driver with ``get_protocol(browser="redirect")`` —
```

- [ ] **Step 3: Commit**

```bash
git add packages/optio-demo/src/optio_demo/tasks/client_directed.py
git commit -m "feat(optio-demo): client-directed browser demo uses get_protocol(redirect)"
```

---

# Phase 4 — tests (parallel; disjoint files)

## Task 18: `test_browser_shims.py` (replaces `test_browser_capture.py`)

**Files:**
- Create: `packages/optio-agents/tests/test_browser_shims.py`
- Delete: `packages/optio-agents/tests/test_browser_capture.py`

- [ ] **Step 1: Create the new test**

```python
"""prepare_browser_shims: ignore=no shims, suppress=silent, redirect=capture."""

import os
import subprocess

import pytest

from optio_host.host import LocalHost
from optio_agents.browser_shims import prepare_browser_shims
from optio_agents.protocol.parser import BrowserEvent, parse_log_line


_SHIM_NAMES = ("xdg-open", "gio", "open", "sensible-browser", "www-browser")


@pytest.mark.asyncio
async def test_ignore_installs_nothing_and_returns_none(tmp_path):
    host = LocalHost(taskdir=str(tmp_path))
    await host.setup_workdir()
    assert await prepare_browser_shims(host, "ignore") is None
    assert not os.path.isdir(os.path.join(host.workdir, "bin"))


@pytest.mark.asyncio
async def test_suppress_writes_silent_stubs_and_env(tmp_path):
    host = LocalHost(taskdir=str(tmp_path))
    await host.setup_workdir()
    await host.write_text("optio.log", "")
    env_add = await prepare_browser_shims(host, "suppress")

    assert env_add["BROWSER"].endswith("/bin/xdg-open")
    assert env_add["PATH"].startswith(f"{host.workdir}/bin:")
    for name in _SHIM_NAMES:
        shim = os.path.join(host.workdir, "bin", name)
        assert os.path.isfile(shim)
        assert os.access(shim, os.X_OK)

    # The suppress stub exits 0 and writes nothing.
    subprocess.run([os.path.join(host.workdir, "bin", "xdg-open"),
                    "https://example.com"], check=True)
    log = open(os.path.join(host.workdir, "optio.log")).read()
    assert "BROWSER:" not in log


@pytest.mark.asyncio
async def test_redirect_captures_browser_marker_end_to_end(tmp_path):
    host = LocalHost(taskdir=str(tmp_path))
    await host.setup_workdir()
    await host.write_text("optio.log", "")
    env_add = await prepare_browser_shims(host, "redirect")

    assert env_add["BROWSER"].endswith("/bin/xdg-open")
    assert env_add["PATH"].startswith(f"{host.workdir}/bin:")

    subprocess.run([os.path.join(host.workdir, "bin", "xdg-open"),
                    "https://example.com/login"], check=True)
    log = open(os.path.join(host.workdir, "optio.log")).read()
    lines = [ln for ln in log.splitlines() if ln.startswith("BROWSER:")]
    assert len(lines) == 1
    ev = parse_log_line(lines[0])
    assert isinstance(ev, BrowserEvent)
    assert ev.url == '"https://example.com/login"'
```

- [ ] **Step 2: Delete the old test**

```bash
cd /home/csillag/deai/optio
git rm packages/optio-agents/tests/test_browser_capture.py
```

- [ ] **Step 3: Commit**

```bash
git add packages/optio-agents/tests/test_browser_shims.py
git commit -m "test(optio-agents): browser_shims three-mode coverage"
```

---

## Task 19: `test_prompt.py` — retarget to `build_log_channel_prompt`

**Files:**
- Modify: `packages/optio-agents/tests/test_prompt.py`

- [ ] **Step 1: Replace the test module**

Replace the **entire** contents of `packages/optio-agents/tests/test_prompt.py` with:

```python
"""Tests for the mode-aware keyword-protocol prompt builder."""

from optio_agents.protocol.prompt import build_log_channel_prompt


def test_core_keywords_present_in_every_mode():
    for browser in ("ignore", "suppress", "redirect"):
        block = build_log_channel_prompt(browser)
        for kw in ("STATUS:", "DELIVERABLE:", "DONE", "ERROR",
                   "ATTENTION:", "DOMAIN_MESSAGE:"):
            assert kw in block, (browser, kw)


def test_browser_keyword_only_in_redirect():
    assert "BROWSER:" in build_log_channel_prompt("redirect")
    assert "BROWSER:" not in build_log_channel_prompt("ignore")
    assert "BROWSER:" not in build_log_channel_prompt("suppress")


def test_suppress_trailing_note_only_in_suppress():
    note = "impossible to launch a browser"
    assert note in build_log_channel_prompt("suppress")
    assert note not in build_log_channel_prompt("ignore")
    assert note not in build_log_channel_prompt("redirect")


def test_block_mentions_log_and_deliverables_paths():
    block = build_log_channel_prompt()
    assert "./optio.log" in block
    assert "./deliverables/" in block


def test_block_states_trailing_newline_requirement():
    block = build_log_channel_prompt()
    assert "newline" in block
    assert "tail -c 1" in block


def test_block_has_log_channel_and_deliverables_sections():
    block = build_log_channel_prompt()
    assert "## Log channel" in block
    assert "## Deliverables" in block


def test_block_is_framing_neutral():
    assert "optio-opencode" not in build_log_channel_prompt()
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-agents/tests/test_prompt.py
git commit -m "test(optio-agents): build_log_channel_prompt per-mode assertions"
```

---

## Task 20: `test_protocol_parser.py` — `recognize_browser` cases

**Files:**
- Modify: `packages/optio-agents/tests/test_protocol_parser.py`

- [ ] **Step 1: Append the toggle cases**

Append to `packages/optio-agents/tests/test_protocol_parser.py`:

```python
# ---- recognize_browser toggle ----

def test_browser_recognized_by_default():
    ev = parse_log_line("BROWSER: https://example.com")
    assert isinstance(ev, BrowserEvent)


def test_browser_recognized_when_enabled():
    ev = parse_log_line("BROWSER: https://example.com", recognize_browser=True)
    assert isinstance(ev, BrowserEvent)


def test_browser_falls_through_to_unknown_when_disabled():
    ev = parse_log_line("BROWSER: https://example.com", recognize_browser=False)
    assert isinstance(ev, UnknownLine)
    assert ev.text == "BROWSER: https://example.com"


def test_attention_still_recognized_when_browser_disabled():
    ev = parse_log_line("ATTENTION: look", recognize_browser=False)
    assert isinstance(ev, AttentionEvent)
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-agents/tests/test_protocol_parser.py
git commit -m "test(optio-agents): parse_log_line recognize_browser toggle"
```

---

## Task 21: `test_protocol.py` — `get_protocol` per mode (NEW)

**Files:**
- Create: `packages/optio-agents/tests/test_protocol.py`

- [ ] **Step 1: Create the test**

```python
"""get_protocol binds documentation + parser + browser mode together."""

import pytest

from optio_agents import get_protocol
from optio_agents.protocol.parser import BrowserEvent, UnknownLine


def test_browser_field_matches_request():
    for browser in ("ignore", "suppress", "redirect"):
        assert get_protocol(browser=browser).browser == browser


def test_default_is_ignore():
    assert get_protocol().browser == "ignore"


def test_documentation_reflects_mode():
    assert "BROWSER:" in get_protocol(browser="redirect").documentation
    assert "BROWSER:" not in get_protocol(browser="ignore").documentation
    assert "impossible to launch a browser" in get_protocol(browser="suppress").documentation


def test_parser_reflects_mode():
    redirect = get_protocol(browser="redirect")
    suppress = get_protocol(browser="suppress")
    assert isinstance(redirect.parse_log_line("BROWSER: https://x"), BrowserEvent)
    assert isinstance(suppress.parse_log_line("BROWSER: https://x"), UnknownLine)
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-agents/tests/test_protocol.py
git commit -m "test(optio-agents): get_protocol per-mode binding"
```

---

## Task 22: `test_client_directed_dispatch.py` — parser injection + driver shim install

**Files:**
- Modify: `packages/optio-agents/tests/test_client_directed_dispatch.py`

- [ ] **Step 1: Update the existing dispatch test to pass a parser**

In `packages/optio-agents/tests/test_client_directed_dispatch.py`, change the import (line 5) and the `_tail_and_dispatch` call (line 52).

Line 5:

```python
from optio_agents.protocol.session import _tail_and_dispatch
from optio_agents import get_protocol
```

The call (line 52):

```python
    await _tail_and_dispatch(
        host, ctx, asyncio.Queue(), done, [],
        get_protocol(browser="redirect").parse_log_line,
    )
```

- [ ] **Step 2: Append a test that the suppress parser does NOT route BROWSER**

Append to the same file:

```python
@pytest.mark.asyncio
async def test_dispatch_ignores_browser_under_suppress():
    host = _FakeHost([
        "BROWSER: https://x\n",
        "ATTENTION: help me\n",
        "DONE\n",
    ])
    ctx = _FakeCtx()
    import asyncio
    done = asyncio.Event()
    await _tail_and_dispatch(
        host, ctx, asyncio.Queue(), done, [],
        get_protocol(browser="suppress").parse_log_line,
    )
    assert ctx.browser == []                 # BROWSER line was inert (UnknownLine)
    assert ctx.attention == ["help me"]      # ATTENTION still routed
    assert done.is_set()
```

- [ ] **Step 3: Commit**

```bash
git add packages/optio-agents/tests/test_client_directed_dispatch.py
git commit -m "test(optio-agents): dispatch uses injected parser; suppress ignores BROWSER"
```

---

## Task 23: `test_package_exports.py` — assert the variation surface

**Files:**
- Modify: `packages/optio-agents/tests/test_package_exports.py`

- [ ] **Step 1: Append an export assertion**

Append to `packages/optio-agents/tests/test_package_exports.py`:

```python
def test_top_level_exports_protocol_variation():
    import optio_agents
    for name in ("get_protocol", "Protocol", "BrowserMode",
                 "build_log_channel_prompt", "browser_shims", "seeds"):
        assert hasattr(optio_agents, name), name
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-agents/tests/test_package_exports.py
git commit -m "test(optio-agents): assert get_protocol/seeds/browser_shims exports"
```

---

## Task 24: opencode `test_prompt.py` — assert suppress docs shape

**Files:**
- Modify: `packages/optio-opencode/tests/test_prompt.py`

- [ ] **Step 1: Append suppress-mode assertions**

Append to `packages/optio-opencode/tests/test_prompt.py`:

```python
def test_opencode_docs_omit_browser_keyword():
    """opencode suppresses browser-opens, so it must NOT advertise BROWSER:."""
    out = _compose()
    assert "BROWSER:" not in out


def test_opencode_docs_include_suppress_note():
    out = _compose()
    assert "impossible to launch a browser" in out
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-opencode/tests/test_prompt.py
git commit -m "test(optio-opencode): docs omit BROWSER:, include suppress note"
```

---

# Phase 5 — VERIFICATION (run everything, once)

> Only now do we run tests + guards. All require a MongoDB reachable at
> `MONGO_URL` (default `mongodb://localhost:27017`) for the seed/session
> suites — start it via Docker per the project convention.

## Task 25: Full verification sweep

**Files:** none (verification only).

- [ ] **Step 1: optio-agents suite**

Run: `cd /home/csillag/deai/optio/packages/optio-agents && python -m pytest -q`
Expected: PASS. Covers `test_seeds.py` (moved), `test_browser_shims.py`, `test_prompt.py`, `test_protocol_parser.py`, `test_protocol.py`, `test_client_directed_dispatch.py`, `test_package_exports.py`, plus the unchanged protocol/context/download tests.

- [ ] **Step 2: optio-host suite**

Run: `cd /home/csillag/deai/optio/packages/optio-host && python -m pytest -q`
Expected: PASS (conftest still valid after the `mongo_db` removal; no test references the moved seed engine).

- [ ] **Step 3: optio-claudecode suite**

Run: `cd /home/csillag/deai/optio/packages/optio-claudecode && python -m pytest -q`
Expected: PASS. Seed tests resolve `seeds` from optio-agents; `compose_agents_md` renders `redirect` docs (with `BROWSER:`); existing prompt/resume/session tests pass via the `documentation` default.

- [ ] **Step 4: optio-opencode suite**

Run: `cd /home/csillag/deai/optio/packages/optio-opencode && python -m pytest -q`
Expected: PASS. Prompt tests now assert `BROWSER:` absent + suppress note present; session tests pass with suppression via the protocol driver + `launch_opencode(extra_env=…)`.

- [ ] **Step 5: optio-demo import sanity**

Run: `cd /home/csillag/deai/optio/packages/optio-demo && python -c "import optio_demo.tasks.client_directed as m; print('OK')"`
Expected: prints `OK`.

- [ ] **Step 6: Grep guards — no stale references anywhere**

Run:
```bash
cd /home/csillag/deai/optio
! grep -rn "optio_host import seeds\|optio_host\.seeds" packages/ --include=*.py \
  && ! grep -rn "browser_capture" packages/ --include=*.py \
  && ! grep -rn "LOG_CHANNEL_PROMPT\|BASE_PROMPT_PRE" packages/ --include=*.py \
  && test ! -e packages/optio-host/src/optio_host/seeds.py \
  && echo CLEAN
```
Expected: prints `CLEAN`. (No module imports the moved seed engine from optio-host; no `browser_capture` references survive; the removed `LOG_CHANNEL_PROMPT` / `BASE_PROMPT_PRE` symbols are gone.)

- [ ] **Step 7: Final commit (if any verification fixups were needed)**

If Steps 1-6 surfaced a fixup, apply it, re-run the affected suite, then:
```bash
git add -A
git commit -m "fix: phase-3 verification fixups"
```
If everything passed clean, there is nothing to commit here.

---

## Spec coverage check

**Protocol-variation spec** (`docs/2026-05-29-optio-protocol-variation-design.md`):
- Public surface `get_protocol` / `Protocol` / `BrowserMode` → Tasks 4, 8, 21.
- Mode semantics table (docs/parser/shim per mode + suppress note) → Tasks 1, 2, 3, 18, 19, 20.
- Driver integration (parser + shim install + `protocol` param) → Task 5; `HookContext.browser_launch_env` → Task 6.
- A1 env delivery (merge at launch site) → Tasks 13 (claudecode), 16 (opencode), 17 (demo).
- Shim unification (`browser_shims.py`, opencode suppression deleted) → Tasks 1, 15.
- Docs param threading (`compose_agents_md(documentation=…)`) → Tasks 7, 12, 14.
- Adoption: claudecode `redirect`, opencode `suppress`, demo `redirect` → Tasks 13, 16, 17.
- Fix advertise-but-suppress bug → Tasks 14, 24.
- PATH robustness (absolute prepend/BROWSER) → Task 1 (`_write_shims`).

**Seed spec phase-3 delta** (engine relocation): `optio_host/seeds.py` → `optio_agents/seeds.py` → Tasks 9, 10, 11 (+ export in Task 8). The seed feature's behavior is unchanged; the `browser_capture` config flag is **not** added (superseded — Tasks 13/14/15/17 implement the protocol model instead).
