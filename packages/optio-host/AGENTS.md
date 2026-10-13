# optio-host — Agent Cheatsheet

Generic host abstraction (local subprocess or remote SSH) used by optio task
types (`optio-opencode`, future recipe-execution). The agent-coordination
protocol and `HookContext` that used to live here now live in `optio-agents`.

Design: `docs/2026-04-30-optio-host-split-design.md` (in the optio repo).

## Layers

- **L0 — host primitives.** `optio_host.host` (Host Protocol +
  `LocalHost` + `RemoteHost`) and friends (`optio_host.archive`,
  `optio_host.paths`, `optio_host.types`).
- **L0 — download task factory.** `optio_host.download` exposes
  `create_download_task` (URL → file via curl) and the `DownloadFailed`
  exception. Drives `curl --trace-ascii -` via either
  `Host.launch_subprocess` (when a host is supplied) or
  `asyncio.create_subprocess_exec` (when not), parses byte-progress on
  stdout, ring-buffers stderr for failure reporting, supports cooperative
  cancel.

For the log/deliverables coordination protocol, the keyword parser, and
**HookContext**, see **[optio-agents](../optio-agents)**.

## Workdir capture cap

`optio_host.capture_cap` holds the setting, the list parser and the
suspend-capture decision (design:
`docs/2026-10-13-workdir-capture-cap-design.md`).

- `workdir_capture_max_bytes()` reads `OPTIO_WORKDIR_CAPTURE_MAX_MB` — "MB"
  here is MiB (1,048,576 bytes), compared against `du -sb` bytes. Unset or
  empty gives the default, `DEFAULT_MAX_MB` = 100 MiB; `"0"` turns the cap
  off; any other non-negative integer is used as given; anything else
  raises `ValueError`.
- `parse_regenerable(text)` sanitises the contents of `REGENERABLE_LIST`
  (`regenerable.txt`, kept by the agent at the workdir root): it drops
  comments, blank lines and ` #` notes, normalises `./x/` to `x`, and
  skips absolute paths, any `..` segment, `.`, and anything containing a
  NUL byte, keeping the original order without duplicates.

### In `archive_workdir`

`archive_workdir(exclude, *, on_note=None)` (the `Host` Protocol, `LocalHost`
and `RemoteHost`; `on_note: Callable[[str, str], None] | None`) applies the
cap before it starts the archive, on the host where the workdir lives:

- **Cap `0`:** nothing is measured or read, and the archive command is the
  same as without the cap.
- **Otherwise** `regenerable_excludes(run, workdir, patterns, cap_bytes)`
  runs `du_command(workdir, patterns)`, i.e.
  `cd <workdir> && timeout 1 du -sb --exclude=… .`, with the capture's own
  exclude patterns, so what is never captured does not count. A non-zero
  exit (`timeout`'s 124 included) or a size above the cap counts as over.
- **Over the cap:** it reads `regenerable.txt` with `list_command(workdir)`
  and parses it with `parse_regenerable`. The archive leaves the entries out
  as anchored, literal excludes: `./<entry>` and everything under it.
  - Each entry is one `literal_exclude(entry)` option: `--exclude=./<entry>`
    with `\`, `*`, `?` and `[` backslash-escaped. GNU tar and BusyBox tar both
    read it literally and only from the root (`./node_modules` keeps
    `./pkg/node_modules`; `build[1]` keeps `build1`). There is no
    `--anchored`/`--no-wildcards`: BusyBox tar rejects them, which failed the
    whole archive.
  - **The tar flavour.** Before applying any entry, the host runs
    `TAR_VERSION_COMMAND` (`tar --version 2>&1 | head -1`), once per
    `LocalHost` and once per `RemoteHost` connection. Only GNU tar and BusyBox
    tar (`tar_takes_literal_excludes`) take the list. Any other tar (bsdtar
    strips a pattern's leading `./`, so the exclude would match anywhere)
    gets none, and the note becomes `unknown_tar_note`:
    `regenerable.txt not applied: tar on this host is not GNU or BusyBox
    ('<version line>'); capturing everything`.
- **The read is bounded:**
  `[ -f regenerable.txt ] && timeout 1 head -c 65536 -- regenerable.txt`.
  - Only a regular file (or a symlink to one) is read, so a FIFO cannot hang
    the capture and `/dev/zero` is not read. Anything else, or a failure,
    means an empty list.
  - At most `LIST_MAX_BYTES` (64 KiB) is read. A last line that this limit
    cuts short is dropped, so a cut-off `src/gen` never excludes `./src`.
- **The entries are limited.** Each applied entry becomes a
  `literal_exclude` option in the single `bash -c` argument that runs the
  archive, and Linux caps one exec argument at 128 KiB (E2BIG). Entries are
  applied in order while those arguments fit in `EXCLUDES_MAX_BYTES` (64 KiB,
  counting the escapes and the outer quoting); the rest are ignored, and the
  note says `(N more ignored: list too long)`.
- **The hosts' `run`:** `RemoteHost` runs these commands over its SSH
  connection, and `LocalHost` under local `bash`. Both decode output with
  `errors="replace"`, so a binary list cannot raise.
- **LocalHost's fallback** (no `tar` or `bash`) decides with
  `archive.fallback_regenerable_excludes` instead. It measures with a Python
  walk (`measure_workdir`) honouring the same patterns, under a 1 s
  `time.monotonic()` budget (`WALK_BUDGET_S`; running out counts as over).
  It reads the list with Python, with the same bounds: opened non-blocking,
  only a regular file, at most 64 KiB.
- **The timeouts are read at call time:** `DU_TIMEOUT_S` (the `du` and the
  list read) and `WALK_BUDGET_S`, so tests patch them rather than race them.
- **It fails safe.** Anything the decision raises becomes the note
  `capture decision failed (<exception>); capturing everything`, a warning,
  logged on `optio_host.archive` and handed to `on_note` like every other
  note, and the capture goes ahead leaving nothing out. That covers the cap
  setting, the measurement, the list read, the tar probe, and an SSH channel
  or a `bash` spawn that fails. Losing a shutdown snapshot is worse than capturing an
  oversized one. An `on_note` that raises is logged too, and the capture
  goes on.

**The note.** A capture that finds the workdir over the cap produces a
one-line note with a level, a `capture_cap.CaptureNote(text, level)`. Under
the cap there is none.

- The level is `"info"` or `"warning"` (`capture_cap.INFO`, `WARNING`), the
  levels of `ctx.report_progress`:
  - `"info"` when paths were left out, e.g.
    `workdir 214 MB (cap 100 MB): left out 3 paths listed in regenerable.txt`,
    or `workdir size unknown (du timed out after 1 s): left out 3 paths …`;
  - `"warning"` when everything is captured because the cap could not be
    applied: the list is missing or empty (e.g. `workdir 214 MB is over the
    100 MB capture cap, but regenerable.txt is missing or empty; capturing
    everything`), the host's tar is not GNU or BusyBox, or the decision
    failed.
- It goes to `on_note(text, level)`, when given, so the caller (an engine
  wrapper) can forward it to the process log, which the host layer has no
  handle to. Every engine wrapper calls
  `ctx.report_progress(None, text, level=level)`, so a warning reaches the
  process log as a warning.
- It is always logged on the `optio_host.archive` logger, at INFO or
  WARNING to match.

Sizes in notes are whole MiB, rounded down.
