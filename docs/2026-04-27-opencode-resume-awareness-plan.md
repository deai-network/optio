# Opencode Resume-Awareness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit, in-band resume-detection to opencode tasks via a `<workdir>/resume.log` file and a new framework prompt section, plus a `supports_resume: bool = True` opt-out on `OpencodeTaskConfig` that disables snapshot capture, the resume.log write, and the prompt section consistently.

**Architecture:** Three orthogonal-but-coupled pieces. (1) New `supports_resume` field on `OpencodeTaskConfig` (default True) plumbed through `create_opencode_task` to the existing `TaskInstance.supports_resume`. (2) A new framework section in `compose_agents_md` that teaches the agent to read `./resume.log` on every user message, treating new lines as resume signals — with the actual `workdir_exclude` patterns inlined so the agent's mental model matches what's actually preserved by snapshots. (3) A new step in `run_opencode_session` that appends an ISO 8601 timestamp to `<workdir>/resume.log` on each launch (fresh or resumed); plus gating of snapshot capture and the prompt section on `config.supports_resume`.

**Tech Stack:** Python 3.11+, existing optio-opencode patterns (asyncio, asyncssh for RemoteHost, pytest + pytest-asyncio + motor for tests).

**Spec:** `docs/2026-04-27-opencode-resume-awareness-design.md`.

---

## Plan-level notes

### Files affected

- **Modify:** `packages/optio-opencode/src/optio_opencode/types.py` — add `supports_resume: bool = True` to `OpencodeTaskConfig`.
- **Modify:** `packages/optio-opencode/src/optio_opencode/prompt.py` — split `BASE_PROMPT` into pre/post halves, add `RESUME_SECTION_TEMPLATE` + `_render_resume_section` helper, change `compose_agents_md` signature.
- **Modify:** `packages/optio-opencode/src/optio_opencode/session.py` — `create_opencode_task` reads `config.supports_resume`; `compose_agents_md` call site gets new args; new `_append_resume_log_entry` step; snapshot capture gated.
- **Modify:** `packages/optio-opencode/AGENTS.md` — document the new field and the resume.log mechanic.
- **Append:** `packages/optio-opencode/tests/test_prompt.py` — new tests for the resume section + signature requirements.
- **Modify:** `packages/optio-opencode/tests/test_prompt.py` — existing tests rewritten to match the new signature (no more `BASE_PROMPT` constant export).
- **Append:** `packages/optio-opencode/tests/test_types.py` — new tests for the `supports_resume` field.
- **Modify:** `packages/optio-opencode/tests/test_sanity.py` — new test for opt-out path.
- **Append:** `packages/optio-opencode/tests/test_session_local.py` — new tests for `resume.log` writes + opt-out behavior.
- **Modify:** `packages/optio-opencode/tests/test_session_resume.py` — augment one existing test to assert `resume.log` grows by one line per resume.

### Test conventions (already in use)

- `tmp_workdir` fixture in `tests/conftest.py` — temp dir, removed after test.
- `mongo_db` fixture in `tests/conftest.py` — fresh DB per test.
- `ctx_and_captures` fixture in `test_session_local.py` — real `ProcessContext` + `Captured` dataclass with progress/widget recordings.
- `LocalHost(taskdir=...)` with `opencode_cmd=[sys.executable, FAKE_OPENCODE]` for fake-opencode tests.
- `pytestmark = pytest.mark.asyncio` at module level for async tests.

### Don't introduce

- No `Co-Authored-By` lines in commit messages (per user preference).
- No `BASE_PROMPT` constant export — it's gone after Task 2. Existing tests that referenced it must be rewritten.
- No new `Host` protocol method for the resume-log append — the existing `Host.run_command` is sufficient (`echo TS >> path`).

### Verification cheat-sheet

- Single test file: `cd packages/optio-opencode && pytest tests/test_prompt.py -v`
- All optio-opencode tests, skipping pre-existing flake: `cd packages/optio-opencode && pytest --deselect tests/test_session_remote.py::test_remote_happy_path -q`
- Single test by name: `cd packages/optio-opencode && pytest tests/test_prompt.py::test_compose_agents_md_includes_resume_section_by_default -v`

---

## Task 1: Add `supports_resume` to `OpencodeTaskConfig` + plumb through `create_opencode_task`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/types.py`
- Modify: `packages/optio-opencode/src/optio_opencode/session.py` (`create_opencode_task`)
- Modify: `packages/optio-opencode/tests/test_types.py` (append tests)
- Modify: `packages/optio-opencode/tests/test_sanity.py` (append one test)

