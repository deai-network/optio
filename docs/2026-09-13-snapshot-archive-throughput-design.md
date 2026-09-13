# Snapshot archive throughput: stream the workdir in blocks, compress with pigz

Date: 2026-09-13. Status: design agreed with the owner; implemented on branch
`archive-throughput` (items 1-5), plus review follow-ups the owner accepted:
remote stderr tail in the error, the remote pipeline closed when a capture is
abandoned, and a missing exit status treated as failure.

## Problem

Stopping or restarting a claudecode task on a remote ("Access from") host
captures a snapshot within the cancel grace (`EXCAVATOR_CANCEL_GRACE_SECONDS`,
180 s). For excavator's plakomm analysis (process `6aa42dea1b791783f5a01e83`,
workdir 738 MB, ~13.9 k files, mostly agent-installed tool venvs) the capture
never finishes: optio force-fails the task at the deadline, no snapshot record is
written, and the next resume would restore an older state.

## Evidence

Instrumented capture (owner cancel, 2026-09-13 06:58:35 local; logs in
`excavator:~/excavator-forensics/cancel-capture-20260913T065833.*`, via
`OPTIO_CANCEL_TRACE=1` and the engine log file):

- every unwind step before the archive took < 1 s in total (after_execute hook
  0.1 s, agent stop 0.1 s, session blob 1.9 MB 0.4 s, `rm -rf` steps 0.0 s);
- `archive_workdir+store` ran 179.5 s at a steady **~1.1 MiB/s** and had streamed
  ~198 MB of ~297 MiB when the deadline force-cancelled it.

## Root cause

`RemoteHost.archive_workdir` (`optio-host/src/optio_host/host.py`, the
`async for chunk in proc.stdout` loop) reads the remote `tar czf -` output with
asyncssh's async iterator. In asyncssh 2.24 `SSHStreamSession.aiter` is a
`readline()` loop, so a gzip stream arrives as newline-delimited pieces
averaging **307 bytes**. `_capture_snapshot` then awaits one GridFS write per
piece (Motor, ~0.2 ms each), capping the capture at ~1.1–1.4 MiB/s.

## Measurements (plaplay → excavator, engine key, asyncssh 2.24.0)

| Probe | Result |
|---|---|
| iperf3 excavator→plaplay / plaplay→excavator | 6.81 / 6.22 Gbit/s |
| OpenSSH `head -c 256M /dev/zero` plaplay→excavator | 87 MiB/s |
| Stream only, data discarded: `async for` vs `read(1 MiB)` | 5.18 vs 5.15 MiB/s (tar-bound); 441,670 chunks avg 307 B vs 1,304 chunks |
| GridFS write (Motor upload stream), 307 B / 64 KiB / 1 MiB chunks | 1.36 / 124.9 / 19.3 MiB/s |

Compression of the real workdir on plaplay (8 CPUs, cache warm, output
discarded; zstd times varied between two runs, ranking stable):

| Method | Time | Output |
|---|---|---|
| gzip -6 (today, via `tar czf`) | 41.0 s | 296.7 MiB |
| gzip -1 | 20.3 s | 321.1 MiB |
| pigz -6 (8 threads) | 11.8 s | 296.3 MiB |
| **pigz -1 (8 threads)** | **7.0 s** | 320.1 MiB |
| zstd -3 -T0 | 5.8–10.0 s | 284.2 MiB |
| zstd -1 -T0 | 2.1–5.8 s | 310.6 MiB |
| lz4 -1 / --fast=1 | 1.8 / 1.6 s | 388.3 / 398.8 MiB |

End-to-end `tar | pigz -1` → asyncssh `read(block)` → Motor GridFS (320.1 MiB):

| Block | Time | Throughput | Inside GridFS writes |
|---|---|---|---|
| discard, 256 KiB | 7.8 s | 40.9 MiB/s | – |
| 16 KiB | 17.7 s | 18.1 MiB/s | 16.3 s |
| 64 KiB | 10.9 s | 29.4 MiB/s | 9.5 s |
| **256 KiB** | **10.5 s** | 30.6 MiB/s | 8.1 s |
| 1 MiB | 9.7 s | 32.9 MiB/s | 7.3 s |
| 4 MiB | 13.8 s | 23.3 MiB/s | 10.8 s |

asyncssh returns at most ~200 KiB per `read()` however large the request, so
blocks ≥ 256 KiB are equivalent (differences are noise).

## Decision (owner, 2026-09-13)

pigz -1 for now (output is gzip, so restore and all existing snapshots are
unchanged); zstd may come later, once the rest of the pipeline is understood.

## Design

1. **Block reads** in `RemoteHost.archive_workdir`: replace `async for chunk in
   proc.stdout` with a `await proc.stdout.read(256 * 1024)` loop until EOF. This
   is the actual bug fix and applies whichever compressor is used.
2. **Compressor** in the same method: run the archive as
   `bash -c 'set -o pipefail; cd <workdir> && tar cf - <excludes> . | pigz -1'`
   when `pigz` exists on the host, else `... | gzip -1`. Probe availability once
   per connection (`command -v pigz`). `pipefail` keeps a tar failure visible
   through the pipe (the existing exit-status check stays); invoke `bash`
   explicitly because the remote login shell may be dash.
3. **Restore unchanged**: both variants produce gzip, so
   `RemoteHost.restore_workdir` (`tar xzf -`) and all stored snapshots keep
   working. No format detection needed until a non-gzip format is adopted.
4. **Tests** (optio-host): command construction with and without pigz, excludes
   quoting, `pipefail` present; the read loop consumes large blocks from a fake
   stdout and stops at EOF; a failing pipeline raises.
5. **Host prerequisite**: pigz on agent hosts (plaplay has it; the fallback
   covers hosts without it). Add it to the host-prerequisites list of the
   bring-up guide.
6. **Expected result**: ~10 s for this workdir (vs ~280 s today; grace 180 s),
   leaving headroom for workdirs ~15× larger.

## Deploy and verify

Branch + worktree (never edit `~/deai/optio` directly: the engine runs from it
and watchfiles restarts it on source changes), review, then fast-forward the
live checkout while no process is running. Verify: resume analysis
`6aa42dea…`, cancel it, and expect the process-page milestones to end with
`Snapshot saved` within ~15 s; the engine log (`~/.excavator/logs/engine.log`)
shows the `archive_workdir` progress and DONE lines.

## Later / out of scope

- Overlap SSH reads and GridFS writes (queue): ~10 s → ~8 s.
- zstd with magic-byte detection on restore (gzip `1f 8b`, zstd `28 b5 2f fd`).
- Exclude regenerable caches (e.g. `home/.cache/pip`, 98 MB) from snapshots.
- `LocalHost` archives with Python `tarfile` gzip and builds the whole archive
  in memory (`optio_host/archive.py`); unchanged here.
- A failed stop leaks the remote `tar`/`gzip`, `tail -F optio.log` and the
  SFTP session, and the engine keeps that SSH connection open.
