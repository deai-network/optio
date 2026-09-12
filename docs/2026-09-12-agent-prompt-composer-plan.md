# Agent Prompt Composer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the seven per-wrapper copies of the instructions-file prompt text with one composer in optio-agents, driven by a per-agent profile, and make every call site pass the session's protocol docs and sandbox dirs.

**Architecture:** `optio_agents.prompt` gains `AgentPromptProfile` (the per-agent variations) and `compose_instructions_file` (owns every piece of text). Each `optio_<agent>/prompt.py` shrinks to a `PROFILE` plus a thin `compose_agents_md` with the signature it has today. The 14 call sites in the wrappers' `session.py` pass `protocol.documentation` and `fs_isolation_dirs(config, host.workdir)`; each `_maybe_refresh_on_resume` takes the session's `protocol`. A guard test in optio-agents-all fails if a wrapper grows prompt text again.

**Tech Stack:** Python 3.11+, dataclasses, pytest (+ pytest-asyncio auto mode, pytest-xdist). Spec: `docs/2026-09-12-agent-prompt-composer-design.md`.

## Global Constraints

- Work on branch `agent-prompt-composer` in the worktree `excavator:~/deai/optio-prompt-dedupe`. Never edit or run pnpm in `~/deai/optio` (another agent's checkout).
- Run tests with the worktree's sources first on the path, so the editable installs in `~/deai/optio` are not imported:
  ```bash
  cd ~/deai/optio-prompt-dedupe
  export PYTHONPATH=$(ls -d $PWD/packages/*/src | paste -sd:)
  PYT="$HOME/deai/optio/.venv/bin/python -m pytest -p no:cacheprovider"
  ```
  Then `cd packages/<pkg> && $PYT -q tests/...`. Verify once per task with `$HOME/deai/optio/.venv/bin/python -c 'import optio_agents; print(optio_agents.__file__)'` printing a path under `optio-prompt-dedupe`.
- Commits: signed (repo default), conventional prefix (`feat(optio-agents): …`, `refactor(optio-grok): …`), **no** `Co-Authored-By` line (repo convention: none in history).
- Default output must be byte-identical to `main` except for the changes listed in the spec's "Text changes an agent will see". Task 15's render diff enforces this.
- Version floors (`optio-agents>=0.6.1`) and package versions are **not** bumped here; that is the release step, done after merge with the owner's approval.
- Out of scope: `_append_resume_log_entry` copies, `build_resume_notice_args` copies.

---

## File map

| File | Responsibility |
|---|---|
| `packages/optio-agents/src/optio_agents/prompt.py` | **Modify.** `AgentPromptProfile`, `compose_instructions_file`, all prompt text (`_INTRO`, `BASE_PROMPT_POST`, `DEFAULT_CONVERSATION_INSTRUCTIONS`, resume section, `System:` explainer, sandbox note, `downloadables_block`). Old `compose_agents_md` removed in Task 12. |
| `packages/optio-agents/src/optio_agents/fs_grants.py` | **Modify.** Add `fs_isolation_dirs(config, workdir)`. |
| `packages/optio-agents/tests/test_prompt_composer.py` | **Create.** Composer unit tests (all blocks, both resume variants, profile fields). |
| `packages/optio-agents/tests/test_fs_grants.py` | **Modify.** Tests for `fs_isolation_dirs`. |
| `packages/optio-<agent>/src/optio_<agent>/prompt.py` (×7) | **Rewrite.** `PROFILE` + thin `compose_agents_md`. |
| `packages/optio-<agent>/src/optio_<agent>/session.py` (×7) | **Modify.** Two compose call sites; `_maybe_refresh_on_resume(..., protocol)`. |
| `packages/optio-<agent>/tests/…` | **Modify.** Refresh-test signatures, fake hosts gain `workdir`, docs-threading tests. |
| `packages/optio-agents-all/tests/test_prompt_dedupe_guard.py` | **Create.** Guard: no prompt text in wrapper `prompt.py` modules. |
| `docs/writing-agent-wrappers.md` | **Modify.** Part 2D, Stage 2 reference, Appendix A row 18, Appendix B. |

---

### Task 1: Shared composer in optio-agents

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/prompt.py`
- Create: `packages/optio-agents/tests/test_prompt_composer.py`

**Interfaces:**
- Produces: `AgentPromptProfile(instructions_file="AGENTS.md", state_dir=None, state_dir_contents="", preamble="", browser="redirect", delivery_section="")`, `compose_instructions_file(consumer_instructions, *, profile, workdir_exclude, documentation=None, supports_resume=True, host_protocol=True, omit_task_framing=False, fs_isolation_dirs=None, file_download=False, check_resume_log_every_message=True) -> str`, `DEFAULT_CONVERSATION_INSTRUCTIONS`, `BASE_PROMPT_POST`, `downloadables_block` (unchanged). The old `compose_agents_md(consumer_instructions, *, documentation, resume_section=None)` stays until Task 12 so claudecode/codex keep working meanwhile.

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-agents/tests/test_prompt_composer.py`:

```python
"""Tests for the shared instructions-file composer."""

from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX, get_protocol
from optio_agents.prompt import (
    BASE_PROMPT_POST,
    DEFAULT_CONVERSATION_INSTRUCTIONS,
    AgentPromptProfile,
    compose_instructions_file,
)

PLAIN = AgentPromptProfile()
CLAUDE = AgentPromptProfile(
    instructions_file="CLAUDE.md",
    state_dir="home/.claude/",
    state_dir_contents="credentials, settings, and the conversation transcript",
    delivery_section="## Getting text to the user\n\nBody.",
)
POLL_RULE = "At the start of every new incoming user message"
NOTICE = f"`{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}`"


def _c(instructions="do X", profile=PLAIN, workdir_exclude=("a", "b"), **kw):
    return compose_instructions_file(
        instructions, profile=profile, workdir_exclude=list(workdir_exclude), **kw,
    )


def test_order_preamble_intro_docs_resume_delivery_framing_body():
    out = _c(profile=AgentPromptProfile(
        preamble="PREAMBLE\n\n", delivery_section="## Delivery\n\nD.",
    ))
    pos = [out.index(s) for s in (
        "PREAMBLE", "# Coordination protocol with the host harness", "## Log channel",
        "## Resumes", "## Delivery", "## Task", "do X",
    )]
    assert pos == sorted(pos)
    assert out.startswith("PREAMBLE")
    assert out.endswith("do X\n")


def test_default_docs_follow_profile_browser():
    assert "BROWSER:" in _c()                                   # redirect
    sup = _c(profile=AgentPromptProfile(browser="suppress"))
    assert "BROWSER:" not in sup and "impossible to launch a browser" in sup


def test_passed_documentation_is_used_verbatim():
    docs = get_protocol(browser="redirect", client_messages=True).documentation
    out = _c(documentation=docs)
    assert docs in out and "CLIENT_MESSAGE:" in out


def test_host_protocol_off_drops_intro_and_docs_adds_explainer():
    out = _c(host_protocol=False)
    assert "# Coordination protocol" not in out and "## Log channel" not in out
    assert "originate from the harness" in " ".join(out.split())
    assert "## Resumes" in out


def test_explainer_present_without_resume_section_when_host_protocol_off():
    out = _c(host_protocol=False, supports_resume=False)
    assert "## Resumes" not in out
    assert "originate from the harness" in " ".join(out.split())


def test_explainer_absent_when_host_protocol_on():
    assert "originate from the harness" not in " ".join(_c().split())


def test_supports_resume_false_omits_resume_and_state_dir():
    out = _c(profile=CLAUDE, supports_resume=False)
    assert "## Resumes" not in out and "resume.log" not in out
    assert "home/.claude/" not in out


def test_state_dir_bullet_only_when_set():
    assert "IS preserved across resumes" not in _c()
    out = _c(profile=CLAUDE)
    assert "**Your `home/.claude/` directory — credentials, settings, and the" in out
    assert "IS preserved across resumes**, so your" in out
    assert "history travels with you" in out


def test_instructions_file_named_in_refreshed_example_and_cat_hint():
    out = _c(profile=CLAUDE)
    assert "REFRESHED:CLAUDE.md" in out and "`cat ./CLAUDE.md`" in out
    out = _c()
    assert "REFRESHED:AGENTS.md" in out and "`cat ./AGENTS.md`" in out


def test_excludes_listed_custom_and_empty():
    out = _c(workdir_exclude=("custom_a",))
    assert "`custom_a`" in out and "exclude list is: `custom_a`." in out
    out = _c(workdir_exclude=())
    assert "No paths are excluded" in out
    assert "inside an excluded subdirectory" not in out


def test_resume_detection_polls_by_default():
    out = _c()
    assert POLL_RULE in out and "slips past unnoticed" in out
    assert NOTICE not in out


def test_resume_detection_by_notice():
    out = _c(check_resume_log_every_message=False)
    assert POLL_RULE not in out and "slips past unnoticed" not in out
    assert NOTICE in out
    assert "read the latest line of `./resume.log`" in out
    assert "Then resume the work you were doing." in out


def test_task_framing_and_omission():
    assert BASE_PROMPT_POST in _c()
    out = _c(DEFAULT_CONVERSATION_INSTRUCTIONS, omit_task_framing=True)
    assert "## Task" not in out
    assert out.endswith(DEFAULT_CONVERSATION_INSTRUCTIONS + "\n")


def test_delivery_section_survives_omit_task_framing():
    out = _c(profile=CLAUDE, omit_task_framing=True)
    assert "## Getting text to the user" in out and "## Task" not in out


def test_downloads_note_then_sandbox_note_after_body():
    out = _c(file_download=True, fs_isolation_dirs=[("/wd", "rwx"), ("/ro", "ro")])
    body, dl, fs = out.index("do X"), out.index("## Downloadable files"), out.index("**Filesystem access:**")
    assert body < dl < fs
    assert "`/ro` (read-only)" in out and "`/wd` (read-only)" not in out
    assert "## Downloadable files" not in _c()
    assert "Filesystem access" not in _c()


def test_downloads_wording_follows_host_protocol():
    assert "Deliverables (the DELIVERABLE keyword)" in _c(file_download=True)
    assert "Deliverables (the DELIVERABLE keyword)" not in _c(file_download=True, host_protocol=False)
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd packages/optio-agents && $PYT -q tests/test_prompt_composer.py
```
Expected: ImportError (`AgentPromptProfile`).

- [ ] **Step 3: Implement the composer**

Replace the whole of `packages/optio-agents/src/optio_agents/prompt.py` with:

```python
"""Instructions-file composer shared by every optio agent wrapper.

``compose_instructions_file`` renders the file each wrapper writes into its
workdir (``CLAUDE.md`` for optio-claudecode, ``AGENTS.md`` for the others):
the coordination intro plus the keyword-protocol docs, the resume section, the
``System:`` explainer, the task framing, and the consumer's own instructions.
Everything that differs between agents arrives through an
``AgentPromptProfile``; a wrapper's ``prompt.py`` holds its profile and a thin
``compose_agents_md`` and no prompt text of its own (a guard test in
optio-agents-all enforces that).

The keyword-protocol docs are **passed in** by the session
(``get_protocol(...).documentation``) so they reflect the task's protocol
features; the ``profile.browser`` fallback exists for unit tests and
standalone callers only.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

from optio_agents.context import SYSTEM_MESSAGE_PREFIX
from optio_agents.protocol.features import ProtocolFeatures
from optio_agents.protocol.prompt import RESUME_NOTICE, build_log_channel_prompt


@dataclass(frozen=True)
class AgentPromptProfile:
    """What differs between agents in the instructions file."""

    instructions_file: str = "AGENTS.md"   # named in the REFRESHED: example
    state_dir: str | None = None           # e.g. "home/.claude/"; None = no bullet
    state_dir_contents: str = ""           # e.g. "credentials, settings, and ..."
    preamble: str = ""                     # rendered first (grok's identity line)
    browser: str = "redirect"              # fallback docs only, see module doc
    delivery_section: str = ""             # rendered just before the task framing


_INTRO = """# Coordination protocol with the host harness

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


DEFAULT_CONVERSATION_INSTRUCTIONS = "Let's have a conversation with the user."


# Added whenever the keyword-protocol docs (which explain the System:
# convention) are omitted (host_protocol=False).
_SYSTEM_PREFIX_EXPLAINER = """
(Messages prefixed `System:` on your input channel originate from the
harness coordinating this session, not from the human user.)
"""


_RESUME_SECTION_TEMPLATE = """## Resumes

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
{state_dir_bullet}
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
`2026-05-28T13:15:42Z REFRESHED:{instructions_file}`) — your in-memory copy of
those files is stale and must be re-read before continuing.

{detection_clause}"""


def _resume_steps(instructions_file: str) -> str:
    """What the agent does once it knows it was resumed (either detection style)."""
    return f"""- Verify any tools, processes, or files you previously gathered
  outside the workdir are still where you left them.
- Re-establish anything that's gone (re-launch a server, re-fetch a
  file, etc.) before continuing.
- **If the latest line carries a `REFRESHED:` suffix, re-read each
  listed file** (e.g. `cat ./{instructions_file}`) — the harness updated it
  since your last context snapshot and the version you remember is
  out of date.
- Then resume the work you were doing.
"""


def _detect_by_polling(instructions_file: str) -> str:
    """Pull: poll resume.log on every user message, for sessions the harness
    may resume without telling the agent."""
    return """**At the start of every new incoming user message, read
`./resume.log` first.** Compare the latest line to the value you
remembered last time you checked. If a new line has appeared, treat
the situation as a resume:

""" + _resume_steps(instructions_file) + """
If a resume slips past unnoticed, a failing tool call is the
next-best signal — re-check `./resume.log` then.

You may also be notified of a resume by a `System:` message on your input
channel; when you see one, follow the `resume.log` procedure above.
"""


def _detect_by_notice(instructions_file: str) -> str:
    """Push: every resume is announced by the resume notice, so resume.log is
    read only when it arrives (for its REFRESHED: suffix)."""
    return f"""The harness tells you about every resume with a
`{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}` message on your input channel, so you do
not need to check `./resume.log` before each message. When that message
arrives, read the latest line of `./resume.log` and treat the situation
as a resume:

""" + _resume_steps(instructions_file)


def _state_dir_bullet(profile: AgentPromptProfile) -> str:
    if not profile.state_dir:
        return ""
    text = (
        f"**Your `{profile.state_dir}` directory — {profile.state_dir_contents} — "
        "IS preserved across resumes**, so your history travels with you even "
        "when the underlying process and host change."
    )
    return "\n" + textwrap.fill(
        text, width=72, initial_indent="- ", subsequent_indent="  ",
        break_long_words=False, break_on_hyphens=False,
    ) + "\n"


def render_resume_section(
    profile: AgentPromptProfile,
    workdir_exclude: list[str],
    check_resume_log_every_message: bool = True,
) -> str:
    """Render the resume section for ``profile`` and the effective exclude list.

    ``check_resume_log_every_message`` picks how the agent detects a resume:
    by polling ``resume.log`` on every user message (default), or by waiting
    for the ``System:`` resume notice (only when every resume sends one)."""
    if not workdir_exclude:
        excludes_clause = (
            "**No paths are excluded** — every file in the workdir is preserved."
        )
        outside_clause = (
            "If you need to stash large data, place it outside the workdir "
            "(e.g. `/tmp/`) — but remember it may be missing when you next look."
        )
    else:
        excludes_str = ", ".join(f"`{p}`" for p in workdir_exclude)
        excludes_clause = (
            f"**Paths matching the snapshot exclude list are NOT preserved**, "
            f"even inside the workdir. The current exclude list is: {excludes_str}."
        )
        outside_clause = (
            "If you need to stash large data, place it outside the workdir "
            "(e.g. `/tmp/`) or inside an excluded subdirectory — but remember "
            "any such location may be missing when you next look."
        )
    detect = _detect_by_polling if check_resume_log_every_message else _detect_by_notice
    return _RESUME_SECTION_TEMPLATE.format(
        excludes_clause=excludes_clause,
        outside_clause=outside_clause,
        state_dir_bullet=_state_dir_bullet(profile),
        instructions_file=profile.instructions_file,
        detection_clause=detect(profile.instructions_file),
    )


def _sandbox_note(fs_isolation_dirs: list[tuple[str, str]]) -> str:
    # The session is sandboxed (claustrum/Landlock); tell the agent its bounds
    # so EACCES on a stray path isn't a mystery. Entries are (path, mode) with
    # claustrum modes; ro/rox grants are flagged read-only so the agent doesn't
    # plan to write there.
    allowed = ", ".join(
        f"`{d}` (read-only)" if m in ("ro", "rox") else f"`{d}`"
        for d, m in fs_isolation_dirs
    )
    return (
        "\n\n**Filesystem access:** This session is filesystem-isolated — "
        "you can access files within these directories (and their "
        f"subdirectories): {allowed}. Files outside them are not accessible "
        "(reads and writes will fail with a permission error)."
    )


def compose_instructions_file(
    consumer_instructions: str,
    *,
    profile: AgentPromptProfile,
    workdir_exclude: list[str],
    documentation: str | None = None,
    supports_resume: bool = True,
    host_protocol: bool = True,
    omit_task_framing: bool = False,
    fs_isolation_dirs: list[tuple[str, str]] | None = None,
    file_download: bool = False,
    check_resume_log_every_message: bool = True,
) -> str:
    """Render a wrapper's instructions file.

    Args:
      consumer_instructions: the task author's prompt, appended verbatim
        (trailing whitespace stripped).
      profile: the agent's ``AgentPromptProfile``.
      workdir_exclude: the EFFECTIVE snapshot exclude list (already resolved
        by the wrapper), so the section's preservation claims match the
        snapshot. ``[]`` renders the "no paths are excluded" wording.
      documentation: the keyword-protocol block from the session's protocol
        (``get_protocol(...).documentation``). ``None`` falls back to
        ``ProtocolFeatures(browser=profile.browser)``.
      supports_resume: when False, no resume section (and no state-dir bullet).
      host_protocol: when False, the intro and the protocol docs are omitted
        and the ``System:`` explainer is added instead.
      omit_task_framing: drop the ``## Task`` framing (defaulted conversation
        instructions). The delivery section is still rendered.
      fs_isolation_dirs: (path, mode) pairs the agent may touch under fs
        isolation; renders the sandbox note after the instructions.
      file_download: append the downloadables note (comparative wording when
        ``host_protocol``).
      check_resume_log_every_message: False tells the agent to rely on the
        ``System:`` resume notice instead of polling ``resume.log``. Pass it
        only when every resume sends that notice.
    """
    body = consumer_instructions.rstrip()
    if file_download:
        body += downloadables_block(comparative=host_protocol)
    if fs_isolation_dirs:
        body += _sandbox_note(fs_isolation_dirs)
    if host_protocol:
        if documentation is None:
            documentation = build_log_channel_prompt(
                ProtocolFeatures(browser=profile.browser)
            )
        pre = _INTRO + documentation + "\n"
    else:
        pre = ""
    resume_block = (
        render_resume_section(profile, workdir_exclude, check_resume_log_every_message)
        if supports_resume else ""
    )
    if not host_protocol:
        resume_block += _SYSTEM_PREFIX_EXPLAINER
    if resume_block:
        resume_block += "\n"
    delivery = (
        profile.delivery_section.rstrip() + "\n\n" if profile.delivery_section else ""
    )
    framing = "" if omit_task_framing else BASE_PROMPT_POST + "\n"
    return f"{profile.preamble}{pre}{resume_block}{delivery}{framing}{body}\n"


def compose_agents_md(
    consumer_instructions: str,
    *,
    documentation: str | None,
    resume_section: str | None = None,
) -> str:
    """Old outer-framing composer. Kept until every wrapper is on
    ``compose_instructions_file``; removed in the same change set."""
    pre = (_INTRO + documentation + "\n") if documentation else ""
    body = consumer_instructions.rstrip()
    resume_block = (resume_section + "\n") if resume_section else ""
    return f"{pre}{resume_block}{BASE_PROMPT_POST}\n{body}\n"


def downloadables_block(comparative: bool) -> str:
    """Instruction paragraph teaching the agent to offer a file to the human as
    a one-click download via a sentinel markdown link. Two wordings:
    comparative (when the deliverable keyword protocol is active) vs standalone.
    """
    sentinel = "`[name](optio-file:relpath)`"
    if comparative:
        return (
            "\n\n## Downloadable files\n"
            "Deliverables (the DELIVERABLE keyword) are shipped to the host harness "
            "for automatic processing. **Downloadable files are different**: they go "
            "directly to the human user. Produce one only **deliberately**, when the "
            "user interactively asks you for a file. To offer a file for download, "
            "write it into the working directory and present it as a markdown link "
            f"with the optio-file scheme: {sentinel} — where `relpath` is the file's "
            "path relative to the working directory."
        )
    return (
        "\n\n## Downloadable files\n"
        "When the user asks you for a file, write it into the working directory and "
        f"present it to them as a one-click download: a markdown link {sentinel}, "
        "where `relpath` is the file's path relative to the working directory."
    )
```

Note: the old module's `_INTRO` heading was "with the host (optio)"; the shared heading is now "with the host harness" (spec: normalized). The old `compose_agents_md` therefore already renders the new heading for claudecode/codex, which is the intended end state.

- [ ] **Step 4: Run the tests**

```bash
cd packages/optio-agents && $PYT -q tests/test_prompt_composer.py tests/test_prompt.py tests/test_downloadables_block.py tests/test_package_exports.py
```
Expected: all pass. If `test_state_dir_bullet_only_when_set` fails on the wrapped line, print the output and adjust the assertion to the actual wrap points (the wrap is deterministic; the words must all be present).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-agents/src/optio_agents/prompt.py packages/optio-agents/tests/test_prompt_composer.py
git commit -m "feat(optio-agents): shared instructions-file composer (AgentPromptProfile + compose_instructions_file)"
```

---

### Task 2: `fs_isolation_dirs` helper

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/fs_grants.py`
- Modify: `packages/optio-agents/tests/test_fs_grants.py`

**Interfaces:**
- Produces: `fs_isolation_dirs(config, workdir: str) -> list[tuple[str, str]] | None`, where `config` has `fs_isolation: bool` and `extra_allowed_dirs: list[AllowedDir] | None` (any `ClaustrumConfigMixin` subclass).

- [ ] **Step 1: Write the failing tests** (append to `test_fs_grants.py`)

```python
from types import SimpleNamespace


def test_fs_isolation_dirs_none_when_isolation_off():
    cfg = SimpleNamespace(fs_isolation=False, extra_allowed_dirs=[AllowedDir("/x", "ro")])
    assert fs_grants.fs_isolation_dirs(cfg, "/wd") is None


def test_fs_isolation_dirs_workdir_then_extras_verbatim():
    cfg = SimpleNamespace(
        fs_isolation=True, extra_allowed_dirs=[AllowedDir("~/tools", "rox"), AllowedDir("/tmp", "rw")],
    )
    assert fs_grants.fs_isolation_dirs(cfg, "/wd/") == [
        ("/wd", "rwx"), ("~/tools", "rox"), ("/tmp", "rw"),
    ]


def test_fs_isolation_dirs_no_extras():
    cfg = SimpleNamespace(fs_isolation=True, extra_allowed_dirs=None)
    assert fs_grants.fs_isolation_dirs(cfg, "/wd") == [("/wd", "rwx")]
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd packages/optio-agents && $PYT -q tests/test_fs_grants.py
```
Expected: AttributeError (`fs_isolation_dirs`).

- [ ] **Step 3: Implement** (append to `fs_grants.py`)

```python
def fs_isolation_dirs(config, workdir: str) -> "list[tuple[str, str]] | None":
    """The agent-facing (path, mode) list of directories it may touch under fs
    isolation (its workdir + caller extras), or None when isolation is off.
    Used for the sandbox note in the instructions file. Paths stay verbatim
    (incl. ``~/``): the agent's own $HOME view is what it needs. ``config`` is
    any ``ClaustrumConfigMixin`` subclass."""
    if not config.fs_isolation:
        return None
    extras = [(ad.path, ad.mode) for ad in (config.extra_allowed_dirs or [])]
    return [(workdir.rstrip("/"), "rwx"), *extras]
```

- [ ] **Step 4: Run** `cd packages/optio-agents && $PYT -q tests/test_fs_grants.py` — Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-agents/src/optio_agents/fs_grants.py packages/optio-agents/tests/test_fs_grants.py
git commit -m "feat(optio-agents): fs_isolation_dirs helper for the sandbox note"
```

---

### Task 3: optio-opencode

**Files:**
- Rewrite: `packages/optio-opencode/src/optio_opencode/prompt.py`
- Modify: `packages/optio-opencode/src/optio_opencode/session.py` (compose at ~343, `_maybe_refresh_on_resume` at ~1033 and its compose at ~1053, the call at ~393)
- Modify: `packages/optio-opencode/tests/test_prompt.py`, `tests/test_session_local.py`
- Delete: `packages/optio-opencode/tests/test_resume_sentence_opencode.py`

**Interfaces:**
- Consumes: Task 1 `compose_instructions_file`, `AgentPromptProfile`, `DEFAULT_CONVERSATION_INSTRUCTIONS`; Task 2 `fs_isolation_dirs`.
- Produces: `optio_opencode.prompt.compose_agents_md(consumer_instructions, *, workdir_exclude=None, documentation=None, supports_resume=True, host_protocol=True, omit_task_framing=False, fs_isolation_dirs=None, file_download=False, check_resume_log_every_message=True)`; `_maybe_refresh_on_resume(host, hook_ctx, config, protocol)`.

- [ ] **Step 1: Update tests first**

In `tests/test_prompt.py`: delete `test_compose_agents_md_workdir_exclude_required` (and the now-unused `import pytest` if nothing else uses it). Append:

```python
def test_sandbox_note_and_threaded_docs():
    from optio_agents import get_protocol
    docs = get_protocol(browser="suppress", client_messages=True).documentation
    out = compose_agents_md(
        "x", documentation=docs, fs_isolation_dirs=[("/wd", "rwx")],
    )
    assert "CLIENT_MESSAGE:" in out
    assert "**Filesystem access:**" in out and "`/wd`" in out


def test_no_prompt_text_of_its_own():
    import inspect
    import optio_opencode.prompt as m
    src = inspect.getsource(m)
    assert "This harness may pause your session" not in src
    assert "You are running inside a coordination harness" not in src
```

Delete `tests/test_resume_sentence_opencode.py` (the shared behavior is covered by `optio-agents/tests/test_prompt_composer.py::test_resume_detection_polls_by_default`).

In `tests/test_session_local.py`, every direct call `_maybe_refresh_on_resume(host, <hook>, config)` becomes `_maybe_refresh_on_resume(host, <hook>, config, get_protocol(browser="suppress"))` with `from optio_agents import get_protocol` added inside those tests. Add one test next to them:

```python
async def test_maybe_refresh_on_resume_threads_session_docs(tmp_workdir):
    """The refreshed AGENTS.md carries the SESSION's protocol docs (client
    messages documented when the task enables them), not rebuilt defaults."""
    import os
    from dataclasses import replace
    from optio_agents import get_protocol
    from optio_host.host import LocalHost
    from optio_opencode.session import _maybe_refresh_on_resume
    from optio_opencode.types import OpencodeTaskConfig

    host = LocalHost(taskdir=tmp_workdir)
    await host.setup_workdir()
    config = OpencodeTaskConfig(
        consumer_instructions="task X", fs_isolation=False, use_client_messages=True,
        on_resume_refresh=lambda c: replace(c, consumer_instructions="task Y"),
    )
    await host.write_text("AGENTS.md", "stale")

    class _FakeHookCtx:
        async def read_text_from_host(self, path, *, silent=False):
            with open(os.path.join(host.workdir, path)) as f:
                return f.read()

    protocol = get_protocol(browser="suppress", client_messages=True)
    assert await _maybe_refresh_on_resume(host, _FakeHookCtx(), config, protocol) == ["AGENTS.md"]
    with open(os.path.join(host.workdir, "AGENTS.md")) as f:
        text = f.read()
    assert "task Y" in text and "CLIENT_MESSAGE:" in text
```
(Check the field name with `grep -n "client_messages=config\." src/optio_opencode/session.py`; use the attribute it reads.)

- [ ] **Step 2: Run to verify failure**

```bash
cd packages/optio-opencode && $PYT -q tests/test_prompt.py tests/test_session_local.py -k "prompt_text or sandbox_note or threads_session_docs or maybe_refresh"
```
Expected: the new tests fail (TypeError on the 4th arg / assertion).

- [ ] **Step 3: Rewrite `prompt.py`**

```python
"""AGENTS.md composition for optio-opencode: the profile, plus a thin wrapper
over the shared composer (``optio_agents.prompt.compose_instructions_file``),
which owns all of the text."""

from optio_agents.prompt import (
    DEFAULT_CONVERSATION_INSTRUCTIONS,
    AgentPromptProfile,
    compose_instructions_file,
)
from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES

__all__ = ["DEFAULT_CONVERSATION_INSTRUCTIONS", "PROFILE", "compose_agents_md"]

# opencode suppresses browser opens (its docs must not advertise BROWSER:), and
# keeps no state dir the agent needs to know about.
PROFILE = AgentPromptProfile(browser="suppress")


def compose_agents_md(
    consumer_instructions: str,
    *,
    workdir_exclude: list[str] | None = None,
    documentation: str | None = None,
    supports_resume: bool = True,
    host_protocol: bool = True,
    omit_task_framing: bool = False,
    fs_isolation_dirs: list[tuple[str, str]] | None = None,
    file_download: bool = False,
    check_resume_log_every_message: bool = True,
) -> str:
    """Render <workdir>/AGENTS.md for an optio-opencode task.

    ``workdir_exclude=None`` means the framework defaults, the same list the
    snapshot archive uses (``optio_host.archive``)."""
    return compose_instructions_file(
        consumer_instructions,
        profile=PROFILE,
        workdir_exclude=(
            list(DEFAULT_WORKDIR_EXCLUDES) if workdir_exclude is None else workdir_exclude
        ),
        documentation=documentation,
        supports_resume=supports_resume,
        host_protocol=host_protocol,
        omit_task_framing=omit_task_framing,
        fs_isolation_dirs=fs_isolation_dirs,
        file_download=file_download,
        check_resume_log_every_message=check_resume_log_every_message,
    )
```

- [ ] **Step 4: Update `session.py`**

Add `from optio_agents.fs_grants import fs_isolation_dirs` to the imports.

Fresh-start site (~line 343): replace the call with

```python
                compose_agents_md(
                    instructions,
                    documentation=protocol.documentation if config.host_protocol else None,
                    workdir_exclude=config.workdir_exclude,
                    supports_resume=config.supports_resume,
                    host_protocol=config.host_protocol,
                    omit_task_framing=omit_task_framing,
                    fs_isolation_dirs=fs_isolation_dirs(config, host.workdir),
                    file_download=config.file_download,
                ),
```

Refresh call (~line 393): `refreshed_files = await _maybe_refresh_on_resume(host, hook_ctx, config, protocol)`.

Refresh function: signature `async def _maybe_refresh_on_resume(host, hook_ctx, config: OpencodeTaskConfig, protocol) -> list[str]:`; add to its docstring "``protocol`` is the session's protocol; its documentation is rendered so the refreshed file matches the fresh-start composition." Its compose becomes:

```python
    new_agents_md = compose_agents_md(
        new_config.consumer_instructions,
        documentation=protocol.documentation if new_config.host_protocol else None,
        workdir_exclude=new_config.workdir_exclude,
        supports_resume=new_config.supports_resume,
        # Reflect the refreshed config so a resume keeps the downloadables block
        # (with the right wording — host_protocol drives comparative vs standalone).
        host_protocol=new_config.host_protocol,
        fs_isolation_dirs=fs_isolation_dirs(new_config, host.workdir),
        file_download=new_config.file_download,
    )
```

Check nothing else calls the old signature: `grep -rn "_maybe_refresh_on_resume(" src tests`.

- [ ] **Step 5: Run the package's fast tests**

```bash
cd packages/optio-opencode && $PYT -q -n 4 --dist loadscope -m "not serial" tests/test_prompt.py tests/test_file_download.py tests/test_session_local.py
```
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add -A packages/optio-opencode
git commit -m "refactor(optio-opencode): compose AGENTS.md through the shared composer; session docs + sandbox note at both sites"
```

---

### Task 4: optio-claudecode

**Files:**
- Rewrite: `packages/optio-claudecode/src/optio_claudecode/prompt.py`
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py` (delete `_fs_isolation_dirs` at ~100; compose at ~1117; `_maybe_refresh_on_resume` ~1518, its compose ~1542, its call ~1143 inside `_plant_session_content`)
- Rewrite: `packages/optio-claudecode/tests/test_resume_prompt.py`
- Delete: `packages/optio-claudecode/tests/test_resume_sentence_claudecode.py`

**Interfaces:**
- Consumes: Task 1, Task 2.
- Produces: `optio_claudecode.prompt.compose_agents_md` (same signature as opencode's), `PROFILE`, re-exported `DEFAULT_CONVERSATION_INSTRUCTIONS`; `_maybe_refresh_on_resume(host, hook_ctx, config, protocol)`.

- [ ] **Step 1: Rewrite `tests/test_resume_prompt.py`** (claudecode-specific assertions only; shared ones now live in optio-agents)

```python
"""Claudecode-specific parts of the CLAUDE.md resume section and delivery note."""

from types import SimpleNamespace

from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX, get_protocol

from optio_claudecode.prompt import PROFILE, compose_agents_md

_POLL_RULE = "At the start of every new incoming user message"


def test_profile():
    assert PROFILE.instructions_file == "CLAUDE.md"
    assert PROFILE.state_dir == "home/.claude/"


def test_resume_section_names_claude_md_and_home_claude():
    out = compose_agents_md("hi")
    assert "## Resumes" in out
    assert "REFRESHED:CLAUDE.md" in out and "`cat ./CLAUDE.md`" in out
    assert "**Your `home/.claude/` directory" in out


def test_resume_section_omitted_when_disabled():
    out = compose_agents_md("hi", supports_resume=False)
    assert "## Resumes" not in out and "home/.claude/" not in out


def test_delivery_section_present_in_every_mode():
    for kw in (dict(), dict(host_protocol=False), dict(omit_task_framing=True)):
        out = compose_agents_md("DO THE TASK", **kw)
        assert "## Getting text to the user" in out
        assert "AskUserQuestion" in out
        assert out.index("## Getting text to the user") < out.index("DO THE TASK")


def test_polling_flag_passes_through():
    assert _POLL_RULE in compose_agents_md("hi")
    out = compose_agents_md("hi", check_resume_log_every_message=False)
    assert _POLL_RULE not in out
    assert f"`{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}`" in out


def test_session_polls_resume_log_only_without_host_protocol():
    from optio_claudecode.session import _checks_resume_log_every_message
    assert _checks_resume_log_every_message(SimpleNamespace(host_protocol=False))
    assert not _checks_resume_log_every_message(SimpleNamespace(host_protocol=True))


def test_threaded_docs_and_no_prompt_text_of_its_own():
    import inspect
    import optio_claudecode.prompt as m
    docs = get_protocol(browser="redirect", caller_messages=True).documentation
    assert "CALLER_MESSAGE:" in compose_agents_md("x", documentation=docs)
    src = inspect.getsource(m)
    assert "This harness may pause your session" not in src
    assert "You are running inside a coordination harness" not in src
```

Delete `tests/test_resume_sentence_claudecode.py`.

- [ ] **Step 2: Run to verify failure** — `cd packages/optio-claudecode && $PYT -q tests/test_resume_prompt.py` — Expected: ImportError (`PROFILE`).

- [ ] **Step 3: Rewrite `prompt.py`**

```python
"""CLAUDE.md composition for optio-claudecode: the profile, plus a thin wrapper
over the shared composer (``optio_agents.prompt.compose_instructions_file``),
which owns all of the text.

Claudecode-specific: the file is ``CLAUDE.md``; ``home/.claude/`` (credentials,
settings, conversation transcript) is preserved across resumes; and the
"Getting text to the user" section, because with extended thinking on, text
Claude Code writes in a response that also calls a tool may never reach the
reader (owner request, 2026-09-12)."""

from optio_agents.prompt import (
    DEFAULT_CONVERSATION_INSTRUCTIONS,
    AgentPromptProfile,
    compose_instructions_file,
)
from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES

__all__ = ["DEFAULT_CONVERSATION_INSTRUCTIONS", "PROFILE", "compose_agents_md"]

_DELIVERY_SECTION = """## Getting text to the user

Text you write in a response that also calls a tool may never reach the user: with extended thinking on it can end up in your hidden reasoning, and at most a short summary is shown. So:

- Anything the user must read in full (drafts, spec sections, reports, long answers) goes in a response with no tool call after it, or in a file you write and name the path of.
- When you ask the user to review something with AskUserQuestion, put the full text in the question itself or in a file, never in text before the call.
- Never assume the user saw text you wrote before a tool call.
"""

PROFILE = AgentPromptProfile(
    instructions_file="CLAUDE.md",
    state_dir="home/.claude/",
    state_dir_contents="credentials, settings, and the conversation transcript",
    delivery_section=_DELIVERY_SECTION,
)


def compose_agents_md(
    consumer_instructions: str,
    *,
    workdir_exclude: list[str] | None = None,
    documentation: str | None = None,
    supports_resume: bool = True,
    host_protocol: bool = True,
    omit_task_framing: bool = False,
    fs_isolation_dirs: list[tuple[str, str]] | None = None,
    file_download: bool = False,
    check_resume_log_every_message: bool = True,
) -> str:
    """Render <workdir>/CLAUDE.md for an optio-claudecode task.

    ``workdir_exclude=None`` means the framework defaults, the same list the
    snapshot archive uses (``optio_host.archive``)."""
    return compose_instructions_file(
        consumer_instructions,
        profile=PROFILE,
        workdir_exclude=(
            list(DEFAULT_WORKDIR_EXCLUDES) if workdir_exclude is None else workdir_exclude
        ),
        documentation=documentation,
        supports_resume=supports_resume,
        host_protocol=host_protocol,
        omit_task_framing=omit_task_framing,
        fs_isolation_dirs=fs_isolation_dirs,
        file_download=file_download,
        check_resume_log_every_message=check_resume_log_every_message,
    )
```

- [ ] **Step 4: Update `session.py`**

1. Add `from optio_agents.fs_grants import fs_isolation_dirs`.
2. Delete the local `_fs_isolation_dirs` function (~lines 100–110). `grep -rn "_fs_isolation_dirs" src tests` must then return nothing.
3. Fresh-start site (~1117): `fs_isolation_dirs=_fs_isolation_dirs(config, host)` → `fs_isolation_dirs=fs_isolation_dirs(config, host.workdir)`. Keep the `documentation=` and `check_resume_log_every_message=` lines as they are.
4. Refresh call (~1143): `_maybe_refresh_on_resume(host, hook_ctx, config)` → `_maybe_refresh_on_resume(host, hook_ctx, config, protocol)`. `_plant_session_content` already has `protocol` in scope (its fresh-start branch uses `protocol.documentation`); if it is not a parameter, add it and pass it from the caller.
5. Refresh function: signature `async def _maybe_refresh_on_resume(host, hook_ctx, config: ClaudeCodeTaskConfig, protocol) -> list[str]:`, docstring note as in Task 3; its compose becomes:

```python
    new_claude_md = compose_agents_md(
        instructions,
        documentation=protocol.documentation if new_config.host_protocol else None,
        workdir_exclude=new_config.workdir_exclude,
        supports_resume=new_config.supports_resume,
        host_protocol=new_config.host_protocol,
        omit_task_framing=omit_task_framing,
        fs_isolation_dirs=fs_isolation_dirs(new_config, host.workdir),
        file_download=new_config.file_download,
        check_resume_log_every_message=_checks_resume_log_every_message(new_config),
    )
```

- [ ] **Step 5: Run the package's fast tests**

```bash
cd packages/optio-claudecode && $PYT -q -n 4 --dist loadscope -m "not serial" tests/test_resume_prompt.py tests/test_prompt.py tests/test_conversation_config.py tests/test_file_download.py tests/test_on_resume_refresh.py
```
Expected: pass. (`test_on_resume_refresh.py` runs a full fake-binary session; it needs the local Mongo, as before.)

- [ ] **Step 6: Commit**

```bash
git add -A packages/optio-claudecode
git commit -m "refactor(optio-claudecode): compose CLAUDE.md through the shared composer; delivery section; docs threaded on refresh"
```

---

### Task 5: optio-codex

**Files:**
- Rewrite: `packages/optio-codex/src/optio_codex/prompt.py`
- Modify: `packages/optio-codex/src/optio_codex/session.py` (compose ~299; refresh compose ~887; refresh already takes `protocol`)
- Modify: `packages/optio-codex/tests/test_prompt.py`

**Interfaces:** as Task 3 (`compose_agents_md` same signature; `_maybe_refresh_on_resume(host, hook_ctx, config, protocol)` already so).

- [ ] **Step 1: Update tests** — append to `tests/test_prompt.py`:

```python
def test_sandbox_note_and_no_prompt_text_of_its_own():
    import inspect
    import optio_codex.prompt as m
    out = compose_agents_md("x", fs_isolation_dirs=[("/wd", "rwx")])
    assert "**Filesystem access:**" in out
    src = inspect.getsource(m)
    assert "This harness may pause your session" not in src
    assert "originate from the harness" not in src
```
`test_shared_framing_is_imported_not_copied` keeps importing `BASE_PROMPT_POST` from `optio_agents.prompt` (still there).

- [ ] **Step 2: Run to verify failure** — `cd packages/optio-codex && $PYT -q tests/test_prompt.py` — Expected: the new test fails (TypeError `fs_isolation_dirs`).

- [ ] **Step 3: Rewrite `prompt.py`**

```python
"""AGENTS.md composition for optio-codex: the profile, plus a thin wrapper over
the shared composer (``optio_agents.prompt.compose_instructions_file``), which
owns all of the text. The exclude list is resolved with the same function the
snapshot archive uses, so the section's preservation claims never drift."""

from optio_agents.prompt import AgentPromptProfile, compose_instructions_file

from optio_codex.snapshots import effective_workdir_exclude

__all__ = ["PROFILE", "compose_agents_md"]

PROFILE = AgentPromptProfile(
    state_dir="home/.codex/",
    state_dir_contents=(
        "the codex session store (rollout files under `home/.codex/sessions`, "
        "auth, config)"
    ),
)


def compose_agents_md(
    consumer_instructions: str,
    *,
    workdir_exclude: list[str] | None = None,
    documentation: str | None = None,
    supports_resume: bool = True,
    host_protocol: bool = True,
    omit_task_framing: bool = False,
    fs_isolation_dirs: list[tuple[str, str]] | None = None,
    file_download: bool = False,
    check_resume_log_every_message: bool = True,
) -> str:
    """Render <workdir>/AGENTS.md for an optio-codex task.

    ``workdir_exclude=None`` means the codex defaults
    (``snapshots.effective_workdir_exclude``), not the bare framework ones."""
    return compose_instructions_file(
        consumer_instructions,
        profile=PROFILE,
        workdir_exclude=effective_workdir_exclude(workdir_exclude),
        documentation=documentation,
        supports_resume=supports_resume,
        host_protocol=host_protocol,
        omit_task_framing=omit_task_framing,
        fs_isolation_dirs=fs_isolation_dirs,
        file_download=file_download,
        check_resume_log_every_message=check_resume_log_every_message,
    )
```

- [ ] **Step 4: Update `session.py`** — add `from optio_agents.fs_grants import fs_isolation_dirs`; add `fs_isolation_dirs=fs_isolation_dirs(config, host.workdir),` to the fresh-start call (~299) and `fs_isolation_dirs=fs_isolation_dirs(new_config, host.workdir),` to the refresh compose (~887). Both already pass `documentation=protocol.documentation if ….host_protocol else None`.

- [ ] **Step 5: Run** `cd packages/optio-codex && $PYT -q -n 4 --dist loadscope -m "not serial" tests/test_prompt.py tests/test_file_download.py tests/test_config_hooks.py` — Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add -A packages/optio-codex
git commit -m "refactor(optio-codex): compose AGENTS.md through the shared composer; sandbox note"
```

---

### Task 6: optio-cursor

**Files:**
- Rewrite: `packages/optio-cursor/src/optio_cursor/prompt.py`
- Modify: `packages/optio-cursor/src/optio_cursor/session.py` (compose ~417; refresh call ~432; `_maybe_refresh_on_resume` ~1069, its compose ~1087)
- Modify: `packages/optio-cursor/tests/test_prompt.py`; any test calling `_maybe_refresh_on_resume(` directly (`grep -rln "_maybe_refresh_on_resume(" tests`)
- Create: `packages/optio-cursor/tests/test_prompt_threading.py`

**Interfaces:** as Task 3.

- [ ] **Step 1: Tests first.** Append to `tests/test_prompt.py`:

```python
def test_resume_section_names_cursor_store_and_sandbox_note():
    out = compose_agents_md("x", fs_isolation_dirs=[("/wd", "rwx")])
    assert "**Your `home/.cursor/` directory" in out
    assert "**Filesystem access:**" in out


def test_no_prompt_text_of_its_own():
    import inspect
    import optio_cursor.prompt as m
    src = inspect.getsource(m)
    assert "This harness may pause your session" not in src
    assert "You are running inside a coordination harness" not in src
```

Create `tests/test_prompt_threading.py` (pure, xdist-safe; mirrors optio-grok's `test_resume_refresh.py` fakes):

```python
"""The refreshed AGENTS.md carries the SESSION's protocol docs and the sandbox
note, so it matches the fresh-start composition."""

import dataclasses

from optio_agents import get_protocol

from optio_cursor.session import _maybe_refresh_on_resume
from optio_cursor.types import CursorTaskConfig


class _FakeHost:
    workdir = "/tmp/fake-wd"

    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []

    async def write_text(self, path: str, text: str) -> None:
        self.writes.append((path, text))


class _FakeHookCtx:
    async def read_text_from_host(self, path: str, *, silent: bool = False) -> str:
        return "stale"


async def test_refresh_threads_docs_and_sandbox_note():
    cfg = CursorTaskConfig(
        consumer_instructions="original", delivery_type="audit", use_client_messages=True,
        on_resume_refresh=lambda c: dataclasses.replace(c, consumer_instructions="UPDATED"),
    )
    host = _FakeHost()
    protocol = get_protocol(browser="redirect", client_messages=True)
    assert await _maybe_refresh_on_resume(host, _FakeHookCtx(), cfg, protocol) == ["AGENTS.md"]
    (_, text), = host.writes
    assert "UPDATED" in text
    assert "CLIENT_MESSAGE:" in text            # the session's docs, not defaults
    assert "`/tmp/fake-wd`" in text             # sandbox note (fs_isolation default on)
```
(Confirm the config field with `grep -n "client_messages=config\." src/optio_cursor/session.py`.)

- [ ] **Step 2: Run to verify failure** — `cd packages/optio-cursor && $PYT -q tests/test_prompt.py tests/test_prompt_threading.py` — Expected: failures (TypeError / assertion).

- [ ] **Step 3: Rewrite `prompt.py`**

```python
"""AGENTS.md composition for optio-cursor: the profile, plus a thin wrapper
over the shared composer (``optio_agents.prompt.compose_instructions_file``),
which owns all of the text."""

from optio_agents.prompt import AgentPromptProfile, compose_instructions_file
from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES

__all__ = ["PROFILE", "compose_agents_md"]

PROFILE = AgentPromptProfile(
    state_dir="home/.cursor/",
    state_dir_contents="the cursor chat store (conversation history, session state)",
)


def compose_agents_md(
    consumer_instructions: str,
    *,
    workdir_exclude: list[str] | None = None,
    documentation: str | None = None,
    supports_resume: bool = True,
    host_protocol: bool = True,
    omit_task_framing: bool = False,
    fs_isolation_dirs: list[tuple[str, str]] | None = None,
    file_download: bool = False,
    check_resume_log_every_message: bool = True,
) -> str:
    """Render <workdir>/AGENTS.md for an optio-cursor task.

    ``workdir_exclude=None`` means the framework defaults, the same list the
    snapshot archive uses (``optio_host.archive``)."""
    return compose_instructions_file(
        consumer_instructions,
        profile=PROFILE,
        workdir_exclude=(
            list(DEFAULT_WORKDIR_EXCLUDES) if workdir_exclude is None else workdir_exclude
        ),
        documentation=documentation,
        supports_resume=supports_resume,
        host_protocol=host_protocol,
        omit_task_framing=omit_task_framing,
        fs_isolation_dirs=fs_isolation_dirs,
        file_download=file_download,
        check_resume_log_every_message=check_resume_log_every_message,
    )
```

- [ ] **Step 4: Update `session.py`** — add `from optio_agents.fs_grants import fs_isolation_dirs`. Fresh-start call (~417):

```python
            compose_agents_md(
                config.consumer_instructions,
                documentation=protocol.documentation if config.host_protocol else None,
                host_protocol=config.host_protocol,
                workdir_exclude=config.workdir_exclude,
                supports_resume=config.supports_resume,
                fs_isolation_dirs=fs_isolation_dirs(config, host.workdir),
                file_download=config.file_download,
            ),
```
Refresh call (~432): `_maybe_refresh_on_resume(host, hook_ctx, config, protocol)`. Refresh function signature `(host: Host, hook_ctx: HookContext, config: CursorTaskConfig, protocol) -> list[str]` (docstring note as Task 3); its compose:

```python
    new_agents_md = compose_agents_md(
        new_config.consumer_instructions,
        documentation=protocol.documentation if new_config.host_protocol else None,
        host_protocol=new_config.host_protocol,
        workdir_exclude=new_config.workdir_exclude,
        supports_resume=new_config.supports_resume,
        fs_isolation_dirs=fs_isolation_dirs(new_config, host.workdir),
        file_download=new_config.file_download,
    )
```
Any existing test that calls `_maybe_refresh_on_resume(` directly: add the `protocol` argument (`get_protocol(browser="redirect")`) and give its fake host a `workdir = "/tmp/fake-wd"` attribute; where such a test compares against `compose_agents_md(...)` output, add `fs_isolation_dirs=fs_isolation_dirs(cfg, "/tmp/fake-wd")` to that expected composition (or set `fs_isolation=False` on the config).

- [ ] **Step 5: Run** `cd packages/optio-cursor && $PYT -q -n 4 --dist loadscope -m "not serial" tests/test_prompt.py tests/test_prompt_threading.py tests/test_file_download.py tests/test_on_resume_refresh.py tests/test_caller_message_wiring.py` — Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add -A packages/optio-cursor
git commit -m "refactor(optio-cursor): compose AGENTS.md through the shared composer; session docs + sandbox note at both sites"
```

---

### Task 7: optio-grok

**Files:**
- Rewrite: `packages/optio-grok/src/optio_grok/prompt.py`
- Modify: `packages/optio-grok/src/optio_grok/session.py` (compose ~340; refresh call ~336; `_maybe_refresh_on_resume` ~1071, its compose ~1091)
- Modify: `packages/optio-grok/tests/test_prompt.py`, `tests/test_resume_refresh.py`

**Interfaces:** as Task 3.

- [ ] **Step 1: Tests first.** Append to `tests/test_prompt.py`:

```python
def test_identity_line_comes_first_and_no_prompt_text_of_its_own():
    import inspect
    import optio_grok.prompt as m
    out = compose_agents_md("x", fs_isolation_dirs=[("/wd", "rwx")])
    assert out.startswith("You are running inside **Grok Build**")
    assert "**Your `home/.grok/` directory" in out
    assert "**Filesystem access:**" in out
    src = inspect.getsource(m)
    assert "This harness may pause your session" not in src
    assert "You are running inside a coordination harness" not in src
```

In `tests/test_resume_refresh.py`: give `_FakeHost` a class attribute `workdir = "/tmp/fake-wd"`; add `from optio_agents import get_protocol` and `from optio_agents.fs_grants import fs_isolation_dirs`; define `PROTOCOL = get_protocol(browser="redirect")`; change `_agents_md` to

```python
def _agents_md(cfg: GrokTaskConfig) -> str:
    return compose_agents_md(
        cfg.consumer_instructions,
        documentation=PROTOCOL.documentation if cfg.host_protocol else None,
        host_protocol=cfg.host_protocol,
        workdir_exclude=cfg.workdir_exclude,
        supports_resume=cfg.supports_resume,
        fs_isolation_dirs=fs_isolation_dirs(cfg, "/tmp/fake-wd"),
        file_download=cfg.file_download,
    )
```
and every `_maybe_refresh_on_resume(host, hook, cfg)` → `_maybe_refresh_on_resume(host, hook, cfg, PROTOCOL)`. Append:

```python
async def test_refresh_threads_session_docs():
    cfg = GrokTaskConfig(
        consumer_instructions="original", delivery_type="audit", use_client_messages=True,
        on_resume_refresh=lambda c: dataclasses.replace(c, consumer_instructions="UPDATED"),
    )
    host, hook = _FakeHost(), _FakeHookCtx(existing="stale")
    protocol = get_protocol(browser="redirect", client_messages=True)
    assert await _maybe_refresh_on_resume(host, hook, cfg, protocol) == ["AGENTS.md"]
    (_, text), = host.writes
    assert "CLIENT_MESSAGE:" in text and "`/tmp/fake-wd`" in text
```

- [ ] **Step 2: Run to verify failure** — `cd packages/optio-grok && $PYT -q tests/test_prompt.py tests/test_resume_refresh.py` — Expected: failures.

- [ ] **Step 3: Rewrite `prompt.py`**

```python
"""AGENTS.md composition for optio-grok: the profile, plus a thin wrapper over
the shared composer (``optio_agents.prompt.compose_instructions_file``), which
owns all of the shared text. The only grok-specific text is the identity
preamble below."""

from optio_agents.prompt import AgentPromptProfile, compose_instructions_file
from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES

__all__ = ["PROFILE", "compose_agents_md"]

# Always-present identity line. Grok Build infers its environment from ambient
# clues and, running headlessly here, has guessed it is inside Cursor (both are
# xAI-owned) — so state its identity explicitly, regardless of host_protocol.
_GROK_IDENTITY = """You are running inside **Grok Build** (xAI's agentic coding CLI), \
driven headlessly by an automation harness — not Cursor or any other IDE. If asked \
about your environment or identity, you are Grok Build.

"""

PROFILE = AgentPromptProfile(
    state_dir="home/.grok/",
    state_dir_contents="the grok session store (conversation history, plans, session state)",
    preamble=_GROK_IDENTITY,
)


def compose_agents_md(
    consumer_instructions: str,
    *,
    workdir_exclude: list[str] | None = None,
    documentation: str | None = None,
    supports_resume: bool = True,
    host_protocol: bool = True,
    omit_task_framing: bool = False,
    fs_isolation_dirs: list[tuple[str, str]] | None = None,
    file_download: bool = False,
    check_resume_log_every_message: bool = True,
) -> str:
    """Render <workdir>/AGENTS.md for an optio-grok task.

    ``workdir_exclude=None`` means the framework defaults, the same list the
    snapshot archive uses (``optio_host.archive``)."""
    return compose_instructions_file(
        consumer_instructions,
        profile=PROFILE,
        workdir_exclude=(
            list(DEFAULT_WORKDIR_EXCLUDES) if workdir_exclude is None else workdir_exclude
        ),
        documentation=documentation,
        supports_resume=supports_resume,
        host_protocol=host_protocol,
        omit_task_framing=omit_task_framing,
        fs_isolation_dirs=fs_isolation_dirs,
        file_download=file_download,
        check_resume_log_every_message=check_resume_log_every_message,
    )
```

- [ ] **Step 4: Update `session.py`** — add `from optio_agents.fs_grants import fs_isolation_dirs`. Fresh-start call (~340):

```python
                compose_agents_md(
                    config.consumer_instructions,
                    documentation=protocol.documentation if config.host_protocol else None,
                    host_protocol=config.host_protocol,
                    workdir_exclude=config.workdir_exclude,
                    supports_resume=config.supports_resume,
                    fs_isolation_dirs=fs_isolation_dirs(config, host.workdir),
                    file_download=config.file_download,
                ),
```
Refresh call (~336): `_maybe_refresh_on_resume(host, hook_ctx, config, protocol)`. Refresh signature `(host: Host, hook_ctx: HookContext, config: GrokTaskConfig, protocol) -> list[str]`; its compose as in Task 6 Step 4 (with `new_config`, `GrokTaskConfig`).

- [ ] **Step 5: Run** `cd packages/optio-grok && $PYT -q -n 4 --dist loadscope -m "not serial" tests/test_prompt.py tests/test_resume_refresh.py tests/test_file_download.py` — Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add -A packages/optio-grok
git commit -m "refactor(optio-grok): compose AGENTS.md through the shared composer; session docs + sandbox note at both sites"
```

---

### Task 8: optio-kimicode

**Files:**
- Rewrite: `packages/optio-kimicode/src/optio_kimicode/prompt.py`
- Modify: `packages/optio-kimicode/src/optio_kimicode/session.py` (compose ~384; refresh call ~412; `_maybe_refresh_on_resume` ~1182, its compose ~1202)
- Modify: `packages/optio-kimicode/tests/test_prompt.py`, `tests/test_resume_refresh.py`

**Interfaces:** as Task 3.

- [ ] **Step 1: Tests first.** Append to `tests/test_prompt.py`:

```python
def test_sandbox_note_and_no_prompt_text_of_its_own():
    import inspect
    import optio_kimicode.prompt as m
    out = compose_agents_md("x", fs_isolation_dirs=[("/wd", "rwx")])
    assert "**Your `home/.kimi-code/` directory" in out
    assert "**Filesystem access:**" in out
    src = inspect.getsource(m)
    assert "This harness may pause your session" not in src
    assert "You are running inside a coordination harness" not in src
```
`tests/test_resume_refresh.py`: the same edits as Task 7 Step 1 (fake host `workdir`, `PROTOCOL`, `_agents_md` with `documentation=` and `fs_isolation_dirs=`, the 4th argument, and the `test_refresh_threads_session_docs` test with `KimiCodeTaskConfig`).

- [ ] **Step 2: Run to verify failure** — `cd packages/optio-kimicode && $PYT -q tests/test_prompt.py tests/test_resume_refresh.py`.

- [ ] **Step 3: Rewrite `prompt.py`** — identical to Task 6's file with these substitutions: docstring "optio-kimicode"; `PROFILE = AgentPromptProfile(state_dir="home/.kimi-code/", state_dir_contents="the kimi session store (conversation history, session state)")`; docstring "for an optio-kimicode task".

- [ ] **Step 4: Update `session.py`** — as Task 7 Step 4 with `KimiCodeTaskConfig` (fresh-start call ~384, refresh call ~412, refresh function ~1182).

- [ ] **Step 5: Run** `cd packages/optio-kimicode && $PYT -q -n 4 --dist loadscope -m "not serial" tests/test_prompt.py tests/test_resume_refresh.py` — Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add -A packages/optio-kimicode
git commit -m "refactor(optio-kimicode): compose AGENTS.md through the shared composer; session docs + sandbox note at both sites"
```

---

### Task 9: optio-antigravity

**Files:**
- Rewrite: `packages/optio-antigravity/src/optio_antigravity/prompt.py`
- Modify: `packages/optio-antigravity/src/optio_antigravity/session.py` (compose ~279; refresh call ~275; `_maybe_refresh_on_resume` ~852, its compose ~870)
- Modify: `packages/optio-antigravity/tests/test_prompt.py`; any direct `_maybe_refresh_on_resume(` caller in `tests/` (`grep -rln`)
- Create: `packages/optio-antigravity/tests/test_prompt_threading.py`

**Interfaces:** as Task 3.

- [ ] **Step 1: Tests first.** Append to `tests/test_prompt.py`:

```python
def test_sandbox_note_and_no_prompt_text_of_its_own():
    import inspect
    import optio_antigravity.prompt as m
    out = compose_agents_md("x", fs_isolation_dirs=[("/wd", "rwx")])
    assert "**Your `home/.gemini/antigravity/` directory" in out
    assert "**Filesystem access:**" in out
    src = inspect.getsource(m)
    assert "This harness may pause your session" not in src
    assert "You are running inside a coordination harness" not in src
```
Create `tests/test_prompt_threading.py` as Task 6 Step 1's file, with `optio_antigravity.session`, `optio_antigravity.types.AntigravityTaskConfig`.

- [ ] **Step 2: Run to verify failure** — `cd packages/optio-antigravity && $PYT -q tests/test_prompt.py tests/test_prompt_threading.py`.

- [ ] **Step 3: Rewrite `prompt.py`** — identical to Task 6's file with: docstring "optio-antigravity"; `PROFILE = AgentPromptProfile(state_dir="home/.gemini/antigravity/", state_dir_contents="Antigravity's own state store (the conversation transcript, artifacts, settings)")`; docstring "for an optio-antigravity task".

- [ ] **Step 4: Update `session.py`** — as Task 7 Step 4 with `AntigravityTaskConfig` (fresh-start call ~279, refresh call ~275, refresh function ~852). Fix any direct test callers as described in Task 6 Step 4.

- [ ] **Step 5: Run** `cd packages/optio-antigravity && $PYT -q -n 4 --dist loadscope -m "not serial" tests/test_prompt.py tests/test_prompt_threading.py tests/test_file_download.py tests/test_ported_features.py` — Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add -A packages/optio-antigravity
git commit -m "refactor(optio-antigravity): compose AGENTS.md through the shared composer; session docs + sandbox note at both sites"
```

---

### Task 10: Guard test in optio-agents-all

**Files:**
- Create: `packages/optio-agents-all/tests/test_prompt_dedupe_guard.py`

- [ ] **Step 1: Write the test**

```python
"""Guard: no wrapper carries instructions-file prompt text of its own.

All of it lives in ``optio_agents.prompt``; a wrapper's ``prompt.py`` holds a
``PROFILE`` and a thin ``compose_agents_md``. The phrases below occur only in
the shared templates, so a copy-pasted resume section, intro or task framing
fails here instead of drifting for months."""

import importlib
import inspect

import pytest

WRAPPERS = (
    "optio_opencode", "optio_claudecode", "optio_codex", "optio_cursor",
    "optio_grok", "optio_kimicode", "optio_antigravity",
)
SHARED_ONLY_PHRASES = (
    "This harness may pause your session",
    "You are running inside a coordination harness",
    "Here comes the description of your actual task",
    "### Detecting a resume",
    "originate from the harness coordinating this session",
    "**Filesystem access:**",
)


@pytest.mark.parametrize("pkg", WRAPPERS)
def test_wrapper_prompt_module_has_no_shared_prompt_text(pkg):
    mod = importlib.import_module(f"{pkg}.prompt")
    src = inspect.getsource(mod)
    for phrase in SHARED_ONLY_PHRASES:
        assert phrase not in src, f"{pkg}.prompt carries shared prompt text: {phrase!r}"
    assert hasattr(mod, "PROFILE") and hasattr(mod, "compose_agents_md")


@pytest.mark.parametrize("pkg", WRAPPERS)
def test_wrapper_compose_signature_is_uniform(pkg):
    mod = importlib.import_module(f"{pkg}.prompt")
    params = list(inspect.signature(mod.compose_agents_md).parameters)
    assert params == [
        "consumer_instructions", "workdir_exclude", "documentation", "supports_resume",
        "host_protocol", "omit_task_framing", "fs_isolation_dirs", "file_download",
        "check_resume_log_every_message",
    ]
```

- [ ] **Step 2: Run** — `cd packages/optio-agents-all && $PYT -q tests/test_prompt_dedupe_guard.py` — Expected: pass (Tasks 3–9 done). Sanity-check the guard bites: temporarily add `X = "### Detecting a resume"` to `optio_grok/prompt.py`, rerun (expected: 1 failure), revert.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-agents-all/tests/test_prompt_dedupe_guard.py
git commit -m "test(optio-agents-all): guard against prompt text creeping back into wrapper prompt modules"
```

---

### Task 11: Wrapper guide

**Files:**
- Modify: `docs/writing-agent-wrappers.md` (Part 2D ~267–296; Stage 2 ~316–333; Appendix A row 18 ~876; Appendix B table ~898–921)

- [ ] **Step 1: Replace Part 2D's "Interface to implement" and "Reference" paragraphs**

Replace the paragraph starting `**Interface to implement.** Compose the file from:` with:

```markdown
**Interface to implement.** Do not write prompt text. Declare an
`AgentPromptProfile` (`optio_agents.prompt`) with what is specific to your agent
— the instructions filename, the state directory that survives a resume and
what it holds, an optional preamble, an optional extra section — and expose a
thin `compose_agents_md(consumer_instructions, *, workdir_exclude=None,
documentation=None, supports_resume=True, host_protocol=True,
omit_task_framing=False, fs_isolation_dirs=None, file_download=False,
check_resume_log_every_message=True)` that resolves your effective exclude list
and calls `compose_instructions_file(..., profile=PROFILE, ...)`. The shared
composer owns the intro, the keyword-protocol docs, the resume section, the
`System:` explainer, the task framing, the downloads note and the sandbox note.
At both places the session writes the file (fresh start and
`_maybe_refresh_on_resume`, which takes the session's `protocol`), pass
`documentation=protocol.documentation if config.host_protocol else None` and
`fs_isolation_dirs=fs_isolation_dirs(config, host.workdir)`
(`optio_agents.fs_grants`). A guard test in optio-agents-all fails if your
`prompt.py` contains shared prompt text.
```

Replace the `**Reference.**` paragraph of Part 2D with:

```markdown
**Reference.** `optio-agents/…/prompt.py` (`AgentPromptProfile`,
`compose_instructions_file`), `optio-agents/…/protocol/prompt.py`
(`build_log_channel_prompt`, `RESUME_NOTICE`); any wrapper's `prompt.py` for
the profile shape (grok's has a preamble, claudecode's an extra section, codex
resolves its own exclude defaults).
```

- [ ] **Step 2: Stage 2 reference** — in the Stage 2 paragraph, replace `the resume section in `prompt.py`` with `the resume section in `optio-agents/…/prompt.py` (rendered by `compose_instructions_file`)`.

- [ ] **Step 3: Appendix A row 18** — change to
`| 18 | prompt composition from SSOT (profile + thin wrapper; session docs and sandbox dirs at both write sites) | req | `prompt.py` (`PROFILE`, `compose_agents_md`), `optio-agents/…/prompt.py` |`

- [ ] **Step 4: Appendix B** — after the `build_log_channel_prompt`, `RESUME_NOTICE` row add:
`| `AgentPromptProfile`, `compose_instructions_file`, `DEFAULT_CONVERSATION_INSTRUCTIONS` | `optio-agents/…/prompt.py` |`
`| `fs_isolation_dirs` (sandbox note input), `build_grant_flags` | `optio-agents/…/fs_grants.py` |`

- [ ] **Step 5: Commit**

```bash
git add docs/writing-agent-wrappers.md
git commit -m "docs(guide): prompt composition = profile + shared composer; docs and sandbox dirs at both write sites"
```

---

### Task 12: Remove the old shared `compose_agents_md`

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/prompt.py`

- [ ] **Step 1: Verify no callers** — `git grep -n "_compose_agents_md_host\|optio_agents.prompt import compose_agents_md\|optio_agents.prompt import _INTRO"` must print nothing (Tasks 4 and 5 removed them). Also `grep -rn "compose_agents_md" ~/deai/excavator/packages --include=*.py` must print nothing.
- [ ] **Step 2: Delete** the `compose_agents_md` function from `optio_agents/prompt.py`.
- [ ] **Step 3: Run** `cd packages/optio-agents && $PYT -q tests/` — Expected: pass.
- [ ] **Step 4: Commit** — `git commit -am "refactor(optio-agents): drop the old outer-framing compose_agents_md"`.

---

### Task 13: Render diff against `main`

**Files:**
- Create (temporary, not committed): `/tmp/render_prompts.py`

- [ ] **Step 1: Write the script**

```python
"""Render every wrapper's instructions file over an option matrix; print as one text.

Works on both trees: arguments a wrapper's compose_agents_md does not accept
(on main: documentation/omit_task_framing/fs_isolation_dirs on some wrappers)
are dropped, which is exactly what those wrappers do today."""
import importlib, inspect, itertools
from optio_agents import get_protocol

WRAPPERS = ["opencode", "claudecode", "codex", "cursor", "grok", "kimicode", "antigravity"]
BROWSER = {"opencode": "suppress"}

for w in WRAPPERS:
    fn = importlib.import_module(f"optio_{w}.prompt").compose_agents_md
    accepted = set(inspect.signature(fn).parameters)
    for hp, sr, otf, fd, ex, msgs in itertools.product(
        (True, False), (True, False), (False, True), (False, True),
        (None, [], ["custom"]), (False, True),
    ):
        if otf and "omit_task_framing" not in accepted:
            continue
        proto = get_protocol(browser=BROWSER.get(w, "redirect"), client_messages=msgs, caller_messages=msgs)
        kw = dict(
            workdir_exclude=ex, supports_resume=sr, host_protocol=hp, file_download=fd,
            omit_task_framing=otf, documentation=proto.documentation if hp else None,
            fs_isolation_dirs=[("/wd", "rwx"), ("~/tools", "rox")],
        )
        kw = {k: v for k, v in kw.items() if k in accepted}
        print(f"===== {w} hp={hp} sr={sr} otf={otf} fd={fd} ex={ex} msgs={msgs}")
        print(fn("DO THE TASK", **kw))
```

- [ ] **Step 2: Render both sides**

```bash
git -C ~/deai/optio worktree add /tmp/optio-main-ro main   # read-only reference checkout
PY=~/deai/optio/.venv/bin/python
PYTHONPATH=$(ls -d /tmp/optio-main-ro/packages/*/src | paste -sd:) $PY /tmp/render_prompts.py > /tmp/render-main.txt
PYTHONPATH=$(ls -d ~/deai/optio-prompt-dedupe/packages/*/src | paste -sd:) $PY /tmp/render_prompts.py > /tmp/render-branch.txt
diff -u /tmp/render-main.txt /tmp/render-branch.txt > /tmp/render.diff; wc -l /tmp/render.diff
git -C ~/deai/optio worktree remove /tmp/optio-main-ro
```
(On `main`, four wrappers accept no `documentation` and six no `fs_isolation_dirs`; the script drops those, so the diff shows the docs and sandbox-note changes as expected additions.)

- [ ] **Step 3: Review the diff.** Every `+`/`-` hunk must be one of: the intro heading; a state-dir bullet rewording/rewrap; the `System:` explainer spacing (codex) or its presence without a resume section; claudecode's "## Getting text to the user" section; the sandbox note on the six other wrappers; `CLIENT_MESSAGE:`/`CALLER_MESSAGE:` docs appearing for cursor/grok/kimicode/antigravity when `msgs=True`. Anything else is a bug: fix it in the composer or wrapper, add a test for it, rerun. Save the reviewed diff as `/tmp/render.diff` for the report to the owner.

---

### Task 14: Full suites

- [ ] **Step 1: Run all affected packages the way `make test` does**

```bash
cd ~/deai/optio-prompt-dedupe
export PYTHONPATH=$(ls -d $PWD/packages/*/src | paste -sd:)
PYT="$HOME/deai/optio/.venv/bin/python -m pytest -p no:cacheprovider"
W=$(n=$(nproc); w=$((n/2)); [ $w -lt 2 ] && w=2; echo $w)
for p in optio-agents optio-agents-all optio-opencode optio-claudecode optio-codex optio-cursor optio-grok optio-kimicode optio-antigravity; do
  echo ">>> $p"; (cd packages/$p && $PYT -q -n $W --dist loadscope -m "not serial" 2>&1 | tail -3; $PYT -q -m serial 2>&1 | tail -2)
done
```
Expected: every package green (a serial phase with "no tests ran" / exit 5 is fine). Known pre-existing flake: optio-claudecode tests sharing `process_id="p"` under xdist (1–6 failures per run, pass serially) — rerun the failing files serially before treating them as regressions.

- [ ] **Step 2: Record results** in the final report (counts per package) and rebase on `origin/main` if it moved: `git fetch origin && git rebase origin/main` (the other agent's branch may have merged; no file overlap expected).

---

### Task 15: Hand back

- [ ] Report to the owner: commits on `agent-prompt-composer`, the test counts, `/tmp/render.diff` reviewed with the list of hunk categories seen, and ask whether to fast-forward/merge into `main` and push, then whether to run the patch release wave (`optio-agents` 0.6.1, the seven wrappers with `optio-agents>=0.6.1,<0.7`; see the spec's Rollout).
- [ ] After merge: remove the worktree (`git -C ~/deai/optio worktree remove ~/deai/optio-prompt-dedupe; git -C ~/deai/optio branch -d agent-prompt-composer`), update the commissura crew line and topics.log.