- [ ] **Step 1: Append failing tests to `test_types.py`**

```python
def test_opencode_task_config_supports_resume_default_true():
    cfg = OpencodeTaskConfig(consumer_instructions="x")
    assert cfg.supports_resume is True


def test_opencode_task_config_supports_resume_can_be_disabled():
    cfg = OpencodeTaskConfig(consumer_instructions="x", supports_resume=False)
    assert cfg.supports_resume is False
```

- [ ] **Step 2: Append failing test to `test_sanity.py`**

```python
def test_create_opencode_task_supports_resume_off():
    from optio_opencode import create_opencode_task, OpencodeTaskConfig
    task = create_opencode_task(
        process_id="demo-noresume", name="DemoNoResume",
        config=OpencodeTaskConfig(consumer_instructions="hi", supports_resume=False),
    )
    assert task.supports_resume is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd packages/optio-opencode && pytest tests/test_types.py tests/test_sanity.py -v`
Expected: 3 failures — `AttributeError: ... 'supports_resume'` on the new tests.

- [ ] **Step 4: Add the field to `OpencodeTaskConfig`**

In `packages/optio-opencode/src/optio_opencode/types.py`, modify the dataclass:

```python
@dataclass
class OpencodeTaskConfig:
    """Configuration for one optio-opencode task instance."""
    consumer_instructions: str
    opencode_config: dict[str, Any] = field(default_factory=dict)
    ssh: SSHConfig | None = None
    on_deliverable: DeliverableCallback | None = None
    install_if_missing: bool = True
    workdir_exclude: list[str] | None = None
    supports_resume: bool = True
    before_execute: HookCallback | None = None
    after_execute: HookCallback | None = None
```

- [ ] **Step 5: Plumb through `create_opencode_task`**

In `packages/optio-opencode/src/optio_opencode/session.py`, replace `supports_resume=True,` (the hardcoded line at ~632) with:

```python
        supports_resume=config.supports_resume,
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_types.py tests/test_sanity.py -v`
Expected: All pass — including the existing `test_create_opencode_task_declares_resume_support` (still passes because the default is `True`).

- [ ] **Step 7: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/types.py packages/optio-opencode/src/optio_opencode/session.py packages/optio-opencode/tests/test_types.py packages/optio-opencode/tests/test_sanity.py
git commit -m "feat(optio-opencode): OpencodeTaskConfig.supports_resume + plumb through create_opencode_task"
```

---

## Task 2: Refactor `compose_agents_md` signature — mandatory `workdir_exclude`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/prompt.py`
- Modify: `packages/optio-opencode/src/optio_opencode/session.py` (call site)
- Modify: `packages/optio-opencode/tests/test_prompt.py` (rewrite existing tests)

This task changes `compose_agents_md`'s signature. **No resume section yet** — that comes in Task 3. After this task, the function takes mandatory `workdir_exclude` and optional `supports_resume=True`, but neither parameter affects the output yet (both ignored internally). The point is to lock in the new signature so subsequent tasks add behavior without breaking callers.

- [ ] **Step 1: Rewrite `test_prompt.py` to use the new signature**

Replace the entire contents of `packages/optio-opencode/tests/test_prompt.py` with:

```python
"""Tests for prompt composition."""

import pytest

from optio_opencode.prompt import compose_agents_md


def _compose(consumer="say hi", workdir_exclude=None, supports_resume=True):
    """Helper: call compose_agents_md with the new mandatory args."""
    return compose_agents_md(
        consumer,
        workdir_exclude=workdir_exclude,
        supports_resume=supports_resume,
    )


def test_base_prompt_contains_all_keywords():
    out = _compose()
    for kw in ("STATUS:", "DELIVERABLE:", "DONE", "ERROR"):
        assert kw in out


def test_base_prompt_mentions_log_and_deliverables_paths():
    out = _compose()
    assert "./optio.log" in out
    assert "./deliverables/" in out


def test_base_prompt_contains_task_framing():
    out = _compose()
    assert "## Task" in out
    assert "ask questions and dialogue with the human" in out


def test_compose_agents_md_appends_consumer_instructions_verbatim():
    out = _compose("please compute 2 + 2")
    assert out.endswith("please compute 2 + 2\n")


def test_compose_agents_md_empty_consumer_still_ends_cleanly():
    out = _compose("")
    assert out.endswith("\n")


def test_compose_agents_md_workdir_exclude_required():
    """workdir_exclude is mandatory — calling without it raises TypeError."""
    with pytest.raises(TypeError):
        compose_agents_md("hi")  # type: ignore[call-arg]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-opencode && pytest tests/test_prompt.py -v`
