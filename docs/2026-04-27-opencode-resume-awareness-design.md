# Opencode Resume-Awareness: `resume.log` + `supports_resume` opt-out

**Base revision:** `e0def2a97f9914f5a1f52eefcdc79f3ecbd6b615` on branch `main` (as of 2026-04-27T11:58:34Z)

## Summary

Give opencode-based tasks an explicit, in-band signal of cancel/resume so
the LLM agent can detect when the host environment may have changed. The
mechanism: a `<workdir>/resume.log` file with one ISO 8601 timestamp per
line (first line = original launch, each later line = a resume), plus a
new section in the framework prompt teaching the agent how to consume it.
Bundled with the same change: a `supports_resume: bool = True` field on
`OpencodeTaskConfig` so consumer apps can opt out of the resume cycle
entirely (no snapshot capture, no `resume.log`, no resume prompt
section).

## Motivation

The cancel/resume cycle is fully transparent to the agent: the LLM's
context is preserved verbatim across snapshot/restore, so from its point
of view the conversation never paused. That is the right design for not
disrupting the agent's reasoning, but it has a real cost. The agent's
memory says "I just put a server in `/tmp/`, it's running, I have its
PID" — but `/tmp` and the process did not survive the resume. The agent
operates on a stale environmental model and gets surprised by failing
tool calls.

The fix is to give the agent an honest, in-band signal of resume —
readable on demand, costless when not consulted, and tied to the
workdir's snapshot lifecycle so the existence of the signal itself is
consistent. `resume.log` does that with ~25 bytes per resume, no new
tool plumbing for opencode, and minimal prompt budget (~260 words for
the new section).

The proposed mechanism (file + prompt instruction) was chosen over two
alternatives:

- **Inject a synthetic user message on resume** ("you've been resumed at
  T"). Rejected: breaks the transparent-rehydration design, pollutes the
  agent's context with framework artifacts, costs tokens at every
  subsequent turn.
- **Custom `check_resume()` opencode tool**. Rejected: requires hooking
  a custom tool into opencode's tool catalog (invasive, opencode core
  change), and the agent still needs a prompt instruction telling it to
  call the tool — so we save nothing over the file-based approach.

The opt-out flag is a separate but coupled change. The framework
currently hardcodes `supports_resume=True` on every opencode task; this
spec moves that decision to the consumer, defaulting to current
behavior so existing callers do not change.

## Goals

1. Every fresh opencode session writes one ISO 8601 timestamp line to
   `<workdir>/resume.log`. Every resume appends one more line.
2. The agent receives, in the framework prompt, instructions to:
   - Read `./resume.log` at the start of every new user message.
   - Treat a new line (relative to its remembered "latest") as a resume.
   - Verify out-of-workdir state on resume detection.
   - Use a failing tool call as a backup detection signal.
3. The agent receives an accurate, configurable list of which paths in
   the workdir are NOT preserved across snapshots (i.e., the effective
   `workdir_exclude` patterns).
4. `OpencodeTaskConfig` exposes `supports_resume: bool = True`. Setting
   it to `False` skips snapshot capture, skips `resume.log`, and omits
   the resume section from the prompt — end-to-end consistent.

## Non-Goals

