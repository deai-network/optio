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
