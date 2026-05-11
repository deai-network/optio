# optio-host download task — design

Date: 2026-05-12
Status: spec (not yet implemented)
Scope: `packages/optio-host`

## Goal

Provide a reusable way for an optio task body to download a URL to a file on
the same host the task is running on. The download runs as a sub-task (child
process) under the caller, so it has its own process record, its own progress
stream, and its own cooperative cancel.

The integration point on the task author's side is a single new method on
`HookContext`:

```python
await hook_ctx.download_file(url, target)
```

`copy_file` (uploading bytes to the host) and `download_file` (downloading
from a URL to the host) sit next to each other as symmetric host primitives.

## Non-goals

- **Resume** of a partially-downloaded file across optio restarts or
  parent-task resume. Each invocation re-downloads from byte 0. The parent's
  resume contract already re-runs its execute body, which will re-call
  `download_file` if it wants to.
- **Range / partial downloads**, custom headers, auth tokens, custom timeout,
  TLS bypass. v1 ships with `url` + `target` only. If a real use case appears,
  parameters are added then.
- **Generic shell-task factory**. This is specifically a curl-driven download.
- **Optio-demo end-to-end exercise** — deferred; the user has a specific
  consumer in mind.

## Component boundary

The factory and its execute function live in `optio-host`, not `optio-core`.
Reasoning (recorded after a round of brainstorming):

- The execute function must run curl on a `Host` (LocalHost or RemoteHost) so
  the downloaded bytes land on the same machine as the caller's workdir. The
  `Host` Protocol is owned by optio-host.
- optio-core must not import optio-host. Putting the factory in optio-core
  would force a runner abstraction (a `CommandRunner` Protocol in optio-core
  implemented by optio-host) that exists solely to bridge the layer, with no
  second consumer in sight.
- The existing precedent for "task factory living next to the code that
  drives the work" is `create_opencode_task` in `optio-opencode`.

`optio-core` does **not** change.

## Public surface

### `optio_host.download` (new module)

```python
def create_download_task(
    process_id: str,
    name: str,
    *,
    url: str,
    target: str,
    host: Host | None = None,
    description: str | None = None,
    cleanup_on_fail: bool = True,
) -> TaskInstance: ...
```

- `process_id`, `name` — identity of the resulting child process record.
- `url` — http/https URL to download.
- `target` — path the response body is written to.
  - If `host` is not None: an absolute path on the host, or a path resolvable
    by the same rules as `HookContext.copy_file` (workdir-relative, `~`
    home-relative, or absolute). Resolution happens at the
    `HookContext.download_file` integration boundary, **not** inside the
    factory — the factory receives an already-resolved absolute target. (See
    "Integration" below.) Direct callers of `create_download_task` must pass
    an absolute path themselves.
  - If `host` is None: an absolute path or a path relative to the optio
    runtime's cwd. No `~` expansion (caller responsibility).
- `host` — when provided, curl runs on this host via `host.launch_subprocess`.
  When None, curl runs as a local subprocess via `asyncio.create_subprocess_exec`.
  Two code paths inside execute, no fake taskdir.
- `description` — passed through to TaskInstance for the UI.
- `cleanup_on_fail` — when True (default), if the download exits with a
  non-zero curl exit code OR is cancelled, the target file is best-effort
  removed (errors swallowed). Set to False if the caller wants to inspect
  partial bytes for debugging.

Returned `TaskInstance` fields:

- `execute` — the closure described below.
- `process_id`, `name`, `description` — as passed.
- `cancellable=True`.
- `supports_resume=False`.
- `auto_cancel_children=True` (the default; download has no grandchildren but
  the default is harmless).
- `ui_widget=None`.

### `optio_host.download.DownloadFailed` (new exception)

```python
class DownloadFailed(Exception):
    url: str
    target: str
    exit_code: int
    stderr_tail: str  # last ~1 KB of curl stderr
```

`__str__` includes url, exit_code, and the first 200 chars of stderr_tail.

### `optio_host.context.HookContext.download_file` (new method)

```python
async def download_file(
    self,
    url: str,
    target: str,
    *,
    description: str | None = None,
    cleanup_on_fail: bool = True,
) -> None: ...
```

- Resolves `target` to an absolute host path via the existing
  `_resolve_target_path(target, host.workdir, host_home)` — same rules
  `copy_file` uses.