- No per-task resume-recovery hooks. The prompt's generic guidance
  ("verify any tools, processes, or files you previously gathered
  outside the workdir") is enough for now. If real consumer needs
  surface, we can add a hook later.
- No mechanism to recover state lost outside the workdir. The agent
  has to handle that itself per the prompt instructions.
- No backwards-compatibility shim for snapshots taken before this
  feature. Pre-feature snapshots resume with the old transparent
  behavior; new snapshots get the new behavior. The shift is forward-
  only as old snapshots age out.
- No tests asserting that the LLM actually follows the prompt
  instructions. That is opencode-side behavior, untestable from our
  side. The contract is documented; the framework's job is to provide
  the file and the prompt, not to enforce agent compliance.

## `OpencodeTaskConfig` change

Add one field, after `workdir_exclude`, before `before_execute`:

```python
@dataclass
class OpencodeTaskConfig:
    consumer_instructions: str
    opencode_config: dict[str, Any] = field(default_factory=dict)
    ssh: SSHConfig | None = None
    on_deliverable: DeliverableCallback | None = None
    install_if_missing: bool = True
    workdir_exclude: list[str] | None = None
    supports_resume: bool = True             # NEW
    before_execute: HookCallback | None = None
    after_execute: HookCallback | None = None
```

`create_opencode_task` (`session.py:625-632` today) plumbs this through
to the `TaskInstance`:

```python
return TaskInstance(
    execute=_execute,
    process_id=process_id,
    name=name,
    description=description,
    ui_widget="iframe",
    supports_resume=config.supports_resume,    # was hardcoded True
)
```

No call-site changes for existing consumers. The default preserves
current behavior.

## `resume.log` mechanic

**Path.** `<workdir>/resume.log`.

**Format.** One ISO 8601 UTC timestamp per line, seconds precision,
`Z` suffix. Example file after one resume:

```
2026-04-27T12:00:34Z
2026-04-27T13:15:02Z
```

The first line is the launch timestamp. Each subsequent line is a
resume.

**When the framework appends.** A new step `_append_resume_log_entry`
in `session.py`, called immediately after the fresh-vs-resume branch
finishes and before `_install_or_ensure_binary`. Position rationale:

- On fresh start: by this point, `setup_workdir`, `write_text("AGENTS.md", ...)`,
  and `write_text("opencode.json", ...)` have run. We append the first
  timestamp, creating the file.
- On resume: by this point, `restore_workdir(...)` has already pulled
  the previous `resume.log` into the workdir (with all prior
  timestamps). We append one more line.

The shell append handles both cases identically — `>>` creates the file
if missing, appends if present.

**Implementation primitive.** No new `Host` method. The append is one
shell line:

```python
async def _append_resume_log_entry(host) -> None:
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

The timestamp is computed on the **worker** (not the host) — the
worker is the orchestrator and its clock is authoritative. The host
might have NTP drift; we want all timestamps in `resume.log` on a
single clock that the orchestrator controls. The wall-clock UTC is
consistent because `datetime.now(timezone.utc)` is independent of host
TZ.

**`workdir_exclude`.** `resume.log` is small (a few KB even after
thousands of resumes); no need to add it to the default exclude list.
The defaults (`.git`, `node_modules`, `__pycache__`, `.venv`, `*.pyc`,
`.DS_Store`) do not match it, so it is preserved across snapshots. No
change to the defaults.

## Prompt template change in `compose_agents_md`

**Current `prompt.py` structure:**

```
BASE_PROMPT (constant)
  ## Coordination protocol with the host (optio-opencode)
  ## Log channel
  ## Deliverables
  ## Task

compose_agents_md(consumer_instructions) -> BASE_PROMPT + consumer_instructions
```

**New structure:**

```
BASE_PROMPT_PRE  (constant — top through "## Deliverables")
RESUME_SECTION_TEMPLATE (constant template — "## Resumes" + "### Detecting a resume: resume.log")
BASE_PROMPT_POST (constant — "## Task")

compose_agents_md(consumer_instructions, *, workdir_exclude, supports_resume=True) ->
   BASE_PROMPT_PRE
   + (rendered RESUME_SECTION_TEMPLATE if supports_resume else "")
   + BASE_PROMPT_POST
   + consumer_instructions
```

**Updated signature:**

```python
def compose_agents_md(
    consumer_instructions: str,
    *,
    workdir_exclude: list[str] | None,    # MANDATORY (no default)
    supports_resume: bool = True,
) -> str:
```

`workdir_exclude` is mandatory: callers must pass it explicitly, even
if they want defaults (in which case they pass `None`). This forces a
conscious choice and prevents a silent desync between `archive.py`'s
defaults and `prompt.py`'s — a desync would mean the agent's prompt
describes one set of excludes while the snapshot machinery uses a
different set. `supports_resume` keeps a default of `True` because
current behavior is "all opencode tasks support resume" — existing
callers do not change.

**Caller in `session.py`:**

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

**Resume-section template.** The exclude list is rendered as a
comma-separated list of backticked patterns, inlined into the template:

```python
def _render_resume_section(workdir_exclude: list[str] | None) -> str:
    from optio_opencode.archive import DEFAULT_WORKDIR_EXCLUDES
    effective = workdir_exclude if workdir_exclude is not None else DEFAULT_WORKDIR_EXCLUDES
    if not effective:
        excludes_clause = "**No paths are excluded** — every file in the workdir is preserved."
        outside_clause = "If you need to stash large data, place it outside the workdir (e.g. `/tmp/`) — but remember it may be missing when you next look."
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

**`RESUME_SECTION_TEMPLATE`** (the markdown block that lands in the
prompt):

```markdown
## Resumes

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
```

## Session pipeline gating

Three gated changes inside `run_opencode_session`:

1. **Append `resume.log` entry — new step**, between fresh-vs-resume
   branch and binary install. Gated on `config.supports_resume`:

   ```python
   if config.supports_resume:
       await _append_resume_log_entry(host)
   ```

2. **`compose_agents_md` call** (fresh-start path only — on resume,
   AGENTS.md from the snapshot is reused, so we do not recompose):

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

3. **Snapshot capture in the finally block — gated on
   `supports_resume`:**

   ```python
   if config.supports_resume and session_id is not None:
       try:
           await _capture_snapshot(...)
       except Exception:
           _LOG.exception("snapshot capture failed; proceeding with workdir wipe")
   ```

   When opt-out: no GridFS bytes consumed, no DB row in the snapshots
   collection, no `mark_has_saved_state` call (which would no-op +
   warn anyway since `TaskInstance.supports_resume=False`). End-to-end
   consistent.

## Failure modes

- `_append_resume_log_entry` raises → before-execute hook has not run
  yet. Treated like any other pre-launch failure: session ends in
  failed state, after_execute still runs (per the existing finally
  semantics), cleanup runs. Snapshot capture is skipped (the existing
  `if session_id is not None` guard catches it because `session_id`
  is `None` at that point).
- Append succeeds but binary install fails → the `resume.log` entry
  is "premature" but harmless. The next launch (fresh) overwrites the
  workdir; the next launch (resumed) restores the snapshotted workdir
  which has the prior file. Either way, no inconsistency persists.
- Resume from a pre-feature snapshot → restored workdir has no
  `resume.log`. Append creates it with one timestamp. The resumed
  agent's prompt does not have the resume section (because the
  agent's context was baked from a pre-feature `AGENTS.md`), so the
  new file goes unread. Inert state, not a problem.
- Resume from a post-feature snapshot → restored workdir has the
  prior `resume.log`. Append adds a line. The agent (whose context
  has the resume instructions) sees the new line on next user message
  → detects resume.

## Testing

### `packages/optio-opencode/tests/test_prompt.py` (append)

- `test_compose_agents_md_includes_resume_section_by_default` — output
  contains `## Resumes` and `resume.log`.
- `test_compose_agents_md_omits_resume_section_when_supports_resume_false`
  — output contains neither `## Resumes` nor `resume.log`.
- `test_compose_agents_md_workdir_exclude_required` — calling without
  `workdir_exclude` raises `TypeError`.
- `test_compose_agents_md_renders_default_excludes_when_none` —
  `workdir_exclude=None` produces a prompt listing the actual
  `DEFAULT_WORKDIR_EXCLUDES` (`.git`, `node_modules`, `__pycache__`,
  `.venv`, `*.pyc`, `.DS_Store`).
- `test_compose_agents_md_renders_custom_excludes` —
  `workdir_exclude=["foo", "bar"]` produces a prompt listing `foo`
  and `bar` and NOT the defaults.
- `test_compose_agents_md_empty_excludes_renders_no_paths_excluded_copy`
  — `workdir_exclude=[]` produces the special "No paths are excluded"
  wording.
- `test_compose_agents_md_consumer_instructions_appended_after_base`
  — existing behavior preserved, the consumer's text still ends up at
  the bottom.

### `packages/optio-opencode/tests/test_types.py` (append)

- `test_opencode_task_config_supports_resume_default_true` — fresh
  `OpencodeTaskConfig(consumer_instructions="x")` has
  `supports_resume=True`.
- `test_opencode_task_config_supports_resume_can_be_disabled` —
  `OpencodeTaskConfig(..., supports_resume=False)` round-trips the
  value.

### `packages/optio-opencode/tests/test_sanity.py` (modify + add)

- The existing assertion `task.supports_resume is True` still passes
  because the default is `True`.
- New: `test_create_opencode_task_supports_resume_off` — passing
  `OpencodeTaskConfig(..., supports_resume=False)` produces a
  `TaskInstance` with `supports_resume=False`.

### `packages/optio-opencode/tests/test_session_local.py` (append)

- `test_session_local_writes_resume_log_with_launch_timestamp` —
  fresh-start session writes one ISO 8601 line to `resume.log` before
  opencode launches; verify by reading the file via the host's
  filesystem after `setup_workdir` + `_append_resume_log_entry` but
  before launch.
- `test_session_local_supports_resume_false_skips_resume_log` — with
  `supports_resume=False`, `resume.log` is never created.
- `test_session_local_supports_resume_false_skips_snapshot_capture` —
  with `supports_resume=False`, no entry is written to the snapshots
  collection in MongoDB after the session ends.

### `packages/optio-opencode/tests/test_session_resume.py` (augment)

- Existing happy-path test gets an additional assertion: after one
  resume cycle, `resume.log` in the restored workdir contains exactly
  two lines (original launch + one resume), both parseable as ISO
  8601 UTC timestamps, in increasing order.

### Out-of-scope

- No tests for the LLM actually following the prompt — opencode-side
  behavior, untestable from our side.
- No browser/iframe end-to-end tests.
- No `optio-core` test changes — none of optio-core changes here.

## Risks & open questions

- **Prompt budget.** The new section is ~260 words. That is not free
  but is a fair price for the safety value. If the prompt becomes too
  large for some opencode model in the future, we can revisit by
  trimming the rationale (keeping just imperatives) — costs ~100
  words.
- **Empty `workdir_exclude=[]` list.** Handled with a special-case
  wording. Not currently used by any consumer; if it ever becomes
  common, we can promote it to a more direct sentence.
- **Pre-feature-snapshot resumes.** Inert: agent's pre-baked prompt
  does not mention `resume.log`, so the new file goes unread. Pure
  forward shift.
- **Worker clock skew.** All timestamps are computed on the worker
  using `datetime.now(timezone.utc)`. Multiple workers (if Optio
  ever scales horizontally) might disagree by NTP drift, but the
  same process always uses the same worker, so within a single
  process's `resume.log` the timestamps are monotonic.
