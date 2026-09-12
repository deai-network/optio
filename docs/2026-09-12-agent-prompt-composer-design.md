# Agent prompt composer: one shared composer for the seven wrappers

Date: 2026-09-12. Status: design approved by the owner; not yet implemented.

Scope: the instructions file each optio agent wrapper writes into its workdir
(`CLAUDE.md` for optio-claudecode, `AGENTS.md` for the other six). That is
`optio-agents/src/optio_agents/prompt.py`, every
`optio-<agent>/src/optio_<agent>/prompt.py`, the two call sites in each wrapper's
`session.py`, their tests, and `docs/writing-agent-wrappers.md`. The
`resume.log` writers and the resume-notice helpers are out of scope (a separate
run, see the end).

## Problem

**Seven copies of the same prompt text.** Each wrapper's `prompt.py` (184–234
lines) carries its own copy of the resume section (about 60 lines) and the
`System:` explainer; five also carry their own intro and task framing. A shared
composer exists (`optio_agents.prompt.compose_agents_md`, since 2026-05-29), but
it covers only the outer framing (intro, protocol docs, task framing), and its
docstring leaves resume content to each package. Only optio-claudecode and
optio-codex call it.

**How it got here** (git history on `main`):

1. opencode (2026-04-23) came first, with a complete composer of its own.
2. claudecode (2026-05-28) copied opencode's text; its docstring still says the
   resume text is "byte-identical to optio-opencode's, with one added bullet".
3. `75a70533` (2026-05-29) created the shared composer; only claudecode was
   switched to it.
4. June's features landed per package: conversation mode (`host_protocol`,
   `omit_task_framing`) in opencode and claudecode separately, file download in
   both (only `downloadables_block` shared), the sandbox note in claudecode only.
5. July 2–6: five wrappers were built by copying a sibling (grok from opencode,
   cursor and kimicode from grok, antigravity mirroring grok); only codex used
   the shared composer. The wrapper guide points authors at "wrappers'
   `prompt.py` in both packages", so copying the nearest sibling was the easy
   path.

**What the copies cost:**

1. **Accidental divergence.** Three intro headings ("with the host (optio)",
   "with the host harness", "with the host (optio-cursor)"); codex's `System:`
   explainer has different spacing and appears even without a resume section;
   each state-dir bullet is phrased differently.
2. **Features stuck in one wrapper.** Only claudecode tells the agent its sandbox
   bounds, although all seven run under claustrum with `fs_isolation` on by
   default (`ClaustrumConfigMixin`). Only claudecode can switch off the
   per-message `resume.log` check (`6aa2d269`).
3. **Protocol docs not taken from the session (a bug).** cursor, grok, kimicode
   and antigravity build the session's `protocol` with `client_messages` /
   `caller_messages`, but their `prompt.py` rebuilds the docs from
   `ProtocolFeatures(browser="redirect")` alone, so a task that turns those
   messages on never gets them documented. Separately, six of the seven
   resume-refresh functions (all but codex) recompose without the session's
   docs: for an opencode or claudecode task with those messages on, the first
   refresh on resume rewrites the file without their docs and tags it
   `REFRESHED`.

## Decisions (owner, 2026-09-12)

- **Normalize** accidental differences; only real per-agent variation becomes a
  parameter.
- **Profile plus thin wrappers**, chosen over "profile, no wrappers" and "plain
  keyword arguments".
- **Sandbox note for all seven agents**: in scope.
- **Resume writer and notice helper dedupe: postponed** to a separate run. It is
  a more fragile area that needs line-by-line reading and careful testing.
- **Claude Code's profile carries a "Getting text to the user" section**
  (wording below). It was requested through the AoE expert (aoe:d7282b53f1ed) and
  approved by the owner.

## Design

### 1. Shared composer (`optio-agents/src/optio_agents/prompt.py`)