Expected: All fail — `TypeError: compose_agents_md() got an unexpected keyword argument 'workdir_exclude'` (the function doesn't yet accept the new args).

- [ ] **Step 3: Replace `prompt.py` with the new signature**

Replace the contents of `packages/optio-opencode/src/optio_opencode/prompt.py` with:

```python
"""System-prompt composition for optio-opencode.

The base prompt teaches opencode (via AGENTS.md) how to coordinate with the
host harness: which log file to append status/deliverable/done/error lines
to, where to put deliverable files, and how the human expects to be
addressed. The consumer's own task description is then appended verbatim.
"""


BASE_PROMPT_PRE = """# Coordination protocol with the host (optio-opencode)

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
    # Resume section landing here in Task 3.
    return f"{BASE_PROMPT_PRE}\n{BASE_PROMPT_POST}\n{body}\n"
```

- [ ] **Step 4: Update the call site in `session.py`**

In `packages/optio-opencode/src/optio_opencode/session.py`, find the existing call to `compose_agents_md` (search for `compose_agents_md(`) and update it to:

```python
            await host.write_text(
                "AGENTS.md",
                compose_agents_md(
                    config.consumer_instructions,
                    workdir_exclude=config.workdir_exclude,
                ),
            )
```

(`supports_resume` is left to the default `True` for now — Task 5 will plumb it through explicitly.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_prompt.py -v`
Expected: All pass.

Run also: `cd packages/optio-opencode && pytest tests/test_session_local.py -v`
Expected: All pass — the call site change is backward-compatible (default supports_resume keeps the old behavior).

- [ ] **Step 6: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/prompt.py packages/optio-opencode/src/optio_opencode/session.py packages/optio-opencode/tests/test_prompt.py
git commit -m "refactor(optio-opencode): compose_agents_md — mandatory workdir_exclude param"
```

---

## Task 3: Render resume section conditionally — default excludes path

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/prompt.py`
- Modify: `packages/optio-opencode/tests/test_prompt.py` (append tests)

- [ ] **Step 1: Append failing tests**

Append to `packages/optio-opencode/tests/test_prompt.py`:

```python
def test_compose_agents_md_includes_resume_section_by_default():
    """Default supports_resume=True → resume section is present."""
    out = _compose()
    assert "## Resumes" in out
    assert "resume.log" in out


def test_compose_agents_md_omits_resume_section_when_supports_resume_false():
    """supports_resume=False → resume section is absent."""
    out = _compose(supports_resume=False)
    assert "## Resumes" not in out
    assert "resume.log" not in out


def test_compose_agents_md_renders_default_excludes_when_none():
    """workdir_exclude=None → prompt mentions DEFAULT_WORKDIR_EXCLUDES patterns."""
    from optio_opencode.archive import DEFAULT_WORKDIR_EXCLUDES
    out = _compose(workdir_exclude=None, supports_resume=True)
    for pattern in DEFAULT_WORKDIR_EXCLUDES:
        assert f"`{pattern}`" in out


def test_compose_agents_md_resume_section_between_deliverables_and_task():
    """Resume section sits between Deliverables and Task in the rendered prompt."""
    out = _compose()
    deliverables_pos = out.index("## Deliverables")
    resumes_pos = out.index("## Resumes")
    task_pos = out.index("## Task")
    assert deliverables_pos < resumes_pos < task_pos
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-opencode && pytest tests/test_prompt.py -v -k "resume_section or default_excludes or between_deliverables"`
Expected: 4 failures — the `## Resumes` section doesn't exist yet.

- [ ] **Step 3: Add `RESUME_SECTION_TEMPLATE` and `_render_resume_section`**

In `packages/optio-opencode/src/optio_opencode/prompt.py`, add this constant and helper between `BASE_PROMPT_POST` and `compose_agents_md`:

```python
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

Each session start (fresh or resumed) appends one ISO 8601 timestamp
to `./resume.log`. The very first line is the original launch
timestamp; each subsequent line is a resume.

**At the start of every new incoming user message, read
`./resume.log` first.** Compare the latest line to the value you
remembered last time you checked. If a new line has appeared, treat
the situation as a resume:

- Verify any tools, processes, or files you previously gathered
  outside the workdir are still where you left them.
- Re-establish anything that's gone (re-launch a server, re-fetch a
  file, etc.) before continuing.
- Then resume the work you were doing.

If a resume slips past unnoticed, a failing tool call is the
next-best signal — re-check `./resume.log` then.
"""


def _render_resume_section(workdir_exclude: list[str] | None) -> str:
    """Render the RESUME_SECTION_TEMPLATE with the effective exclude list."""
    from optio_opencode.archive import DEFAULT_WORKDIR_EXCLUDES
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
```

- [ ] **Step 4: Integrate the resume section into `compose_agents_md`**

Replace the body of `compose_agents_md` with:

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

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_prompt.py -v`
Expected: All tests pass (existing 6 + 4 new = 10).

- [ ] **Step 6: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/prompt.py packages/optio-opencode/tests/test_prompt.py
git commit -m "feat(optio-opencode): conditional resume section in compose_agents_md"
```

---

## Task 4: Resume section — custom and empty exclude list cases

**Files:**
- Modify: `packages/optio-opencode/tests/test_prompt.py` (append tests)

The `_render_resume_section` helper from Task 3 already handles custom and empty `workdir_exclude` lists. This task locks in those behaviors with explicit tests.

- [ ] **Step 1: Append failing tests**

Append to `packages/optio-opencode/tests/test_prompt.py`:

```python
def test_compose_agents_md_renders_custom_excludes():
    """workdir_exclude=[...] → prompt lists those patterns and NOT defaults."""
    from optio_opencode.archive import DEFAULT_WORKDIR_EXCLUDES
    out = _compose(workdir_exclude=["custom_a", "custom_b"])
    assert "`custom_a`" in out
    assert "`custom_b`" in out
    # None of the default patterns should appear.
    for pattern in DEFAULT_WORKDIR_EXCLUDES:
        assert f"`{pattern}`" not in out


def test_compose_agents_md_empty_excludes_renders_no_paths_excluded_copy():
    """workdir_exclude=[] → 'No paths are excluded' wording."""
    out = _compose(workdir_exclude=[])
    assert "No paths are excluded" in out
    # The 'inside an excluded subdirectory' clause should be absent (it's
    # only relevant when there are exclusions to live inside).
    assert "inside an excluded subdirectory" not in out
```

- [ ] **Step 2: Run tests to verify behavior**

Run: `cd packages/optio-opencode && pytest tests/test_prompt.py -v -k "custom_excludes or empty_excludes"`
Expected: Both pass — the `_render_resume_section` helper from Task 3 already implements the branches.

(If they fail, recheck Task 3's helper implementation.)

- [ ] **Step 3: Commit**

```bash
git add packages/optio-opencode/tests/test_prompt.py
git commit -m "test(optio-opencode): lock in custom + empty workdir_exclude rendering"
```

---

## Task 5: Session — `_append_resume_log_entry` helper + plumb `supports_resume` to `compose_agents_md`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/session.py`
- Append: `packages/optio-opencode/tests/test_session_local.py`

This task adds two coupled changes to `session.py`:
1. Plumb `config.supports_resume` explicitly into the `compose_agents_md` call (so opt-out tasks omit the resume section in their prompt).
2. New `_append_resume_log_entry(host)` helper called once per session — gated on `config.supports_resume` — between the fresh-vs-resume branch and the binary install.

- [ ] **Step 1: Append failing tests to `test_session_local.py`**

The session pipeline cleans up the taskdir on completion, so we can't reliably read `resume.log` after a full session run. Instead, two test pairs:
1. **Direct helper tests** (`test_append_resume_log_entry_*`): exercise the helper against a `LocalHost` fixture; assert file shape.
2. **Session-spy tests** (`test_session_local_supports_resume_*`): verify the helper is called when `supports_resume=True` and skipped when `False`.

```python
async def test_append_resume_log_entry_writes_iso_timestamp(tmp_workdir):
    """Calling _append_resume_log_entry once writes one ISO 8601 line."""
    import os
    import re
    import sys
    from optio_opencode.host import LocalHost
    from optio_opencode.session import _append_resume_log_entry

    host = LocalHost(taskdir=tmp_workdir, opencode_cmd=[sys.executable, "-c", "pass"])
    await host.setup_workdir()

    await _append_resume_log_entry(host)

    resume_log = os.path.join(host.workdir, "resume.log")
    assert os.path.isfile(resume_log)
    with open(resume_log) as f:
        content = f.read()
    lines = [line for line in content.splitlines() if line]
    assert len(lines) == 1
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", lines[0])


async def test_append_resume_log_entry_appends_on_repeat_call(tmp_workdir):
    """Two calls produce two lines (append, not overwrite)."""
    import asyncio
    import os
    import sys
    from optio_opencode.host import LocalHost
    from optio_opencode.session import _append_resume_log_entry

    host = LocalHost(taskdir=tmp_workdir, opencode_cmd=[sys.executable, "-c", "pass"])
    await host.setup_workdir()

    await _append_resume_log_entry(host)
    # Sleep just over a second so the second timestamp differs
    # (seconds-precision format).
    await asyncio.sleep(1.1)
    await _append_resume_log_entry(host)

    resume_log = os.path.join(host.workdir, "resume.log")
    with open(resume_log) as f:
        lines = [line for line in f.read().splitlines() if line]
    assert len(lines) == 2
    assert lines[0] != lines[1]


async def test_session_local_supports_resume_false_skips_resume_log(
    ctx_and_captures, _supply_scenario, tmp_workdir,
):
    """With supports_resume=False, no resume.log is created during the session.

    We verify by patching _append_resume_log_entry and asserting it isn't called.
    """
    from unittest.mock import AsyncMock
    import optio_opencode.session as session_mod

    ctx, _, _ = ctx_and_captures
    _supply_scenario["name"] = "happy"

    cfg = OpencodeTaskConfig(
        consumer_instructions="(scenario: happy)",
        supports_resume=False,
    )

    spy = AsyncMock()
    session_mod._append_resume_log_entry = spy  # type: ignore[attr-defined]
    try:
        await run_opencode_session(ctx, cfg)
    finally:
        # Restore original (re-import to get the unpatched ref).
        import importlib
        importlib.reload(session_mod)

    spy.assert_not_called()


async def test_session_local_supports_resume_true_calls_append(
    ctx_and_captures, _supply_scenario, tmp_workdir, monkeypatch,
):
    """With supports_resume=True (default), _append_resume_log_entry IS called."""
    from unittest.mock import AsyncMock
    import optio_opencode.session as session_mod

    ctx, _, _ = ctx_and_captures
    _supply_scenario["name"] = "happy"

    cfg = _config("happy")  # default supports_resume=True

    spy = AsyncMock()
    monkeypatch.setattr(session_mod, "_append_resume_log_entry", spy)
    await run_opencode_session(ctx, cfg)

    assert spy.await_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-opencode && pytest tests/test_session_local.py -v -k "resume_log or append_resume"`
Expected: Failures — `_append_resume_log_entry` doesn't exist yet.

- [ ] **Step 3: Add the helper and gate to `session.py`**

In `packages/optio-opencode/src/optio_opencode/session.py`:

1. Add this import near the top (with the other stdlib imports):
   ```python
   import shlex
   from datetime import datetime, timezone
   ```
   (`shlex` is likely already imported; only add what's missing.)

2. Add this helper at module level, near the other `_capture_snapshot`-style helpers:
   ```python
   async def _append_resume_log_entry(host) -> None:
       """Append one ISO 8601 UTC timestamp line to <workdir>/resume.log.

       Creates the file if missing (via shell `>>`). Caller is responsible
       for gating this on config.supports_resume.
       """
       ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
       target = f"{host.workdir}/resume.log"
       result = await host.run_command(
           f"echo {shlex.quote(ts)} >> {shlex.quote(target)}"
       )
       if result.exit_code != 0:
           raise RuntimeError(
               f"failed to append to resume.log: exit {result.exit_code}: "
               f"{result.stderr!r}"
           )
   ```

3. Find the existing `compose_agents_md` call site (it's inside the fresh-start branch, after `setup_workdir`) and update it to pass `supports_resume`:
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

4. Find the spot **after the fresh-vs-resume branch** (i.e., after both the fresh-start `write_text` calls AND the resume-path `restore_workdir` / `opencode_import` / `rotate_optio_log` calls have completed) and **before the `_install_or_ensure_binary` call**. Insert:
   ```python
           if config.supports_resume:
               await _append_resume_log_entry(host)
   ```

   (To find the right spot: search for `_install_or_ensure_binary` in `session.py` and insert the `if`-block immediately before it, at the matching indentation level inside the `try:` block.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_session_local.py -v`
Expected: All pass — including the new resume_log tests.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/session.py packages/optio-opencode/tests/test_session_local.py
git commit -m "feat(optio-opencode): _append_resume_log_entry + plumb supports_resume to compose_agents_md"
```

---

## Task 6: Gate snapshot capture on `supports_resume`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/session.py`
- Append: `packages/optio-opencode/tests/test_session_local.py`

- [ ] **Step 1: Append failing test**

Append to `packages/optio-opencode/tests/test_session_local.py`:

```python
async def test_session_local_supports_resume_false_skips_snapshot_capture(
    ctx_and_captures, _supply_scenario, tmp_workdir,
):
    """With supports_resume=False, no entry is added to the snapshots collection."""
    from optio_core.store import _collection
    ctx, _, _ = ctx_and_captures
    _supply_scenario["name"] = "happy"

    cfg = OpencodeTaskConfig(
        consumer_instructions="(scenario: happy)",
        supports_resume=False,
    )
    await run_opencode_session(ctx, cfg)

    snapshots_coll = ctx._db["test_snapshots"]
    count = await snapshots_coll.count_documents({"processId": "p"})
    assert count == 0


async def test_session_local_supports_resume_true_captures_snapshot(
    ctx_and_captures, _supply_scenario, tmp_workdir,
):
    """With supports_resume=True (default), a snapshot IS captured."""
    ctx, _, _ = ctx_and_captures
    _supply_scenario["name"] = "happy"

    cfg = _config("happy")  # default supports_resume=True
    await run_opencode_session(ctx, cfg)

    snapshots_coll = ctx._db["test_snapshots"]
    count = await snapshots_coll.count_documents({"processId": "p"})
    assert count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-opencode && pytest tests/test_session_local.py -v -k "skips_snapshot or true_captures"`
Expected: `test_session_local_supports_resume_false_skips_snapshot_capture` fails — the snapshot is taken regardless of `supports_resume`.

(`test_session_local_supports_resume_true_captures_snapshot` should already pass — it locks in current behavior.)

- [ ] **Step 3: Gate the snapshot call**

In `packages/optio-opencode/src/optio_opencode/session.py`, find the existing `_capture_snapshot` call inside the `finally` block. It currently looks like:

```python
        if session_id is not None:
            try:
                await _capture_snapshot(...)
```

Change the guard to also require `config.supports_resume`:

```python
        if config.supports_resume and session_id is not None:
            try:
                await _capture_snapshot(...)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_session_local.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/session.py packages/optio-opencode/tests/test_session_local.py
git commit -m "feat(optio-opencode): gate snapshot capture on supports_resume"
```

---

## Task 7: Resume cycle — verify `resume.log` grows by one line per resume

**Files:**
- Modify: `packages/optio-opencode/tests/test_session_resume.py` (append one new test)

The existing file already has a `_run_one_cycle(mongo_db, process_id, resume)` helper and a `task_root` fixture (autouse), and imports `load_latest_snapshot` from `optio_opencode.snapshots`. We piggyback on these — no fixture wiring duplication needed.

The test runs two cycles (fresh + resume), then reads back the LATEST snapshot's workdir blob (which is a tarball), extracts `./resume.log`, and asserts it has exactly two ISO 8601 lines in monotonic order.

- [ ] **Step 1: Append the new test**

Append to `packages/optio-opencode/tests/test_session_resume.py`:

```python
async def test_resume_appends_second_line_to_resume_log(mongo_db, task_root):
    """After one resume cycle, resume.log in the latest snapshot has exactly 2 lines."""
    import io
    import re
    import tarfile
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket

    pid = "oc_resume_log_growth"
    await _run_one_cycle(mongo_db, pid, resume=False)  # first launch
    await _run_one_cycle(mongo_db, pid, resume=True)   # resume

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    assert snap is not None

    # The workdir blob is a gzipped tar. Extract resume.log and read it.
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    stream = await bucket.open_download_stream(snap["workdirBlobId"])
    workdir_bytes = await stream.read()

    with tarfile.open(fileobj=io.BytesIO(workdir_bytes), mode="r:gz") as tar:
        member = tar.getmember("./resume.log")
        contents = tar.extractfile(member).read().decode("utf-8")

    lines = [line for line in contents.splitlines() if line]
    assert len(lines) == 2, f"expected 2 lines, got {len(lines)}: {contents!r}"

    iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    for line in lines:
        assert iso_re.match(line), f"non-ISO-8601 line: {line!r}"
    assert lines[0] <= lines[1], f"timestamps not monotonic: {lines!r}"
```

- [ ] **Step 2: Run the test**

Run: `cd packages/optio-opencode && pytest tests/test_session_resume.py::test_resume_appends_second_line_to_resume_log -v`
Expected: Pass (only after Tasks 1, 2, 3, 5 are complete — those provide the helper and wiring).

- [ ] **Step 3: Run all session-related tests for full regression**

Run: `cd packages/optio-opencode && pytest tests/test_session_local.py tests/test_session_resume.py tests/test_session_hooks.py -v`
Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-opencode/tests/test_session_resume.py
git commit -m "test(optio-opencode): assert resume.log gains a second line on resume cycle"
```

---

## Task 8: Update `packages/optio-opencode/AGENTS.md`

**Files:**
- Modify: `packages/optio-opencode/AGENTS.md`

- [ ] **Step 1: Read the current AGENTS.md**

Run: `cat packages/optio-opencode/AGENTS.md | head -80`

Find the section that lists `OpencodeTaskConfig` fields (probably labeled "Public API" or "OpencodeTaskConfig fields") — added or expanded during the hooks feature.

- [ ] **Step 2: Add `supports_resume` to the `OpencodeTaskConfig` fields list**

Add a bullet to the fields list:

```markdown
- `supports_resume: bool = True` — when False, the framework skips
  snapshot capture, omits the resume-detection prompt section, and
  doesn't write `resume.log`. The task launches fresh every time.
  Default `True` preserves current behavior.
```

- [ ] **Step 3: Add a "Resume awareness" subsection**

Insert a new subsection (typical placement: after the Hooks section, before the Log-file contract section). Use this content:

```markdown
## Resume awareness

When `supports_resume=True` (default), the framework writes
`<workdir>/resume.log` with one ISO 8601 timestamp per session start
(fresh launch and every resume). The agent's prompt — composed by
`compose_agents_md` — includes a section instructing the agent to
read `./resume.log` at the start of every new user message and treat
new lines as resume signals. The exact `workdir_exclude` patterns
configured for the task are inlined into the prompt so the agent's
mental model matches what the snapshot mechanism actually preserves.

When `supports_resume=False`:
- No snapshot capture occurs (no GridFS writes, no snapshots-collection
  rows).
- `resume.log` is never created.
- The resume-detection section is omitted from the composed AGENTS.md.

The opt-out is symmetric across the three concerns; consumers who
disable resume don't pay any cost for it.
```

- [ ] **Step 4: Verify the markdown looks reasonable**

Run: `cat packages/optio-opencode/AGENTS.md | head -150`
Visually scan for broken indentation, misaligned bullets, mismatched code fences.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/AGENTS.md
git commit -m "docs(optio-opencode): document supports_resume opt-out + resume.log mechanic"
```

---

## Final verification

- [ ] **Step 1: Run the full optio-opencode test suite**

Run: `cd packages/optio-opencode && pytest -v --deselect tests/test_session_remote.py::test_remote_happy_path 2>&1 | tail -10`
Expected: All tests pass (1 deselected — pre-existing flake unrelated to this branch).

- [ ] **Step 2: Verify the package imports cleanly**

Run: `cd packages/optio-opencode && python -c "from optio_opencode.prompt import compose_agents_md; from optio_opencode.types import OpencodeTaskConfig; from optio_opencode.session import _append_resume_log_entry; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Spot-check the rendered prompt**

Run:
```python
cd packages/optio-opencode && python -c "
from optio_opencode.prompt import compose_agents_md
print(compose_agents_md('Demo task', workdir_exclude=None, supports_resume=True))
"
```
Expected: Output includes `## Coordination protocol`, `## Log channel`, `## Deliverables`, `## Resumes` (with the default `.git`, `node_modules`, etc. patterns inlined), `### Detecting a resume: \`resume.log\``, `## Task`, and `Demo task` at the bottom.

- [ ] **Step 4: Spot-check opt-out**

Run:
```python
cd packages/optio-opencode && python -c "
from optio_opencode.prompt import compose_agents_md
print(compose_agents_md('Demo task', workdir_exclude=None, supports_resume=False))
"
```
Expected: Same as above MINUS the `## Resumes` section and any mention of `resume.log`.
