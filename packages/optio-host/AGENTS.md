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

`optio_host.capture_cap` holds the setting and the list parser that the
suspend-capture decision builds on (design:
`docs/2026-10-13-workdir-capture-cap-design.md`).

- `workdir_capture_max_bytes()` reads `OPTIO_WORKDIR_CAPTURE_MAX_MB` — "MB"
  here is MiB (1,048,576 bytes), compared against `du -sb` bytes. Unset or
  empty gives the default, `DEFAULT_MAX_MB` = 100 MiB; `"0"` turns the cap
  off; any other non-negative integer is used as given; anything else
  raises `ValueError`.
- `parse_regenerable(text)` sanitises the contents of `REGENERABLE_LIST`
  (`regenerable.txt`, kept by the agent at the workdir root): it drops
  comments, blank lines and ` #` notes, normalises `./x/` to `x`, and
  skips absolute paths, any `..` segment, and `.`, keeping the original
  order without duplicates.

Applying the cap to an actual capture — measuring the workdir with `du`,
reading `regenerable.txt` off the (possibly remote) host, and excluding the
listed paths — is a later change.