```python
@dataclass(frozen=True)
class AgentPromptProfile:
    instructions_file: str = "AGENTS.md"  # name used in the REFRESHED: example
    state_dir: str | None = None          # e.g. "home/.claude/"; None = no bullet
    state_dir_contents: str = ""          # e.g. "credentials, settings, and ..."
    preamble: str = ""                    # rendered first (grok's identity line)
    browser: str = "redirect"             # only for fallback docs, see below
    delivery_section: str = ""            # rendered before the task framing

def compose_instructions_file(
    consumer_instructions: str,
    *,
    profile: AgentPromptProfile,
    workdir_exclude: list[str],           # effective list, already resolved
    documentation: str | None = None,
    supports_resume: bool = True,
    host_protocol: bool = True,
    omit_task_framing: bool = False,
    fs_isolation_dirs: list[tuple[str, str]] | None = None,
    file_download: bool = False,
    check_resume_log_every_message: bool = True,
) -> str: ...
```

Rendered order (the same order as today):

1. `profile.preamble`, if set.
2. If `host_protocol`: the intro, then the protocol docs. Sessions always pass
   their `protocol.documentation`; when `documentation` is `None` (unit tests,
   standalone callers) the docs are built from
   `ProtocolFeatures(browser=profile.browser)`.
3. If `supports_resume`: the resume section.
4. If not `host_protocol`: the `System:` explainer, with or without a resume
   section.
5. `profile.delivery_section`, if set.
6. The task framing (`BASE_PROMPT_POST`), unless `omit_task_framing`.
7. The consumer instructions, followed by the downloads note (`file_download`)
   and then the sandbox note (`fs_isolation_dirs`), claudecode's current order.

Blocks are joined the way today's composers join them, and the output ends with
one newline.

Text the module owns:

- **Intro**: heading normalized to `# Coordination protocol with the host
  harness`; the body is unchanged.
- **`BASE_PROMPT_POST`** (already here) and **`DEFAULT_CONVERSATION_INSTRUCTIONS`**
  (moved from opencode and claudecode).
- **Resume section**: claudecode's current template (`6aa2d269`), with both
  detection variants (poll `resume.log` on every message, or rely on the
  `System: you have been resumed` notice), generalized:
  - `profile.instructions_file` in the `REFRESHED:` example and the
    `cat ./<file>` hint;
  - the state-dir bullet only when `profile.state_dir` is set, in one sentence
    shape: "- **Your `<state_dir>` directory — <state_dir_contents> — IS
    preserved across resumes**, so your history travels with you even when the
    underlying process and host change.";
  - the exclude clause from `workdir_exclude`; an empty list renders the "No
    paths are excluded" wording, as today.
- **`System:` explainer**: one copy, the leading-newline variant six wrappers use.
- **Sandbox note**: moved verbatim from claudecode.
- **`downloadables_block`**: unchanged.

Removed: the old `compose_agents_md(consumer_instructions, *, documentation,
resume_section)`. Only claudecode and codex call it; nothing outside the
monorepo imports it (excavator checked; `optio_agents/__init__.py` does not
export it).

### 2. Sandbox dirs helper (`optio-agents/src/optio_agents/fs_grants.py`)

`fs_isolation_dirs(config, workdir) -> list[tuple[str, str]] | None`, moved from
claudecode's `session._fs_isolation_dirs`: `None` when `config.fs_isolation` is
off, otherwise the workdir (`rwx`) followed by `config.extra_allowed_dirs` as
`(path, mode)` pairs. Paths stay verbatim (including `~/`), because the agent's
own `$HOME` view is what it needs. It works for every wrapper, since all
configs inherit `ClaustrumConfigMixin`.

### 3. Wrappers (`optio-<agent>/src/optio_<agent>/prompt.py`)

Each shrinks to a docstring, a `PROFILE`, and one function with the same name and
signature in all seven:

```python
def compose_agents_md(consumer_instructions, *, workdir_exclude=None,
        documentation=None, supports_resume=True, host_protocol=True,
        omit_task_framing=False, fs_isolation_dirs=None, file_download=False,
        check_resume_log_every_message=True) -> str:
    return compose_instructions_file(
        consumer_instructions, profile=PROFILE,
        workdir_exclude=<effective excludes for this agent>(workdir_exclude),
        documentation=documentation, supports_resume=supports_resume,
        host_protocol=host_protocol, omit_task_framing=omit_task_framing,
        fs_isolation_dirs=fs_isolation_dirs, file_download=file_download,
        check_resume_log_every_message=check_resume_log_every_message)
```

