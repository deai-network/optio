# Plan: local snapshot archive through tar | pigz -1

Design: `docs/2026-10-03-local-archive-throughput-design.md`.
Branch `csillag/local-archive-pigz`. Package: `optio-host` only.

## Task 1 -- tests first (`packages/optio-host/tests/test_local_archive_workdir.py`)

Real local subprocesses, no fakes except `shutil.which` for the tool probes:

- round trip through `LocalHost.archive_workdir` -> `LocalHost.restore_workdir`,
  with default excludes, an explicit list (verbatim, no merge with defaults),
  and an empty list;
- shell metacharacters in the workdir path and in excludes stay literal;
- symlinks (to a file and to a directory) are stored as symlinks, not followed;
- the output is gzip at the fastest level (gzip header XFL byte = 4) on every
  path: `pigz -1`, `gzip -1`, and the no-`tar`/no-`bash` `tarfile` fallback;
- `pigz -1` is used when `pigz` is on `PATH` (fake `pigz` that records its
  argv and execs `gzip`), `gzip -1` otherwise;
- the stream is yielded in blocks no larger than `_ARCHIVE_READ_BLOCK`;
- a failing `tar` raises `RuntimeError` carrying the stderr tail;
- an abandoned stream (`aclose()` after the first chunk) and a cancelled
  consumer both leave no process of the pipeline's group alive (polled with a
  60 s hang ceiling, no sleep-then-assert);
- the START trace names the workdir and the compressor.

Run them, see them fail for the right reason.

## Task 2 -- implementation (`packages/optio-host/src/optio_host/host.py`, `archive.py`)

- `LocalHost.archive_workdir`: `_archive_command` under `/bin/sh -c`,
  `start_new_session=True`, block reads, stderr tail via `_read_tail`,
  `RuntimeError` on non-zero exit, `killpg(SIGKILL)` + reap when abandoned;
  traces START / DONE like `RemoteHost`.
- Fallback to `yield_workdir_archive` when `tar` or `bash` is missing.
- `archive._build_archive_bytes`: `compresslevel=1`; docstring updated.

## Task 3 -- verification

- `optio-host` suite, then the new file 15x under CPU load (excavator has
  5.5 GB RAM: cap xdist workers at 2).
- Dependent packages' suites that exercise `LocalHost` snapshots
  (optio-claudecode, optio-opencode, optio-codex, optio-cursor, optio-grok,
  optio-kimicode, optio-antigravity), compared against `main` for
  pre-existing failures.

## Task 4 -- docs, commit, push, release

- Root `AGENTS.md` host section: one line on how each host compresses.
- Commit on the branch, fast-forward `main`, push `origin main`,
  fast-forward the shared `~/deai/optio` checkout.
- Release every package changed since its last release, per
  `docs/release-cookbook.md`.
