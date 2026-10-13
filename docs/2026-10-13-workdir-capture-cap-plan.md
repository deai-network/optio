# Workdir capture cap: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a workdir is over `OPTIO_WORKDIR_CAPTURE_MAX_MB` at capture time, leave out the
paths the agent listed in `./regenerable.txt`, and tell agents to keep that list and to regenerate
missing entries on resume.

**Architecture:**
- **optio-host.** A new module `capture_cap.py` holds the setting, the list parser and the
  size-measurement decision. `archive_workdir` (LocalHost, RemoteHost and the Python fallback)
  applies anchored excludes when the decision says so, and reports a note through an optional
  callback.
- **optio-agents.** The shared resume section renders the instruction.
- **The engines.** The seven wrappers forward the note to the process log.

**Tech stack:** Python 3, asyncio, asyncssh (RemoteHost), GNU tar/du, pytest + pytest-asyncio.

**Spec:** `docs/2026-10-13-workdir-capture-cap-design.md`. Read it first.

## Global constraints

**Where the work happens**
- Host `excavator`, reached only through `ssh -F /home/csillag/share/fleet-ssh/config excavator '<cmd>'`.
- Worktree `~/deai/optio/.worktrees/csillag/workdir-capture-cap`, branch
  `csillag/workdir-capture-cap`. Below, `W` stands for that path.
- Never edit `~/deai/optio` itself: the shared dev engine runs from it, and a Python edit there
  restarts it.
- Never `pip install` and never create a venv.

**Running tests**
- Use the main venv, with the worktree's sources first on the path:
  `PYTHONPATH=$(ls -d $W/packages/*/src | tr '\n' ':') ~/deai/optio/.venv/bin/pytest <files> -rA -p no:cacheprovider --junitxml=/tmp/wcc-tests/<name>.xml > /tmp/wcc-tests/<name>.log 2>&1`,
  run from the package directory. Then read the log. Never rerun just to learn what failed.
- Run only the affected test files, never the full suite.
- Tests must not depend on wall-clock time. Inject the measurement result or the cap; never
  race a real timer.
- Tests that touch `OPTIO_WORKDIR_CAPTURE_MAX_MB` set or unset it with `monkeypatch`, so the
  host's environment never leaks in.

**Committing**
- Commits are signed automatically. Add no `Co-Authored-By` or other self-credit lines.
- `git add` explicit paths only.
- A commit that changes a package's public API (exports, signatures, behaviour others rely on)
  updates that package's `AGENTS.md`, and the root `AGENTS.md` where it covers the same thing.

**Spec values, verbatim**
- The setting is `OPTIO_WORKDIR_CAPTURE_MAX_MB`, default `100`; `0` means off. "MB" is MiB
  (1,048,576 bytes), compared against `du -sb` bytes. An invalid value raises a clear error.
- The list file is `./regenerable.txt` at the workdir root.
- The measurement is `timeout 1 du -sb` with the capture's own exclude patterns, on the
  workdir's host. A timeout or other failure counts as over the cap.
- Listed paths are excluded, never deleted. They are anchored at the workdir root, with no
  globs; a directory entry excludes everything under it.
- The agent is not told what was left out.

## Review focus

1. **A list entry naming something that also appears deeper.** `node_modules` listed must leave
   `./node_modules` out but keep `./pkg/node_modules`. (Task 2)
2. **A list entry with glob characters** (`build[1]`, `*.log`) is taken literally, never
   expanded. (Task 2)
3. **A workdir over the cap without a `regenerable.txt`:** a full capture and a warning note,
   never an error. (Task 3)
4. **An unreadable or binary `regenerable.txt`:** no crash. An unreadable file is an empty
   list; binary content is decoded with `errors="replace"`, and the resulting garbage lines
   are harmless literal excludes that match nothing. (Task 3)
5. **A cap of `0`:** no `du` runs, no file is read, and the archive is byte-for-byte today's
   command. (Task 3)

---

### Task 1: the setting and the list parser (`optio_host/capture_cap.py`)

**Files:**
- Create: `packages/optio-host/src/optio_host/capture_cap.py`
- Create: `packages/optio-host/tests/test_capture_cap.py`
- Modify: `packages/optio-host/src/optio_host/__init__.py`, if it re-exports public helpers.
  Follow its existing pattern.