- **Exclude resolution**: codex uses `snapshots.effective_workdir_exclude`; the
  other six use `optio_host.archive.DEFAULT_WORKDIR_EXCLUDES` when given `None`.
  opencode's `workdir_exclude` stops being mandatory, since `None` already means
  the same defaults its snapshot uses (`optio_host/archive.py:76`).
- opencode and claudecode re-export `DEFAULT_CONVERSATION_INSTRUCTIONS`, which
  their sessions and tests import from there.
- `_render_resume_section` disappears from the wrappers; tests that import it
  move to optio-agents (see Testing).

Profiles:

| wrapper | `instructions_file` | `state_dir` | `state_dir_contents` | other |
|---|---|---|---|---|
| opencode | `AGENTS.md` | none | none | `browser="suppress"` |
| claudecode | `CLAUDE.md` | `home/.claude/` | credentials, settings, and the conversation transcript | `delivery_section` |
| codex | `AGENTS.md` | `home/.codex/` | the codex session store (rollout files under `home/.codex/sessions`, auth, config) | own exclude defaults |
| cursor | `AGENTS.md` | `home/.cursor/` | the cursor chat store (conversation history, session state) | |
| grok | `AGENTS.md` | `home/.grok/` | the grok session store (conversation history, plans, session state) | `preamble` = grok's identity line, verbatim |
| kimicode | `AGENTS.md` | `home/.kimi-code/` | the kimi session store (conversation history, session state) | |
| antigravity | `AGENTS.md` | `home/.gemini/antigravity/` | Antigravity's own state store (the conversation transcript, artifacts, settings) | |

Claude Code's `delivery_section`, verbatim:

```markdown
## Getting text to the user

Text you write in a response that also calls a tool may never reach the user: with extended thinking on it can end up in your hidden reasoning, and at most a short summary is shown. So:

- Anything the user must read in full (drafts, spec sections, reports, long answers) goes in a response with no tool call after it, or in a file you write and name the path of.
- When you ask the user to review something with AskUserQuestion, put the full text in the question itself or in a file, never in text before the call.
- Never assume the user saw text you wrote before a tool call.
```

### 4. Call sites (`optio-<agent>/src/optio_<agent>/session.py`)

Two per wrapper: the fresh-start write and `_maybe_refresh_on_resume`. Line
numbers on `main` at `6aa2d269`:

| wrapper | fresh-start compose | refresh function (its compose) | `get_protocol` |
|---|---|---|---|
| opencode | 343 | 1033 (1053) | 192 |
| claudecode | 1117 | 1518 (1542) | 147 |
| codex | 299 | 864 (887) | 122 |
| cursor | 417 | 1069 (1087) | 208 |
| grok | 340 | 1071 (1091) | 151 |
| kimicode | 384 | 1182 (1202) | 244 |
| antigravity | 279 | 852 (870) | 101 |

Changes at every site:

1. `documentation=protocol.documentation if <config>.host_protocol else None`.
   Each `_maybe_refresh_on_resume` takes the session's `protocol` as a
   parameter, as codex's already does, passed down from where it is called.
2. `fs_isolation_dirs=fs_isolation_dirs(<config>, <host>.workdir)`, with the
   host each site uses today. claudecode's local `_fs_isolation_dirs` is deleted.
3. claudecode keeps `check_resume_log_every_message=_checks_resume_log_every_message(<config>)`.
   The other six do not pass it, so they keep polling `resume.log`.

### 5. Wrapper guide (`docs/writing-agent-wrappers.md`)

