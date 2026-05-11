# optio-host — Agent Cheatsheet

Generic host abstraction (local subprocess or remote SSH) + log/deliverables
coordination protocol used by optio task types (`optio-opencode`, future
recipe-execution).

Design: `docs/2026-04-30-optio-host-split-design.md` (in the optio repo).

## Layers

- **L0 — host primitives.** `optio_host.host` (Host Protocol +
  `LocalHost` + `RemoteHost`) and friends (`optio_host.context`,
  `optio_host.archive`, `optio_host.paths`, `optio_host.types`).
- **L0 — download task factory.** `optio_host.download` exposes
  `create_download_task` (URL → file via curl) and the `DownloadFailed`
  exception. Drives `curl --trace-ascii -` via either
  `Host.launch_subprocess` (when a host is supplied) or
  `asyncio.create_subprocess_exec` (when not), parses byte-progress on
  stdout, ring-buffers stderr for failure reporting, supports cooperative
  cancel.
- **L1 — log/deliverables protocol.** `optio_host.protocol.parser` (pure
  parser) + `optio_host.protocol.session` (`run_log_protocol_session`
  driver).

L1 may import from L0; L0 must not import from L1.

## HookContext methods (consumer surface)

The `HookContext` wraps a `ProcessContext` plus a `Host` and exposes these
methods to task bodies. Unspecified attributes fall through to the wrapped
`ProcessContext` (so `hook_ctx.report_progress(...)`, `hook_ctx.params`,
etc. work directly).

- `await hook_ctx.run_on_host(command, *, check=True, capture_stderr=False, cwd=None)`
- `await hook_ctx.copy_file(source, target, *, skip_if_unchanged=False)`
- `await hook_ctx.read_from_host(path, *, silent=False)`
- `await hook_ctx.read_text_from_host(path, *, silent=False)`
- `await hook_ctx.download_file(url, target, *, description=None, cleanup_on_fail=True)`
  — Spawns a child task that downloads `url` to `target` on the same host
  the parent runs on. Reports one initial `"Downloading <basename>"`
  message followed by numeric percent updates. Target accepts the same
  forms as `copy_file` (absolute, `~`/`~/…`, or workdir-relative).
  Workdir-escape raises `ValueError` without spawning. Child failure
  surfaces as `RuntimeError` at the `run_child` boundary; the underlying
  `DownloadFailed` lives in the child's `status.error`. Parent cancel
  auto-propagates to the in-flight download.

## Status

Package created; population in progress per the migration phases in the
spec. Until phase 7 lands, expect mixed code paths (some symbols still
in `optio-opencode`, some moved here).