- Docs: `packages/optio-host/AGENTS.md`

**Interfaces:**
- Produces:
  - `ENV_VAR = "OPTIO_WORKDIR_CAPTURE_MAX_MB"`, `DEFAULT_MAX_MB = 100` and
    `REGENERABLE_LIST = "regenerable.txt"`.
  - `workdir_capture_max_bytes() -> int`:
    - unset or empty gives `100 * 1048576`;
    - `"0"` gives `0` (off);
    - a non-negative integer string gives that many MiB in bytes;
    - anything else raises `ValueError("OPTIO_WORKDIR_CAPTURE_MAX_MB must be a non-negative integer (MiB), got '<value>'")`.
  - `parse_regenerable(text: str) -> list[str]`, which sanitises per the spec:
    - strips a ` #` note and surrounding whitespace;
    - skips blank lines and lines whose first non-space character is `#`;
    - normalises `./x/` to `x` (with `posixpath.normpath`, after stripping leading `./`);
    - skips absolute paths, any `..` segment, and `.`;
    - keeps the original order, without duplicates.

- [ ] **Step 1: Write the failing tests** in `test_capture_cap.py`:
  - `workdir_capture_max_bytes`:
    - unset gives `104857600`;
    - `"0"` gives `0`;
    - `"250"` gives `250*1048576`;
    - `"-1"`, `"1.5"` and `"lots"` raise `ValueError` matching `OPTIO_WORKDIR_CAPTURE_MAX_MB`.
  - `parse_regenerable`, on
    `"# deps\nnode_modules  # npm ci\n./frontend/node_modules/\n\n/abs\n../out\na/../b\n.\nbuild[1]\nnode_modules\n"`,
    gives `["node_modules", "frontend/node_modules", "build[1]"]`.
  - `parse_regenerable("")` gives `[]`.

- [ ] **Step 2: Run the tests and see them fail**, into `/tmp/wcc-tests/t1-red.log`. Expected: an
  `ImportError` for `optio_host.capture_cap`.

- [ ] **Step 3: Implement `capture_cap.py`** with the names above.

- [ ] **Step 4: Run the tests and see them pass**, into `/tmp/wcc-tests/t1-green.log`.

- [ ] **Step 5: Docs and commit.** Add a short "Workdir capture cap" subsection to optio-host's
  AGENTS.md covering the setting and the list file. Tasks 2 and 3 extend it. Commit with the
  message `feat(optio-host): capture cap setting and regenerable list parser`.

### Task 2: anchored literal excludes in both archivers

**Files:**
- Modify: `packages/optio-host/src/optio_host/host.py` (`_archive_command`)
- Modify: `packages/optio-host/src/optio_host/archive.py` (`_build_archive_bytes`,
  `yield_workdir_archive`)
- Test: `packages/optio-host/tests/test_archive.py` (the fallback) and
  `packages/optio-host/tests/test_local_archive_workdir.py` (the tar command)

**Interfaces:**
- Produces:
  - `_archive_command(workdir: str, patterns: list[str], compressor: str, anchored: list[str] = ()) -> str`.
    The existing `patterns` keep today's match-anywhere glob semantics. Each `anchored` entry
    excludes exactly `./<entry>`, and everything under it when it is a directory, taken literally
    (no globbing).
    - **GNU tar hint:** members are named `./…` because the command archives `.`. Put the
      existing `--exclude` options first, then `--anchored --no-wildcards`, then
      `--exclude=./<entry>` for each entry. Options are positional, so the existing patterns keep
      their meaning. Verify with the tests, not by reasoning.
  - `_build_archive_bytes(root, patterns, anchored=())` and
    `yield_workdir_archive(root, exclude=None, anchored=None)` apply the same anchored, literal
    semantics in Python: a relative path equal to an entry, or under it, is skipped.
  - With `anchored` empty, both produce exactly today's output (the same command string, the
    same members).