- Part 2D: replace "compose the file from …" and the pointer to "wrappers'
  `prompt.py`" with: declare an `AgentPromptProfile`, expose a thin
  `compose_agents_md` that calls `compose_instructions_file`, and pass the
  session's `protocol.documentation` and `fs_isolation_dirs(config,
  host.workdir)` at both call sites. The "resume awareness has two halves" text
  stays; the pull half now comes from the composer.
- Stage 2 "Reference": the resume section lives in the composer.
- Appendix A row 18 and Appendix B: add `AgentPromptProfile`,
  `compose_instructions_file` (`optio-agents/…/prompt.py`) and
  `fs_isolation_dirs` (`optio-agents/…/fs_grants.py`).

## Text changes an agent will see

Everything else must render byte for byte as on `main`; the render diff (see
Testing) enforces this.

- **All seven**: the intro heading (claudecode and codex lose "(optio)", cursor
  loses "(optio-cursor)").
- **State-dir bullet**: claudecode "your identity and history travel" becomes
  "your history travels"; codex loses "(minus the excluded paths above)" (the
  exclude clause above it already says so); the others change at most in line
  wrapping.
- **`host_protocol` off**: the `System:` explainer now appears even without a
  resume section (was codex only); codex's explainer spacing changes.
- **claudecode**: gains the "Getting text to the user" section.
- **The six others**: gain the sandbox note (when `fs_isolation` is on, the
  default).
- **cursor, grok, kimicode, antigravity**: document `CLIENT_MESSAGE:` /
  `CALLER_MESSAGE:` when the task enables them.
- **Refresh on resume**: renders the same docs as the fresh start (opencode and
  claudecode tasks with those messages on no longer lose them).

## Edge cases

- `host_protocol=True` with `documentation=None`: fallback docs from
  `profile.browser`. Sessions never hit this; they always pass their protocol's
  docs.
- `workdir_exclude=[]`: the "No paths are excluded" wording.
- `supports_resume=False`: no resume section, no state-dir bullet;
  `check_resume_log_every_message` has no effect.
- `omit_task_framing=True`: the delivery section is still rendered; only the
  framing is dropped.
- `fs_isolation=False`: the helper returns `None`, so no sandbox note.
- Running sessions get the new text at their next resume when the engine sets
  `on_resume_refresh` (the rewrite is tagged `REFRESHED`, so the agent re-reads
  it); otherwise at their next fresh start.

## Testing

- **optio-agents unit tests** (new `tests/test_prompt_composer.py`): each block
  present or absent per option, the rendered order, both resume detection
  variants, every profile field, the delivery section with and without task
  framing, and `fs_isolation_dirs`.
- **Moved tests**: claudecode's `test_resume_prompt.py` and
  `test_resume_sentence_claudecode.py` and opencode's
  `test_resume_sentence_opencode.py` assert shared text, so their shared parts
  move to optio-agents; the wrappers keep agent-specific assertions (state dir,
  file name, preamble).
- **Guard test** (optio-agents-all, which depends on all seven wrappers): for each
  wrapper, the source of its `prompt` module must not contain phrases that occur
  only in the shared template text: `This harness may pause your session`,
  `You are running inside a coordination harness`, `Here comes the description
  of your actual task`, `### Detecting a resume`. (Not bare headings like
  `## Task`, which docstrings legitimately mention.) The next copy-paste then
  fails in CI.
- **Session tests per wrapper**: `CLIENT_MESSAGE:` is documented when
  `use_client_messages=True`, at both the fresh-start and the refresh site; the
  sandbox note is present by default.
- **Render diff before merging**: render each wrapper's file over an option
  matrix (`host_protocol`, `supports_resume`, `omit_task_framing`,
  `file_download`, excludes `None`/`[]`/custom, client/caller messages on/off) on
  `main` and on the branch. Every changed line must be one of the text changes
  listed above.
- **Full suites**: optio-agents, optio-agents-all and all seven wrappers, the way
  `make test` runs them (the xdist phase, then the serial phase), in the worktree
  with `PYTHONPATH` pointing at the worktree's `src` directories.

## Rollout

- Work happens on branch `agent-prompt-composer` in the worktree
  `excavator:~/deai/optio-prompt-dedupe`, merged into `main` when done.
- Release, as a patch wave per `docs/release-cookbook.md`, a separate step the
  owner approves:
  1. optio-agents 0.6.1;
  2. the seven wrappers as patch releases, with `optio-agents>=0.6.1,<0.7`
     (claudecode 0.6.3 also carries `6aa2d269`);
  3. no optio-agents-all release: its pins already accept the new wrapper
     patches;
  4. no excavator change.
- Dev: excavator's engine runs optio from `~/deai/optio`, so the new text reaches
  it once that checkout includes `main`.

## Out of scope

- The six `_append_resume_log_entry` copies and five `build_resume_notice_args`
  copies: the next run.
- Resume-notice guarantees for the six other agents; they keep polling
  `resume.log`.
- Excavator's frontend missing an `antd` entry in Vite's `dedupe` list (in the
  bring-up gotchas guide).
