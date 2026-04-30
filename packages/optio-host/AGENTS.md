# optio-host — Agent Cheatsheet

Generic host abstraction (local subprocess or remote SSH) + log/deliverables
coordination protocol used by optio task types (`optio-opencode`, future
recipe-execution).

Design: `docs/2026-04-30-optio-host-split-design.md` (in the optio repo).

## Layers

- **L0 — host primitives.** `optio_host.host` (Host Protocol +
  `LocalHost` + `RemoteHost`) and friends (`optio_host.context`,
  `optio_host.archive`, `optio_host.paths`, `optio_host.types`).
- **L1 — log/deliverables protocol.** `optio_host.protocol.parser` (pure
  parser) + `optio_host.protocol.session` (`run_log_protocol_session`
  driver).

L1 may import from L0; L0 must not import from L1.

## Status

Package created; population in progress per the migration phases in the
spec. Until phase 7 lands, expect mixed code paths (some symbols still
in `optio-opencode`, some moved here).