- [ ] **Step 1: Write the failing tests.** The workdir fixture holds `node_modules/a.js`,
  `pkg/node_modules/b.js`, `build[1]/x`, `buildx/y`, `keep.txt` and `.git/HEAD`, with
  `anchored=["node_modules", "build[1]"]`:
  - **The fallback** (`yield_workdir_archive` then untar): `node_modules/a.js` is absent;
    `pkg/node_modules/b.js` is present (Review focus 1); `build[1]/x` is absent; `buildx/y` is
    present (Review focus 2); `keep.txt` is present; `.git/HEAD` is absent (the existing default
    excludes still apply).
  - **The tar command:** run `_archive_command(...)` under `bash` against the same fixture,
    gunzip and list it, and assert the same membership.
  - `_archive_command(w, p, c)` equals `_archive_command(w, p, c, [])` and today's string. Pin it
    against the current output.

- [ ] **Step 2: Run the tests and see them fail**, into `/tmp/wcc-tests/t2-red.log`.

- [ ] **Step 3: Implement** the anchored parameter in both archivers.

- [ ] **Step 4: Run the tests and see them pass**, into `/tmp/wcc-tests/t2-green.log`, together
  with `test_archive.py`, `test_local_archive_workdir.py`, `test_remote_archive_workdir.py` and
  `test_archive_progress_format.py`.

- [ ] **Step 5: Commit** with the message `feat(optio-host): anchored literal excludes for workdir archives`.

### Task 3: the capture decision in `archive_workdir`

**Files:**
- Modify: `packages/optio-host/src/optio_host/capture_cap.py` (the decision)
- Modify: `packages/optio-host/src/optio_host/host.py`:
  - the `Host` Protocol's `archive_workdir`;
  - `LocalHost.archive_workdir`;
  - `RemoteHost.archive_workdir`.
