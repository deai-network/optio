# Oversized workdirs: leave out what the agent can regenerate

Status: design approved by the owner on 2026-10-13, in a brainstorm with "antd port" (aoe:fa9e60c76e02).

## Problem

optio suspends an agent process in reaction to outside events, such as a server shutdown. There is
no time to talk to the agent first:
1. optio kills the agent;
2. it captures the workdir (a gzip tar streamed by `Host.archive_workdir`) together with the
   engine's own state;
3. later it restores the workdir, resurrects the agent and tells it that it was resumed.

The capture runs against a deadline, the cancel grace. When the workdir is large, the capture
sometimes misses it. Raising optio's timeout does not help, because the real deadline is set from
outside (the shutdown). The cure is a smaller capture. Only the agent knows which parts of its
workdir are cheap to regenerate, and there is no time to ask it at capture time.

Today's static `workdir_exclude` list (default `.git`, `node_modules`, `__pycache__`, `.venv`,
`*.pyc`, `.DS_Store`) only covers what a task author knows in advance. It does not cover what the
agent creates along the way: a nested `frontend/node_modules`, a build directory, a downloaded
dataset.

## Decisions

1. **The agent keeps a list.** The list of regenerable paths lives at a predefined place in the
   workdir. The agent maintains it; optio only reads it.
2. **The list is used only above a size cap.** Small workdirs are captured whole, so a resume
   does not pay for needless reinstalls.
3. **The size is measured by `du`, time-boxed to 1 second.** A `du` that runs out of time counts
   as over the cap.
   - `du` reads metadata only, so its cost grows with the number of files, not their bytes.
   - On excavator a 64k-file tree took 0.29 s idle, and 0.67–1.1 s under 8 CPU hogs plus a
     parallel `tar | gzip`.
   - A timeout errs in the safe direction (a smaller capture). A tree `du` cannot walk in 1 s is
     almost always over 100 MB anyway.
4. **Listed paths are excluded from the tar, not deleted.**
   - It is faster: deleting a big tree can take as long as archiving it.
   - It is non-destructive: an aborted suspend or a failed capture leaves the live workdir intact.
   - It is safe: a bad line cannot delete anything.
   - Restore empties the workdir before unpacking, so the listed paths are missing afterwards
     either way.
5. **The agent is not told what was left out.** On resume it checks its own list and regenerates
   whatever is missing.
6. **The cap is a deployment setting:** `OPTIO_WORKDIR_CAPTURE_MAX_MB` in the environment
   (`.env`), default `100`. `0` turns the feature off entirely.

## The agent's side

### The file

`./regenerable.txt`, at the workdir root, so it is always captured itself.

- One path per line, relative to the workdir. A path names a file, or a directory with everything
  in it.
- Paths are anchored at the root: `node_modules` means `./node_modules` only, not every
  `node_modules` in the tree. No globs.
- Lines that start with `#`, after leading whitespace, are comments. Blank lines are ignored.
- Text after ` #` on a path line is a free note, e.g. `frontend/node_modules  # npm ci`, for the
  agent's own use after a resume.

### The instruction

The shared resume section of the instructions file (`render_resume_section` in
`optio_agents/prompt.py`) gains a paragraph. All seven engines build their instructions file
through `compose_instructions_file`, so this is the one place that reaches every agent. The section
already explains what is and isn't preserved.

The paragraph is rendered when both of these hold:
- the task supports resume (the section exists only then, which is exactly when captures happen);
- the cap is not `0`.

Wording, with the cap filled in:

> **Keep `./regenerable.txt` current.** List in it anything big in the workdir that you could
> regenerate quickly and completely: dependency installs, build outputs, caches, data you can
> download again. One path per line, relative to the workdir (exact paths, no wildcards); a `#`
> note after a path is fine (e.g. `frontend/node_modules  # npm ci`). When the workdir is over
> 100 MB at snapshot time, these paths are left out of the snapshot. Never list anything holding
> work you could not regenerate.

"(exact paths, no wildcards)" was added after the final review (2026-10-14), so the instruction
matches the file format's "No globs" above.

The resume steps (`_resume_steps`) gain one step, rendered under the same condition:

> - If paths listed in `./regenerable.txt` are missing, regenerate them before you continue.

## The capture side

### One choke point

Everything happens inside optio-host's `archive_workdir`, in both host implementations
(`LocalHost` and `RemoteHost`), including `LocalHost`'s pure-Python fallback (`archive.py`) for
hosts without `tar` or `bash`. All seven engine wrappers already capture through it. Its only
signature change is an optional keyword, `on_note`: a callback that receives the capture's
one-line note. Through it, each engine wrapper forwards the note to the process log, because the
host layer has no handle to it. That is the only engine change (an amendment made while
planning, 2026-10-13).

### Steps

