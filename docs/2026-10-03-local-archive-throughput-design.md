# Local snapshot archive throughput: stream the workdir through tar | pigz -1

Date: 2026-10-03. Status: requested by the owner after a production loss;
implemented on branch `csillag/local-archive-pigz`.

## Problem

On bobcat (excavator production) a claudecode analysis with no "Access from"
host ran on the engine's `LocalHost`. When it was stopped, its snapshot
capture spent the whole 30 s cancel grace in "Snapshot: archiving workdir...",
optio force-failed it ("Task did not unwind within cancellation grace
period"), and no snapshot record was written. About 12 hours of work survived
only because the workdir happened to still be in the engine container; it was
restored by hand following `docs/2026-09-13-manual-snapshot-restore.md`.

## Root cause

`docs/2026-09-13-snapshot-archive-throughput-design.md` fixed `RemoteHost`
(256 KiB block reads, `tar | pigz -1` with a `gzip -1` fallback) and listed
`LocalHost` as out of scope. `LocalHost.archive_workdir` still uses
`optio_host.archive.yield_workdir_archive`: Python `tarfile` in `w:gz` mode,
whose default is zlib level 9, single-threaded, and building the whole
archive in memory before yielding the first byte.

Measured on the bobcat workdir (743 MiB tar, 22 k files, mostly
agent-installed tools), 12 cores:

| compressor | time | output |
|---|---|---|
| zlib level 9, one thread (current `LocalHost`) | 62 s | 264 MiB |
| `gzip -1` | 9 s | 292 MiB |
| `pigz -1` | 1 s | 291 MiB |

Compression alone needs twice the grace; GridFS upload comes on top.

## Change

1. `LocalHost.archive_workdir` runs the same `_archive_command`
   (`bash -c 'set -o pipefail; cd <workdir> && tar cf - <excludes> . | <compressor>'`)
   that `RemoteHost` runs over SSH, as a local subprocess:
   - compressor `pigz -1` when `pigz` is on `PATH`, else `gzip -1`;
   - stdout read in `_ARCHIVE_READ_BLOCK` blocks and yielded as it arrives
     (no whole-archive buffer);
   - stderr drained alongside, its tail carried in the error;
   - non-zero exit raises `RuntimeError("local workdir archive failed (exit N): <stderr tail>")`;
   - the pipeline runs in its own process group (`start_new_session=True`);
     if the consumer abandons the stream or the capture is cancelled, the
     whole group is SIGKILLed, so `tar` and the compressor do not outlive the
     capture.
2. When `tar` or `bash` is not on `PATH` (stripped images, e.g. Alpine
   without bash), `LocalHost` keeps the in-process `tarfile` path, now at
   compression level 1 instead of 9.

Both paths emit gzip, and `LocalHost.restore_workdir` (Python `tarfile`,
`r:gz`) reads either, as `RemoteHost.restore_workdir` (`tar xzf -`) already
does. Snapshots taken before the change restore unchanged.

## Behaviour differences, accepted

- Member names gain a `./` prefix, as remote archives already have.
- A symlink to a directory is stored as a symlink. `os.walk` skipped those
  entirely. Remote archives have always stored them, and the agents re-link
  the ones they own (`ln -sfn`, e.g. claudecode's `home/.local/share/claude/versions`).
- Directories are stored as entries, so empty directories survive a round trip.
- Exclude patterns are matched by `tar --exclude` instead of
  `optio_host.archive._excluded`; this is the matching `RemoteHost` uses.

## Out of scope

- `LocalHost.restore_workdir` still buffers the archive in memory; it runs at
  launch, not under the cancel deadline.
- Installing `pigz` in consumers' images (excavator's engine image has none;
  without it the local path gets `gzip -1`, ~9 s on the workdir above).