- Modify: `packages/optio-host/src/optio_host/archive.py` (the fallback's measurement)
- Test: `packages/optio-host/tests/test_capture_cap.py`, `test_local_archive_workdir.py`,
  `test_remote_archive_workdir.py` (follow its existing fake-connection pattern) and
  `test_archive.py`
- Docs: `packages/optio-host/AGENTS.md`

**Interfaces:**
- Consumes: `workdir_capture_max_bytes` and `parse_regenerable` (Task 1); `anchored` (Task 2).
- Produces:
  - `archive_workdir(self, exclude: list[str] | None, *, on_note: Callable[[str], None] | None = None) -> AsyncIterator[bytes]`,
    on the Protocol and on both hosts. Existing callers are unchanged.
  - In `capture_cap.py`:
    - `du_command(workdir: str, patterns: list[str]) -> str` builds
      `cd <workdir> && timeout 1 du -sb <--exclude=…> .`, quoted with `shlex`.
    - `async def regenerable_excludes(run: Callable[[str], Awaitable[tuple[int, str]]], workdir: str, patterns: list[str], cap_bytes: int) -> tuple[list[str], str | None]`
      returns `(anchored_entries, note)`:
      - **Cap is `0`:** `([], None)` without calling `run` at all.
      - **Otherwise** it runs `du_command`. It is over the cap when the exit code is non-zero
        (which includes `timeout`'s 124) or the size parsed from stdout exceeds `cap_bytes`.
      - **Under the cap:** `([], None)`.
      - **Over the cap:** it runs `cat -- regenerable.txt` in the workdir. A non-zero exit means an empty list. The host's `run` decodes output with
        `errors="replace"`, so nothing here raises on binary content. With entries, it returns
        `(entries, "workdir <N> MB (cap <C> MB): left out <k> paths listed in regenerable.txt")`.
        When `du` failed, the note reads `workdir size unknown (du timed out after 1 s): left out <k> paths …`
        instead. With no list or an empty one, it returns `([], "workdir <N or unknown> MB is over the <C> MB capture cap, but regenerable.txt is missing or empty; capturing everything")`.
  - **The hosts:**
    - Each builds its own `run`: LocalHost through a local subprocess under `bash`; RemoteHost
      through `self._conn.run(cmd, check=False)`.
    - Each calls `regenerable_excludes` with `workdir_capture_max_bytes()` before starting the
      archive.
    - Each passes the entries as `anchored`.
    - When there is a note, each calls `on_note(note)` if given, and always logs it on the
      `optio_host.archive` logger (`logging.getLogger("optio_host.archive")`), at WARNING for the
      missing-list note and INFO otherwise.
  - **LocalHost's Python fallback** (no `tar` or `bash`) measures with a Python walk under a
    1-second `time.monotonic()` budget, honouring the same patterns; exhausting the budget
    counts as over the cap. It reads the list with Python. This budget is a product timeout,
    not a test timing.

- [ ] **Step 1: Write the failing tests.** Inject the cap with `monkeypatch.setenv`, and inject
  `run` for the decision.
  - **`regenerable_excludes`, with a fake `run`:**
    - cap 0: `run` is never called;
    - under the cap: `([], None)`;
    - over the cap with the list `"a\nb\n"`: `(["a","b"], note)`, where the note contains
      `left out 2 paths`;
    - `du` exit 124: the list is applied and the note contains `du timed out`;
    - over the cap with `cat` exit 1: `([], note)`, where the note contains
      `missing or empty` (Review focus 3);
    - over the cap with `cat` returning `"\ufffd\ufffd\n"` (what replacement-decoded binary
      looks like): no exception, and the entries are `["\ufffd\ufffd"]` (Review focus 4).
  - **`LocalHost.archive_workdir`, end to end:** use a temp workdir with a 2 MiB
    `big/blob.bin`, a `keep.txt`, and `regenerable.txt` containing `big`.
    - `OPTIO_WORKDIR_CAPTURE_MAX_MB=1`: `big/` is absent from the archive, `keep.txt` and
      `regenerable.txt` are present, and `on_note` received one note.
    - Cap `100`: everything is present and no note arrives.
    - Cap `0`: everything is present, no note arrives, and no `du` ran. Assert this by
      monkeypatching `regenerable_excludes` to fail if `run` is invoked (Review focus 5).
  - **`RemoteHost.archive_workdir`:** the same over-the-cap and under-the-cap cases through the
    test file's existing fake connection, asserting the commands it was asked to run (`du`, then
    `cat`) and the anchored `--exclude=./big` in the tar command.
  - **The fallback:** with a test-local patch that forces the no-tar path the way
    `test_archive.py` already does, if it does, the over-the-cap case leaves out `big/`.

- [ ] **Step 2: Run the tests and see them fail**, into `/tmp/wcc-tests/t3-red.log`.

- [ ] **Step 3: Implement** the items listed under Produces.

- [ ] **Step 4: Run the tests and see them pass**, into `/tmp/wcc-tests/t3-green.log`, together
  with every optio-host test file touching archives or hosts:
  `ls packages/optio-host/tests/test_*archive* packages/optio-host/tests/test_local_host*.py packages/optio-host/tests/test_remote*.py`.

- [ ] **Step 5: Docs and commit.** optio-host AGENTS.md documents:
  - `archive_workdir`'s cap behaviour;
  - the `on_note` parameter;
  - the logger name.
  
  Commit with the message `feat(optio-host): archive_workdir leaves out regenerable paths above the capture cap`.

### Task 4: the instruction (optio-agents)

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/prompt.py`:
  - `_RESUME_SECTION_TEMPLATE` (or a block appended to it);
  - `_resume_steps`;
  - `render_resume_section`;
  - `compose_instructions_file`.
- Test: `packages/optio-agents/tests/test_prompt.py` and `test_prompt_composer.py`
- Also run and update: every engine package's prompt tests that pin the resume section text
  (`ls packages/optio-*/tests/test_prompt*.py`).
- Docs: `packages/optio-agents/AGENTS.md`

**Interfaces:**
- Consumes: `workdir_capture_max_bytes` and `REGENERABLE_LIST` (Task 1), imported from
  optio-host. optio-agents already depends on it.
- Produces:
  - `render_resume_section(profile, workdir_exclude, check_resume_log_every_message=True, *, capture_max_bytes: int | None = None)`.
  - `compose_instructions_file(..., capture_max_bytes: int | None = None)`.
  - `None` means "read `workdir_capture_max_bytes()`", which is called once per composition,
    so a bad setting fails at agent launch.
  - `0` renders neither the paragraph nor the step.
  - A positive value renders the paragraph, with `<C>` as the cap in whole MiB, plus the
    resume step. Both texts are the spec's verbatim.

- [ ] **Step 1: Write the failing tests:**
  - with `supports_resume=True` and `capture_max_bytes=100*1048576`, the instructions contain
    `**Keep \`./regenerable.txt\` current.**`, `over 100 MB at snapshot time`, and
    `If paths listed in \`./regenerable.txt\` are missing, regenerate them before you continue.`;
  - with `capture_max_bytes=250*1048576`, they contain `over 250 MB`;
  - with `capture_max_bytes=0`, neither text appears;
  - with `supports_resume=False`, neither text appears;
  - with `capture_max_bytes=None` and the env var `"0"`, neither text appears;
  - with `capture_max_bytes=None` and the env var `"x"`, it raises `ValueError`.

- [ ] **Step 2: Run the tests and see them fail**, into `/tmp/wcc-tests/t4-red.log`.

- [ ] **Step 3: Implement.** Place the paragraph in the resume section after the existing
  "preserved / not preserved" text. Add the resume step to `_resume_steps`, conditionally,
  which means threading the flag into `_resume_steps`.

- [ ] **Step 4: Run the tests.** Run optio-agents' prompt tests and every engine's prompt test
  file, into `/tmp/wcc-tests/t4-green.log`. Where an engine test pins the full resume section
  and now fails only because of the new paragraph (the default cap is on), update its
  expectation, or set the env var to `0` in that test if it is about something else. Never
  weaken an assertion about anything else.

- [ ] **Step 5: Docs and commit.** optio-agents AGENTS.md documents the paragraph, the step, the
  condition and the new parameter. Commit with the message
  `feat(optio-agents): agents keep ./regenerable.txt; resume step regenerates missing entries`.

### Task 5: engines forward the note to the process log

**Files:**
- Modify the archive call sites:
  - `packages/optio-claudecode/src/optio_claudecode/session.py` (`_stream_archive`, around line 2066);
  - `optio-codex/.../session.py` (around 1017 and 1021);
  - `optio-opencode/.../session.py` (around 960);
  - `optio-cursor/.../session.py` (around 1170);
  - `optio-kimicode/.../snapshots.py` (around 274);
  - `optio-antigravity/.../session.py` (around 949 and 953);
  - `optio-grok/.../session.py` (around 1080).
- Test: `packages/optio-claudecode/tests/`, whichever file tests `_stream_archive` with a fake
  host.

**Interfaces:**
- Consumes: `archive_workdir(exclude, *, on_note=...)` (Task 3).
- Produces: each engine passes `on_note=` a function that writes the note as a line to the
  process log through the context's existing process-log call. Grep the engine for how it
  already writes process-log lines (not progress) and use the same call. If an engine has no
  context at that call site, report it rather than inventing plumbing.

- [ ] **Step 1: Write the failing test.** In claudecode's `_stream_archive` test, with a fake
  host whose `archive_workdir` calls `on_note("left out 2 paths …")`, the process log receives
  that line.
- [ ] **Step 2: Run it and see it fail**, into `/tmp/wcc-tests/t5-red.log`.
- [ ] **Step 3: Implement** at all seven call sites.
- [ ] **Step 4: Run the tests.** Run the claudecode test, plus each engine's existing
  snapshot/archive tests (`ls packages/optio-*/tests/ | grep -i "snapshot\|archive\|capture"`),
  into `/tmp/wcc-tests/t5-green.log`.
- [ ] **Step 5: Commit** with the message `feat(engines): workdir capture notes go to the process log`.

### Task 6: docs pass and verification

- [ ] **Step 1: Check the documentation against the spec:**
  - optio-host AGENTS.md, optio-agents AGENTS.md and the root AGENTS.md (the engine-wrappers
    sections) agree with the spec and each other;
  - the spec's one amendment is recorded in its own "Compatibility" section: the engines changed
    only to forward notes;
  - no plan task numbers appear in permanent docs.
  
  Commit any fixes with the message `docs: workdir capture cap reference`.
- [ ] **Step 2: Final verification.** Run every test file this branch touched or created
  (`git diff --name-only main..HEAD | grep tests/`), plus the archive and host test files from
  Task 3 and every engine prompt test, with full logs into `/tmp/wcc-tests/final.log` and
  `final.xml`. Expected: all pass.