1. **Cap is `0`:** capture exactly as today.
2. **Otherwise, measure the workdir size** on the host where the workdir lives, with
   `timeout 1 du -sb` plus the same exclude patterns the capture will apply. A big `.git` that is
   never captured therefore doesn't count toward the cap.
   - `RemoteHost` runs it over its existing SSH connection.
   - The Python fallback walks the tree with a 1-second budget.
3. **The workdir is over the cap, or the measurement timed out:** read `./regenerable.txt`
   (from the same host), parse and sanitise it, and add each entry to the capture as an exclusion
   anchored at the workdir root.
   - **The tar pipeline (`_archive_command`):** anchored and unanchored excludes must not change
     each other's meaning. The existing patterns keep today's match-anywhere behaviour; list
     entries match only `./<path>` and everything under it.
   - **The Python fallback:** the same semantics.
4. **Otherwise,** capture as today.

### Sanitising

Each entry is stripped of its note and of surrounding whitespace, and `./x/` is normalised to
`x`. Entries are skipped when they:
- are empty;
- are absolute;
- contain a `..` segment;
- normalise to the workdir itself (`.`).

Duplicates are harmless.

### Failure modes and logging

- **The list is missing, empty or unreadable while the workdir is over the cap:** the capture
  goes ahead as today, and the process log gets a warning.
- **The measurement fails** for any reason other than the timeout (e.g. `du` missing): treat it
  as over the cap. That is the safe direction, and the list is still optional.
- **Every capture that applied the list** logs one line to the process log, through `on_note`
  and the `optio_host.archive` logger, for example
  `workdir 214 MB (cap 100 MB): left out 3 paths listed in regenerable.txt` or
  `workdir size unknown (du timed out after 1 s): left out 3 paths …`.

### The setting

A single helper in optio-host reads `OPTIO_WORKDIR_CAPTURE_MAX_MB`. optio-agents already depends
on optio-host. "MB" here means MiB (1,048,576 bytes), compared against `du -sb` bytes. The helper:
- defaults to `100`, and treats `0` as off;
- raises a clear error on a value that is not a non-negative integer.

It is called when the instructions file is composed, at agent launch, so a bad value surfaces
before any capture depends on it. Both the capture and the instruction renderer use it, so the
number the agent is told always matches the cap that is applied.

### Restore

Unchanged: the workdir is emptied and the archive unpacked. The listed paths are simply absent
until the agent regenerates them.

## Compatibility and release

- **No breaking API change.** `archive_workdir` gains an optional `on_note` keyword, and
  `compose_instructions_file` and `render_resume_section` gain an optional `capture_max_bytes`.
  The seven engine wrappers pass `on_note`, forwarding the note to the process log.
- **Behaviour change by default.** With the default of 100 MB, a workdir over the cap is now
  captured without the paths its agent listed. Agents learn about the list from their
  instructions, so a workdir without a list behaves as today. `OPTIO_WORKDIR_CAPTURE_MAX_MB=0`
  restores today's behaviour exactly.
- **Release.** optio-host first, then optio-agents, following `docs/release-cookbook.md`, only on
  the owner's go.
  - optio-host is released as a **minor** (0.4.0), not a patch. optio-agents and every engine
    package now use what only 0.4 has (`optio_host.capture_cap`, and `archive_workdir`'s
    `on_note` with its level), so their `optio-host` requirement is raised from `>=0.3,<0.4` to
    `>=0.4,<0.5` in the same release wave. The release script rewrites those pins; by the
    cookbook's wave rule, every package whose pin moved is released too.
- **Excavator.** Its `.env.example` should document the variable; that is a separate excavator
  change.

## Documentation (same commits as the change)

- **`packages/optio-host/AGENTS.md`:** `archive_workdir`'s cap, the list, the anchored exclusion
  and the setting.
- **`packages/optio-agents/AGENTS.md`:** the new instruction paragraph and resume step, and when
  they render.
- **The root `AGENTS.md`:** kept consistent where it covers the same ground.

## Testing

TDD, affected test files only, no wall-clock dependence. The timeout path is tested by injecting
the measurement result, not by racing a real clock.

- **The setting helper:**
  - unset gives 100 MB;
  - `0` turns the feature off;
  - a valid number is used;
  - garbage raises.
- **Parsing and sanitising the list:**
  - comments, notes and blank lines;
  - `./x/` normalised to `x`;
  - absolute paths, `..` and `.` skipped;
  - a missing file gives an empty list.
- **`_archive_command`:**
  - a list entry `a/node_modules` excludes `./a/node_modules` and everything under it, but not
    `./b/a/node_modules`;
  - the existing unanchored patterns still match anywhere.
- **`LocalHost.archive_workdir`, end to end on a temporary workdir, with the cap or the measured
  size injected:**
  - over the cap with a list: the listed paths are absent from the archive and everything else is
    present;
  - under the cap: everything is present;
  - a measurement timeout: the list is applied;
  - over the cap without a list: a full capture and a warning;
  - cap `0`: no measurement at all.