- Generates the child's `process_id` as `f"{parent.process_id}.download-{n}"`
  where `n` is the value of the parent ctx's `_child_counter['next']` at the
  moment of the call (peek, not increment; `run_child` will increment for
  ordering). Per-parent monotonic; collisions across resume are not a concern
  because `clear_result_fields` deletes descendants on relaunch.
- Generates the child's `name` as `f"download {basename}"` where `basename =
  os.path.basename(resolved_target)`.
- Calls `create_download_task(process_id=..., name=..., url=url,
  target=resolved_target_abs, host=self._host, description=description,
  cleanup_on_fail=cleanup_on_fail)`.
- Calls `await self._ctx.run_child(task.execute, task.process_id, task.name,
  description=task.description)`.
- Returns None on success. On child failure, `run_child` raises
  `RuntimeError("Child process '<name>' failed")` — the original
  `DownloadFailed` type is lost across the boundary (see "Known limitation"
  below). On parent cancel, the child completes as "cancelled" and the
  framework propagates the cancel flag back to the parent per the standard
  child→parent rule (`survive_cancel=False` default).

### `optio_host/__init__.py` exports

Re-export `create_download_task` and `DownloadFailed`.

## Execute body

The closure produced by `create_download_task` has signature `async def
_execute(ctx)` (plain `ProcessContext`). Behavior:

1. `basename = os.path.basename(target)`.
2. `ctx.report_progress(None, f"Downloading {basename}")` — single initial
   message.
3. Build the curl command:
   ```
   stdbuf -oL curl --trace-ascii - -s -f -L -o <quoted target> <quoted url>
   ```
   - `stdbuf -oL` forces line-buffered stdout so trace lines flush promptly.
     If unavailable on the host, omit (parser handles chunked input).
   - `-s` silences the normal progress meter.
   - `-f` fails on HTTP ≥ 400 with exit code 22.
   - `-L` follows redirects.
   - `--trace-ascii -` writes the trace to stdout (since `-o` directs the
     body to a file, stdout is otherwise empty).
   - The `-o`'d target file is opened by curl for writing on the host.
4. Run the command:
   - If `host is not None`:
     ```python
     handle = await host.launch_subprocess(
         cmd, cwd=host.workdir, merge_stderr=False,
     )
     ```
     `handle.stdout` and `handle.stderr` are both `AsyncIterator[bytes]`.
   - Else:
     ```python
     proc = await asyncio.create_subprocess_exec(
         "sh", "-c", cmd,
         stdout=asyncio.subprocess.PIPE,
         stderr=asyncio.subprocess.PIPE,
     )
     ```
     Wrap `proc.stdout` and `proc.stderr` as async iterators (`iterate-by-line`
     or `read(N)` chunks).
5. Drive both streams concurrently via `asyncio.gather`:
   - **stdout drain**: parse trace lines. Lowercase each line. On
     `0000: content-length: <N>` set `total = int(N)`. On `<= recv data, <N>
     bytes` set `received += N`; if `total > 0`, `ctx.report_progress(
     min(100.0, received * 100.0 / total), None)`. The throttling already in
     `ProcessContext._flush_progress` handles avalanches — no extra
     debouncing here.
   - **stderr drain**: append each chunk to a `collections.deque` with a
     byte-count cap of 1024. After the subprocess exits, `stderr_tail =
     b"".join(deque).decode("utf-8", errors="replace")`.
6. **Cancel polling**: each iteration of the stdout drain loop checks
   `ctx.should_continue()`. If False, terminate the subprocess:
   - host path: `await host.terminate_subprocess(handle, aggressive=False)`.
   - no-host path: `proc.terminate()` then `await
     asyncio.wait_for(proc.wait(), timeout=5.0)`; on timeout, `proc.kill()`.
   - Break out of both drain loops; let `asyncio.gather` finish.
7. Await the subprocess exit code:
   - host path: `exit_code = await handle.pid_like.wait()` (the existing
     ProcessHandle exposes a wait method via its underlying object; if not,
     read from the iterator-completed semantics or extend ProcessHandle).
   - no-host path: `exit_code = await proc.wait()`.
8. Cancel branch: if cancel was observed in step 6, run cleanup if enabled
   (see below), return normally. Framework sees cancel_flag set → writes
   `cancelled`.
9. Failure branch: if `exit_code != 0` and not cancelled: run cleanup if
   enabled. Raise `DownloadFailed(url=url, target=target,
   exit_code=exit_code, stderr_tail=stderr_tail)`. Framework's
   `_execute_process` catches, writes `status.error = str(DownloadFailed)`,
   terminal `failed`.
10. Success branch: return normally. Framework writes `done`.

**Cleanup-on-fail behavior:** at steps 8 (cancel) and 9 (failure), if
`cleanup_on_fail` is True, best-effort remove the target file:
- host path: `await host.remove_file(target)` (already best-effort/no-op on
  missing in LocalHost; verify same for RemoteHost; swallow exceptions).
- no-host path: `os.remove(target)` in a try/except.

Implementation note: in the host path, `remove_file` may itself raise on
errors other than "missing file". Wrap in try/except and `_log.warning(...)`
on unexpected failures; do not re-raise.

## Cancel semantics

Parent → child propagation is already wired in `Optio.cancel` (lifecycle.py
~line 446): if the parent's TaskInstance has `auto_cancel_children=True`
(default), the cancel sweep recurses to active direct children, setting each
child's `cancel_flag` via `request_cancel_with_deadline`. The download's
execute polls `ctx.should_continue()` and terminates curl.

If the user later sets `auto_cancel_children=False` on the parent task
(opt-out), `Optio.cancel(parent)` will not stop the download child. The
download is still individually cancellable via `Optio.cancel(child_id)`.

## Resume semantics

The download task itself sets `supports_resume=False`. Parent task resume
re-runs its execute body; if the body calls `download_file` again, a fresh
child is created (with the same generated process_id since the counter
restarts, which is fine because `launch_process` calls `clear_result_fields`
→ `delete_descendants` before re-running). Any partially-downloaded file at
the target from a prior crashed run is overwritten by curl's `-o` from byte 0.

## Failure mode reference

| curl exit | meaning                              | DownloadFailed.exit_code |
|----------:|--------------------------------------|--------------------------|
|         6 | could not resolve host (DNS)         |                        6 |
|         7 | failed to connect                    |                        7 |
|        22 | HTTP error ≥ 400 (with `-f`)         |                       22 |
|        28 | operation timed out                  |                       28 |
|        56 | failure receiving network data       |                       56 |

All map to `DownloadFailed`. Caller catches on type (post known-limitation
fix) or by `exit_code` value.

## Known limitation — child failure type loss

The structured `DownloadFailed` exception raised inside the child's execute
is caught by `_execute_process` (executor.py:181-194) and converted to a
string in `status.error`. When the parent's `run_child` sees the child end
in `failed` state, it raises `RuntimeError("Child process '<name>'
failed")`, throwing away the original exception type and fields. Callers of
`download_file` cannot `except DownloadFailed:` — they must catch
`RuntimeError` or read MongoDB.

This is **not** a download-specific issue; it affects every task factory
whose execute raises a typed exception. A note describing the problem and
possible fix shape has been written to `/tmp/optio-child-failure-problem.md`
for the user to pick up separately. v1 of `download_file` accepts the loss.

## Files changed

| File | Change |
|------|--------|
| `packages/optio-host/src/optio_host/download.py` | NEW. Module with `create_download_task`, `DownloadFailed`, and the `_execute` closure. |
| `packages/optio-host/src/optio_host/context.py` | Add `download_file` method to `HookContext`. Add corresponding signature to `HookContextProtocol`. |
| `packages/optio-host/src/optio_host/__init__.py` | Re-export `create_download_task`, `DownloadFailed`. |
| `packages/optio-host/AGENTS.md` | Mention `download.py` under L0 layer; document `HookContext.download_file` and the public factory. |
| `AGENTS.md` (root) | No change. Verified — root file does not enumerate `HookContext` methods today. |
| `packages/optio-host/tests/test_download.py` | NEW. Test cases described below. |

No changes in `optio-core`, no new Python dependencies, no new system
dependencies (curl + stdbuf assumed present; stdbuf absence handled).

## Tests

All in `packages/optio-host/tests/test_download.py`. None require MongoDB;
none start a real Optio runtime. They exercise the parser + cancel logic
against a real curl + a real local HTTP server.

### Fixtures

- `http_server` — pytest fixture that spawns `http.server.ThreadingHTTPServer`
  on `127.0.0.1` with an ephemeral port, serving a temp directory of known
  files. Yields the base URL. Tear-down stops the server.
- `slow_http_server` — variant whose handler sleeps between writes to
  produce a measurable download time (used for cancel test).
- `recording_ctx` — minimal fake `ProcessContext` analogue used by direct
  `_download_execute` calls. Records every `report_progress(percent,
  message)` invocation in a list. Has a settable `cancel_flag` (asyncio.Event).
  Mirrors patterns already in `packages/optio-host/tests/test_context.py`'s
  `_FakeCtx`.

### Test cases

1. **`test_download_file_happy_path_host`** — using `LocalHost(taskdir=tmp_path)`
   and `http_server` serving a 4 MB random blob. Direct call to
   `_download_execute`. Assert:
   - First `report_progress` call is `(None, "Downloading <basename>")`.
   - Subsequent percent values are monotonic non-decreasing, start ≤ 5, end
     at 100.0.
   - Target file's sha256 matches the served content's sha256.

2. **`test_download_file_happy_path_no_host`** — same as 1 but `host=None`
   and `target = tmp_path/"out.bin"` (absolute). Assert same.

3. **`test_download_file_404_cleans_up`** — point `url` at a path the server
   doesn't serve. Assert `DownloadFailed` raised with `exit_code == 22`.
   Assert `stderr_tail` is non-empty and contains a curl error fragment.
   Assert target file does not exist.

4. **`test_download_file_404_no_cleanup`** — same as 3 with
   `cleanup_on_fail=False`. Spy on the cleanup call (monkeypatch
   `host.remove_file` / `os.remove`); assert it was NOT invoked. Filesystem
   state of the target file itself is not asserted — curl's behavior around
   creating-then-not-writing the file is platform-dependent and not the
   spec's contract.

5. **`test_download_file_cancel_mid_stream`** — `slow_http_server` serving
   a large blob, throttled to e.g. 64 KB/s. Start download in an asyncio
   task. After observing the first numeric `report_progress`, set the
   recording_ctx's `cancel_flag`. Await the task. Assert:
   - `_download_execute` returns without raising.
   - Target file does not exist (cleanup_on_fail default True).
   - Total elapsed time is well under the time to fully download the blob.

6. **`test_download_file_routing`** — pure unit test of
   `HookContext.download_file`. Use a fake ctx initialized with
   `_child_counter={"next": 0}` (no prior children) and a recording
   `run_child` that captures the (execute, process_id, name, description)
   tuple. Use a stub host whose `workdir` is a known absolute path. Call
   `await hook_ctx.download_file("https://example/foo.bin",
   "downloads/foo.bin")`. Assert:
   - Generated `process_id` is `f"{parent_pid}.download-0"`.
   - Generated `name` is `"download foo.bin"`.
   - The target path captured by the factory has been resolved against
     `host.workdir` (i.e. equals `f"{host.workdir}/downloads/foo.bin"`).
   - A second call generates `download-1`.
   - `_resolve_target_path` rejection cases (workdir-escape, `..`) bubble
     out as `ValueError` without spawning a child.

No `stdbuf`-absent unit test is required. Implementation decision deferred
to coding: either probe at call-time (`shutil.which("stdbuf")`) or wrap the
command so curl runs regardless. Spec-level contract: the feature must
function on a host without stdbuf — only the responsiveness of progress
updates degrades.

## Risks

- **curl `--trace-ascii` format drift.** Format has been stable for many
  curl versions; parsing only two specific lowercased line prefixes; install
  script in the user's opencode fork already relies on the same shape.
  Low risk.
- **Buffering when `stdbuf` absent.** Without `stdbuf -oL`, curl may
  buffer stdout in 4 KB chunks → progress updates batchy but still
  monotonic. Functionally fine; UX degrades on tiny downloads. Acceptable.
- **Cleanup race on cancel.** Curl may still hold the file descriptor when
  cleanup tries to remove. On POSIX this is fine — `unlink` removes the
  dirent and the data is freed when the fd closes. No special handling.
- **stderr_tail truncation under chunky stderr.** Cap at 1 KB; curl's error
  messages are short, never approach the cap. Safe.

## Out of scope (catalogued for future work)

- Resume of partial downloads (`curl -C -`).
- Custom headers / auth / timeout / TLS bypass.
- Returning content metadata (etag, last-modified, content-type) to the caller.
- Integrity verification (sha256 expected, like `copy_file`'s
  `skip_if_unchanged`).
- A symmetric `upload_url` for HTTP PUT/POST uploads.
- Fixing the child-failure-type-loss across `run_child` (see
  `/tmp/optio-child-failure-problem.md`).
- Demo / integration exercise in `optio-demo` — the user has a specific
  consumer in mind.