- **The Python fallback:** the same over-the-cap and under-the-cap cases.
- **`RemoteHost.archive_workdir`:** the same cases, using the existing remote-host test setup if
  one exists. Otherwise its command construction is unit-tested, and the end-to-end cases are
  covered on `LocalHost`.
- **The instructions (optio-agents):** the paragraph and the resume step appear only when the
  task supports resume and the cap is not `0`. The rendered text names the configured cap.

## Implementation notes (2026-10-14)

Points the implementation had to settle that this design left open, without changing any
decision above:

- **The "size unknown" note names the real cause,** not just the timeout: `du` timed out, `du`
  failed with a non-zero exit, `du` printed no size, or (the Python fallback) the walk timed
  out. Each gives its own `workdir size unknown (<cause>)` note.
- **The whole capture decision fails safe, not only the measurement.** Anything it raises — a
  bad `OPTIO_WORKDIR_CAPTURE_MAX_MB`, the `du` run, the list read, a failed SSH channel or
  `bash` spawn — is caught, logged at WARNING on `optio_host.archive` and handed to `on_note`
  as `capture decision failed (<exception>); capturing everything`, and the capture leaves
  nothing out: losing a shutdown snapshot to the decision itself is worse than capturing an
  oversized one.
- **The list read is bounded:** `[ -f regenerable.txt ] && timeout 1 head -c 65536 --
  regenerable.txt` (the Python fallback opens the file non-blocking and reads the same 64 KiB,
  regular files only). A last line the limit may have cut short is dropped, so a cut-off
  `src/gen` can never exclude `./src`.
- **An entry containing a NUL byte is dropped,** alongside the absolute, `..` and `.` cases
  under "Sanitising" above — no real path can contain one, and exec would reject it anyway.
- **The excludes applied to one archive are capped at 64 KiB of `--exclude=` arguments**
  (Linux caps one `exec` argument at 128 KiB, and the archive runs as a single `bash -c`).
  Entries are applied in listed order while they still fit; the note names how many more were
  dropped, e.g. `(3 more ignored: list too long)`.
- **An invalid setting is handled differently depending on when it is read.** Read at agent
  launch (`compose_instructions_file` → `render_resume_section`), it raises the clear error
  under "The setting" above. Read again at capture time, inside the fail-safe decision, the
  same error instead falls back to a full capture, by the same reasoning as any other failed
  decision.
- **The engines forward the note the same way as every other milestone line,** at the level it
  comes with: `ctx.report_progress(None, note, level=level)` (see "The note carries a level"
  below).

Settled after the final review (2026-10-14):

- **The listed entries are literal tar excludes that BusyBox tar takes too.** Each entry is one
  `--exclude=./<entry>` with `\`, `*`, `?` and `[` backslash-escaped
  (`capture_cap.literal_exclude`). GNU tar and BusyBox tar both match it literally and only from
  the root: `./node_modules` keeps `./pkg/node_modules`, `build[1]` keeps `build1`. The first
  version switched GNU tar's mode with `--anchored --no-wildcards`, which BusyBox tar (Alpine)
  rejects, so the whole archive failed and the snapshot was lost. The escapes count toward the
  64 KiB exclude budget.
- **Only GNU tar and BusyBox tar get the list.** Before applying any entry, the host runs
  `tar --version 2>&1 | head -1`, once per `LocalHost` and once per `RemoteHost` connection.
  Any other tar (bsdtar strips a pattern's leading `./`, so the exclude would match anywhere)
  gets none, and the capture takes everything with the warning
  `regenerable.txt not applied: tar on this host is not GNU or BusyBox ('<version line>');
  capturing everything`.
- **The note carries a level.** `on_note` is `Callable[[str, str], None]`, called as
  `on_note(note, level)` with `level` `"info"` or `"warning"`, the levels of
  `ctx.report_progress`. A warning: the list is missing, unreadable or names no path, the tar is
  neither GNU nor BusyBox, or the decision failed. Info: paths were left out. The
  `optio_host.archive` logger logs the note at the same level.
- **A missing list is told from a failed read.** Exit 1 from the list read (the `[ -f ]` test)
  gives `regenerable.txt is missing or not a regular file`. Any other exit, e.g. 127 without
  `timeout`, gives `regenerable.txt could not be read (exit N)`. A list that is read but names
  no path gives `regenerable.txt lists no paths`. The Python fallback tells a missing or
  non-regular file from any other `OSError` the same way.
- **The list never leaves itself out.** An entry naming `regenerable.txt` is skipped, alongside
  the cases under "Sanitising" above, so the list is always captured.
- **The entries left out are logged** at DEBUG on `optio_host.archive` whenever the list is
  applied.
