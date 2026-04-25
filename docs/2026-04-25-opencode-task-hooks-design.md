# Opencode Task Hooks: `before_execute` / `after_execute`

**Base revision:** `39d1692bebf021057fccde67af22ff1a58b53d23` on branch `main` (as of 2026-04-25T14:52:26Z)

## Summary

Add two optional async hooks — `before_execute` and `after_execute` — to
`OpencodeTaskConfig`, plus a unified `HookContext` that the hooks (and the
reworked `on_deliverable` callback) receive. `HookContext` is the existing
`ProcessContext` extended with four host-aware primitives: `copy_file`,
`run_on_host`, `read_from_host`, `read_text_from_host`. The hooks let
consumer apps run their own setup/teardown logic *inside* the
`run_opencode_session` pipeline, with the host already connected and the
workdir already provisioned, so they can ship their own files to the host
the same way the framework ships the opencode binary today.

`create_opencode_task()` becomes a complete `TaskInstance` factory: consumer
code drops it directly into `get_tasks()`; no wrapper `_execute(ctx)` is
needed for any per-run customization.

## Motivation

Today, opencode-based tasks have no way to do per-task preparation on the
host that will run opencode. The framework already ships the opencode
binary itself via `host.install_opencode_binary` (an SFTP put with
SHA-256 skip + atomic rename), but consumers can't piggyback on that
mechanism for their own files — config templates, datasets, secrets, or
generated assets that the opencode session needs in its workdir.

**The current `optio-demo` wrapper-`_execute` pattern is not a forced
workaround for an API gap; it is over-engineering driven by a
misunderstanding.** The demo wraps `create_opencode_task()` inside a
custom `_execute(ctx)` for two stated reasons, both of which dissolve on
inspection:

1. *`_resolve_ssh_config()` runs at execute time so env-var changes
   between worker restarts are picked up.* Env vars are read at the
   worker process's startup; calling the resolver at module load
   (inside `get_tasks()`) gives the same behavior with less ceremony.
2. *`_make_on_deliverable(ctx)` closes over `ctx` so the callback can
   call `report_progress`.* The framework already auto-emits
   `"Deliverable: <path>"` to `report_progress` for every received
   deliverable (session.py:374, in `_tail_and_dispatch`). The demo's
   callback adds a content snippet on top of that, but the basic
   "deliverable exists" visibility is free. If the demo had simply
   passed `on_deliverable=None`, no closure (and no wrapper) would have
   been required at all.

So the wrapper-`_execute` pattern existed for nothing the API actually
demanded. The proper fix is twofold: (a) give the public API real hooks
so consumers who *do* want to run code at execute time have a clean
place for it; (b) rework `on_deliverable` to receive the hook context
as its first argument — eliminating the closure-over-ctx pattern even
for callbacks that genuinely want it.

This change also opens a second, larger door: consumer apps that need
to ship their own files to the host get a uniform, supported mechanism
for doing so, instead of having to build out their own SFTP plumbing.

## Goals

1. A consumer task can ship its own files (worker → host) before
   opencode starts, with the same atomic-rename + progress-reporting
   guarantees the binary install already has.
2. A consumer task can run arbitrary shell commands on the host
   (`whoami`, `mkdir`, `git clone`, etc.).
3. A consumer task can pull files back from the host (host → worker)
   after opencode finishes, before workdir cleanup.
4. The hooks work uniformly for both `LocalHost` and `RemoteHost`.
5. `optio-demo` becomes the canonical example: it stops using the
   wrapper-`_execute` pattern and gains a `before_execute` hook that
   runs `whoami` on the host and reports the result.

## Non-Goals

- No async-generator / streaming hook outputs. Hooks are simple async
  functions returning `None`.
- No declarative "list of files to deliver" alternative — the
  imperative hook approach is the only path.
- No changes to the resume feature or other in-flight work; this is
  purely additive in the opencode-task surface.
- No Windows local-host support for `run_on_host` — same constraint
  the rest of optio-opencode has today.

## API

### `HookContext`

New module: `packages/optio-opencode/src/optio_opencode/hook_context.py`,
exporting `HookContext`, `HookContextProtocol`, `RunResult`, and
`HostCommandError`.

```python
class HookContext:
    def __init__(self, ctx: ProcessContext, host: Host) -> None:
        self._ctx = ctx
        self._host = host

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ctx, name)

    async def copy_file(
        self,
        source: str | os.PathLike | bytes | GridFSBlobHandle,
        target: str,
        *,
        skip_if_unchanged: bool = False,
    ) -> None: ...

    async def run_on_host(
        self,
        command: str,
        *,
        check: bool = True,
        capture_stderr: bool = False,
        cwd: str | None = None,
    ) -> RunResult | str: ...

    async def read_from_host(self, path: str) -> bytes: ...

    async def read_text_from_host(self, path: str) -> str: ...
```

`HookContextProtocol` is a `typing.Protocol` listing the most-used
`ProcessContext` methods (`report_progress`, `should_continue`,
`params`, `metadata`, `services`, `set_widget_data`, GridFS blob
helpers) plus the four new ones — for IDE discoverability without
relying on `__getattr__`.

### `copy_file(source, target, *, skip_if_unchanged=False)`

- `source` is one of: a path-like (read from worker filesystem),
  `bytes` (shipped directly without a worker temp file), or a
  `GridFSBlobHandle` from `optio-core` (streamed through the transfer
  primitive without materializing a worker temp file).
- `target` follows Unix path conventions to encode where it goes:
  - Starts with `/` → absolute host path (e.g.
    `/usr/local/bin/mytool`); used as-is.
  - Starts with `~/` (or is exactly `~`) → home-relative on the host;
    `~` is expanded by the host to `<host_home>/<rest>` (the SSH
    user's home for `RemoteHost`, the worker user's home for
    `LocalHost`). Useful for `~/.local/bin/...` style installs.
  - Otherwise → **workdir-relative**: resolved to `<workdir>/<target>`.
- Parent directories are auto-created in all three cases (mkdir -p
  semantics for the parent of the target).
- Validation rules:
  - Workdir-relative paths: reject `..` segments and any resolved
    path outside `<workdir>` (sandbox).
  - Absolute and home-relative paths: trusted; no sandbox check.
    Consumers asking for `/usr/local/bin/...` or
    `~/.local/bin/...` know what they're doing.
  - Empty `target` is rejected in all cases.
- For `RemoteHost`: SFTP put with the same atomic-rename pattern as
  `install_opencode_binary` (write to `<target>.tmp`, fsync, rename).
- `skip_if_unchanged=True`: enables a checksum-based skip. Computes
  SHA-256 of `source` (path → streaming hash, bytes → hash directly,
  `GridFSBlobHandle` → use the blob's stored checksum if available,
  otherwise hash on read) and SHA-256 of `target` (locally for
  `LocalHost`, via remote `sha256sum` for `RemoteHost`). If hashes
  match, the transfer is skipped. If `target` does not exist, the
  copy proceeds normally. Default `False`: always copy.
- Progress reporting:
  - With `skip_if_unchanged=False`:
    - On entry: `ctx.report_progress(None, f"Copying {basename(target)}...")`.
    - During: numerical `ctx.report_progress(percent, None)` updates
      as bytes flow. Throttled to ≤ ~10 Hz to avoid spamming the
      channel for small files.
  - With `skip_if_unchanged=True`:
    - On entry: `ctx.report_progress(None, f"Verifying {basename(target)}...")`.
    - If hashes match → `ctx.report_progress(None, f"Already up to date: {basename(target)}")` and return.
    - Otherwise → fall through to the normal "Copying ..." +
      percent-updates flow.
  - `basename(target)` is used as the universal identifier across
    all source types (path / bytes / blob), since `bytes` and blob
    sources don't have a meaningful source filename.

### `run_on_host(command, *, check=True, capture_stderr=False, cwd=None)`

- Runs `command` via `/bin/sh -c` on the host. Default `cwd` is the
  workdir; `cwd=` overrides.
- `check=True` (default): on exit ≠ 0, raises `HostCommandError(command,
  exit_code, stdout, stderr)`. On success, returns the **string** of
  stdout (decoded UTF-8).
- `check=False`: never raises on exit code; returns a
  `RunResult(stdout, stderr, exit_code)` dataclass.
- `capture_stderr=True`: with `check=True`, stderr is merged into the
  returned stdout; with `check=False`, the result still has separate
  fields (the flag only affects the happy-path return shape).

`HostCommandError` subclasses `Exception`; its `__str__` includes
exit code and the first 200 chars of stderr.

### `read_from_host(path)` / `read_text_from_host(path)`

- `path` follows the same three-form convention as `copy_file`'s
  `target`: workdir-relative (sandboxed), `~/`-prefixed (home-relative
  on the host), or absolute (`/`-prefixed). Empty `path` is rejected.
- Returns full contents as `bytes` / decoded UTF-8 `str`. No streaming
  for now.
- Progress reporting:
  - On entry: `ctx.report_progress(None, f"Reading {basename(path)}...")`.
  - During: numerical percent updates if the host reports a content
    length (SFTP stat), otherwise just the start message.

### `OpencodeTaskConfig` changes

```python
@dataclass
class OpencodeTaskConfig:
    consumer_instructions: str
    opencode_config: dict[str, Any] = field(default_factory=dict)
    ssh: SSHConfig | None = None
    on_deliverable: DeliverableCallback | None = None       # signature changes
    install_if_missing: bool = True
    before_execute: HookCallback | None = None              # NEW
    after_execute: HookCallback | None = None               # NEW
```

```python
HookCallback = Callable[[HookContext], Awaitable[None]]
DeliverableCallback = Callable[[HookContext, str, str], Awaitable[None]]  # was (str, str)
```

Hooks must be async (no sync support — keeps the contract simple).

## Pipeline integration

`run_opencode_session` is extended with two hook slots:

```
connect → setup_workdir + write AGENTS.md/opencode.json → install binary 
       → [BEFORE_EXECUTE] → launch opencode → tunnel → run loop
       → [AFTER_EXECUTE]  → cleanup_workdir → disconnect
```

`before_execute` runs on a fully-provisioned host: workdir exists,
`AGENTS.md` and `opencode.json` are written, opencode binary is on
the host, but opencode itself is not yet running.

`after_execute` runs after opencode has terminated (or been
cancelled) but before the workdir is wiped — giving it a last chance
to fetch artifacts via `read_from_host`.

Implementation shape:

```python
host = ...
hook_ctx: HookContext | None = None
session_error: BaseException | None = None
try:
    await host.connect()
    await host.setup_workdir()
    await host.write_text("AGENTS.md", ...)
    await host.write_text("opencode.json", ...)
    await _install_or_ensure_binary(host, config)

    hook_ctx = HookContext(ctx, host)
    if config.before_execute is not None:
        await config.before_execute(hook_ctx)

    process = await host.launch_opencode(...)
    # ... tunnel + run loop ...
except BaseException as exc:
    session_error = exc
    raise
finally:
    if config.after_execute is not None and hook_ctx is not None:
        try:
            await config.after_execute(hook_ctx)
        except BaseException as after_exc:
            if session_error is None:
                raise
            ctx.report_progress(None, f"after_execute callback raised: {after_exc!r}")
    if host.is_connected:
        try:
            await host.cleanup_workdir()
        finally:
            await host.disconnect()
```

`_deliverable_fetch_loop` is updated to construct the same `HookContext`
once per run and pass it into `on_deliverable(hook_ctx, path, text)`.
The error-recovery `report_progress` calls in that loop are unchanged.

## Failure semantics

- **`before_execute` raises** → session fails. Opencode never
  launches; tunnel never established. `after_execute` still runs
  (always-runs semantics). `cleanup_workdir` and `disconnect` still
  run. The original `before_execute` exception is what the executor
  sees as the failure cause.
- **`after_execute` raises on a successful session** → session is
  marked failed with the `after_execute` exception.
- **`after_execute` raises on an already-failing session** → exception
  is reported via `ctx.report_progress("after_execute callback
  raised: ...")` and does not shadow the original failure cause.
  Mirrors the existing `on_deliverable` error handling.
- **Cancellation during `before_execute`** → `CancelledError`
  propagates; `after_execute` still runs (with a few-seconds budget
  before being itself cancelled); cleanup runs.
- **Cancellation during `after_execute`** → `CancelledError`
  propagates; `cleanup_workdir` + `disconnect` still run.
- **Hook never runs**: if we fail before connect/setup (so there is
  no host yet), `hook_ctx` is still `None` and `after_execute` is
  skipped — there is nothing meaningful for it to operate on.

## Host protocol extensions

The `HookContext` methods delegate to new low-level primitives on
`Host`. Both `LocalHost` and `RemoteHost` get implementations.

### New `Host` methods

```python
class Host(Protocol):
    workdir: str  # already exists; surfaced explicitly here

    async def put_file_to_host(
        self,
        source: str | os.PathLike | bytes | GridFSBlobHandle,
        absolute_target: str,
        *,
        skip_if_unchanged: bool = False,
        progress_cb: Callable[[float | None, str | None], None] | None = None,
    ) -> None: ...

    async def fetch_bytes_from_host(
        self,
        absolute_path: str,
        *,
        progress_cb: Callable[[float | None, str | None], None] | None = None,
    ) -> bytes: ...

    async def run_command(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> RunResult: ...
```

These are the **generic** primitives. `HookContext.copy_file` /
`read_from_host` resolve the user-supplied path according to the
three-form convention (workdir-relative / `~/`-home-relative /
absolute), pass the resulting absolute host path to these methods,
forward `skip_if_unchanged`, wire `progress_cb` into
`ctx.report_progress`, and return. `HookContext.run_on_host` calls
`host.run_command` and applies the `check=True` raise behavior.

`put_file_to_host`'s `skip_if_unchanged=True` semantics:

- Compute SHA-256 of `source` (path → streaming hash, bytes → hash
  directly, `GridFSBlobHandle` → use stored checksum if available,
  else hash on read).
- Compute SHA-256 of `absolute_target` if it exists. For `LocalHost`,
  read it locally; for `RemoteHost`, run `sha256sum` over SSH (or
  fall back to streaming via SFTP if `sha256sum` is unavailable).
- If hashes match, return without transferring. If `absolute_target`
  does not exist, proceed with normal copy.

### `LocalHost` implementations

- `put_file_to_host`: source-path → `aiofiles` read + write to a temp
  file alongside target, then `os.replace` for atomic rename. Bytes →
  write to temp + replace. `GridFSBlobHandle` → stream chunks from
  blob to temp file + replace. Progress callback fires per-chunk
  (16 KB chunks, throttled).
- `fetch_bytes_from_host`: read full file via `aiofiles`. Progress
  fires once per chunk while reading.
- `run_command`: `asyncio.create_subprocess_exec("/bin/sh", "-c",
  command, cwd=cwd, env=env)` and capture stdout/stderr. Returns a
  `RunResult(stdout, stderr, exit_code)`.

### `RemoteHost` implementations

- `put_file_to_host`: SFTP put with the same atomic-rename pattern
  already in `install_opencode_binary` (lines 663-736). For path
  source: stream from disk. For bytes: stream from `io.BytesIO`. For
  `GridFSBlobHandle`: stream blob chunks directly into the SFTP file
  handle (no worker temp file). Progress fires per-chunk based on
  transferred / total bytes.
- `fetch_bytes_from_host`: SFTP open + read in chunks; progress fires
  if SFTP stat reports a size, otherwise just the start message.
- `run_command`: `await self._conn.run(command, cwd=cwd, env=env,
  check=False)`. Returns a `RunResult` from the asyncssh result.

### Refactoring `install_opencode_binary`

After this change, `RemoteHost.install_opencode_binary` is a thin
wrapper that resolves the install path, delegates the transfer
(including the SHA-256 skip) to `put_file_to_host`, and chmods the
result:

```python
async def install_opencode_binary(self, local_path, *, progress=None):
    install_path = await self._resolve_install_path()  # e.g. ~/.opencode/bin/opencode
    await self.put_file_to_host(
        local_path,
        install_path,
        skip_if_unchanged=True,
        progress_cb=progress,
    )
    await self.run_command(f"chmod +x {install_path}")
```

All the SFTP plumbing, atomic-rename, and SHA-256 skip logic now
lives in `put_file_to_host` and is inherited from there.

`LocalHost.install_opencode_binary` is unchanged — it still just
stores a path (no copy needed when worker and host share a
filesystem).

The "Installing opencode binary..." high-level progress message
stays; it sits on top of the generic
"Verifying ..." / "Already up to date" / "Copying ..." messages
emitted by `put_file_to_host`.

### Path resolution helper

A small helper `_resolve_target_path(path: str, workdir: str,
host_home: str) -> str` implements the three-form convention:

- Empty `path` → raise `ValueError`.
- `path` starts with `/` → return as-is (absolute host path).
- `path == "~"` or starts with `~/` → return
  `<host_home> + rest` (home-relative; `~` is expanded once,
  no further user expansion attempted).
- Otherwise → workdir-relative:
  - Reject `..` segments and any resolved path that escapes
    `<workdir>` (sandbox).
  - Return `<workdir>/<path>`.

`host_home` is queried lazily and cached on the `Host` (one
`pwd` / `echo $HOME` per session). For `LocalHost`, it's
`os.path.expanduser("~")` on the worker.

Used by `HookContext.copy_file`, `read_from_host`, and
`read_text_from_host`.

## `optio-demo` rewrite

The new `packages/optio-demo/src/optio_demo/tasks/opencode.py`:

```python
"""Reference demo task for optio-opencode."""

import os

from optio_core.models import TaskInstance
from optio_opencode import (
    HookContext,
    OpencodeTaskConfig,
    SSHConfig,
    create_opencode_task,
)


CONSUMER_PROMPT = (
    "Tell me the hostname of the system you are running on. "
    "Then ask the human about their favorite color, then ship a "
    "deliverable containing the number 42 and the designated color. "
    "Then signal completion by appending a `DONE` line to the "
    "`./optio.log` file (writing `DONE` in the chat has no effect — "
    "it must go into that file)."
)


def _resolve_ssh_config() -> SSHConfig | None:
    host = os.environ.get("OPTIO_OPENCODE_DEMO_SSH_HOST")
    if not host:
        return None
    user = (
        os.environ.get("OPTIO_OPENCODE_DEMO_SSH_USER")
        or os.environ.get("USER")
        or "root"
    )
    key_path = os.environ.get(
        "OPTIO_OPENCODE_DEMO_SSH_KEY_PATH",
        os.path.expanduser("~/.ssh/id_ed25519"),
    )
    port_raw = os.environ.get("OPTIO_OPENCODE_DEMO_SSH_PORT", "22")
    try:
        port = int(port_raw)
    except ValueError:
        raise RuntimeError(
            f"OPTIO_OPENCODE_DEMO_SSH_PORT must be an integer, got {port_raw!r}"
        )
    return SSHConfig(host=host, user=user, key_path=key_path, port=port)


async def _before_execute(hook_ctx: HookContext) -> None:
    out = await hook_ctx.run_on_host("whoami")
    hook_ctx.report_progress(None, f"opencode will run as {out.strip()}")


async def _on_deliverable(hook_ctx: HookContext, path: str, text: str) -> None:
    print(f"[opencode-demo] deliverable {path}:\n{text}")


def get_tasks() -> list[TaskInstance]:
    return [
        create_opencode_task(
            process_id="opencode-demo",
            name="Opencode demo",
            description=(
                "Opencode session asking for a color and shipping a "
                "deliverable. Runs `whoami` on the host before launching "
                "opencode, and prints any deliverable to the worker terminal. "
                "Set OPTIO_OPENCODE_DEMO_SSH_HOST to run remotely; "
                "otherwise runs locally."
            ),
            config=OpencodeTaskConfig(
                consumer_instructions=CONSUMER_PROMPT,
                ssh=_resolve_ssh_config(),
                before_execute=_before_execute,
                on_deliverable=_on_deliverable,
            ),
        )
    ]
```

Key contrasts with the previous demo:

- No outer `_execute(ctx)` wrapper. `create_opencode_task()` returns
  the `TaskInstance` directly; `get_tasks()` just lists it.
- No `_make_on_deliverable(ctx)` factory. The callback is a plain
  top-level function with the new `(hook_ctx, path, text)` signature
  and doesn't need `hook_ctx` (it just prints) — demonstrating that
  the new signature accommodates trivial callbacks.
- `_resolve_ssh_config()` is called at module load (inside
  `get_tasks()`), not deferred to execute time. Same env-var
  behavior, less ceremony.
- A `before_execute` hook demonstrates `run_on_host` + `report_progress`
  on the unified `HookContext`.

## Documentation updates

`packages/optio-opencode/AGENTS.md` (currently 121 lines) needs:

1. **New "Hooks" section** between the existing "Public API" and
   "Log-file contract" sections. Documents `before_execute` /
   `after_execute` signatures, when they fire in the pipeline, the
   `HookContext` API, failure semantics, and the workdir-relative
   `target` rule.
2. **Updated "Public API" section** showing the canonical pattern —
   `create_opencode_task()` returning a `TaskInstance` you drop into
   `get_tasks()`. Replaces any prose endorsing the wrapper-`_execute`
   pattern.
3. **Updated `on_deliverable` description** with the new
   `(hook_ctx, path, text)` signature.
4. A short note that the framework auto-emits `"Deliverable: <path>"`
   to `report_progress` for every received deliverable — so consumers
   only need an `on_deliverable` callback if they want to do something
   *additional* (custom processing, content snippet logging, etc.).

## Migration notes

Breaking change: `DeliverableCallback` signature.

- **Before:** `async def cb(path: str, text: str) -> None`
- **After:** `async def cb(hook_ctx: HookContext, path: str, text: str) -> None`

External consumers (e.g. `guy-montag`) need a one-line update: rename
the function so that `hook_ctx` is the first argument. No
compatibility shim is provided.

Internal consumer (`optio-demo`) is rewritten as part of this feature
(see "`optio-demo` rewrite" above).

The wrapper-`_execute` pattern is no longer recommended. Existing
external consumers using it will continue to work (their wrapper just
calls `inner.execute(ctx)`, which still functions), but the AGENTS.md
docs will steer new consumers toward the direct
`create_opencode_task()` pattern.

## Testing

### `packages/optio-opencode/tests/test_hook_context.py`

Exercises `HookContext` against fake `Host` implementations:

- `copy_file` with each source type (path, bytes, GridFS blob);
  asserts `put_file_to_host` is called with the resolved absolute target.
- `copy_file` path-resolution matrix:
  - Workdir-relative `"data/foo.yaml"` → resolves to
    `<workdir>/data/foo.yaml`.
  - Absolute `"/usr/local/bin/tool"` → passed as-is to
    `put_file_to_host`.
  - Home-relative `"~/.local/bin/tool"` → expanded using cached
    `host_home` to `<host_home>/.local/bin/tool`.
- `copy_file` rejects: empty target, workdir-relative targets with
  `..` segments, workdir-relative paths whose resolved form escapes
  the workdir.
- `copy_file` does NOT reject `..` in absolute or `~/`-prefixed
  targets (consumer-trusted forms).
- `copy_file` emits `"Copying <basename>..."` and percent updates
  (asserted against a recording `ctx.report_progress` fake).
- `copy_file(skip_if_unchanged=True)` matrix:
  - Target does not exist → normal copy proceeds; "Verifying ..." +
    "Copying ..." messages emitted.
  - Target exists with matching SHA → transfer is skipped;
    "Verifying ..." then "Already up to date: ..." messages
    emitted; no bytes transferred (assert `put_file_to_host` records
    a skip).
  - Target exists with different SHA → fall through to copy;
    "Verifying ..." + "Copying ..." both emitted.
  - Source variant covered for each: path, bytes, GridFS blob.
- `run_on_host` returns stdout string on exit 0 with `check=True`.
- `run_on_host` raises `HostCommandError` on non-zero exit with
  `check=True`; the error carries exit code + stderr.
- `run_on_host(check=False)` returns `RunResult` regardless of exit
  code, never raises on exit code alone.
- `run_on_host(capture_stderr=True, check=True)` returns merged
  stdout+stderr.
- `run_on_host(cwd=...)` overrides default workdir cwd.
- `read_from_host` / `read_text_from_host` validate paths, emit
  `"Reading <basename>..."`, return correct content.
- `__getattr__` delegation — `report_progress`, `params`,
  `should_continue` on a `HookContext` fall through to the wrapped
  `ProcessContext`.

### `packages/optio-opencode/tests/test_session_hooks.py`

Exercises hook integration with `run_opencode_session`, using fake
`Host` + `fake_opencode`:

- `before_execute` runs after binary install, before opencode
  launches (assert ordering via a shared timeline list each
  fake-method appends to).
- `before_execute` raising → session ends in failure; opencode
  launch is never called; `after_execute` still runs;
  `cleanup_workdir` and `disconnect` still run; the original
  `before_execute` exception is what the executor sees.
- `after_execute` runs on success path, on opencode failure path, on
  cancellation path. Three separate test cases.
- `after_execute` raising on a successful session → session is
  marked failed with the `after_execute` exception.
- `after_execute` raising on an already-failing session → exception
  is reported via `ctx.report_progress` and does not shadow the
  original failure cause.
- Hooks not configured (`before_execute=None`, `after_execute=None`)
  → session behaves exactly as before this change. (Regression
  guard.)

### `packages/optio-opencode/tests/test_on_deliverable_signature.py`

- Callback receives `(hook_ctx, path, text)`. Assert
  `hook_ctx.run_on_host` is callable inside the deliverable callback
  (host primitives work during the run loop too, not just in
  before/after hooks).
- Callback raising still surfaces via
  `ctx.report_progress("on_deliverable callback raised: ...")`
  (existing behavior preserved).
- `on_deliverable=None` path unchanged.

### `packages/optio-opencode/tests/test_host_primitives.py`

Exercises the new low-level `Host` methods, using the fake_opencode
SSH harness for `RemoteHost` and a tempdir for `LocalHost`:

- `LocalHost.put_file_to_host` from path / bytes / GridFS blob, target
  absolute → file present, atomic-rename used (assert no `*.tmp`
  left around on success or simulated mid-write failure).
- `LocalHost.put_file_to_host(skip_if_unchanged=True)`:
  - Missing target → copy proceeds.
  - Target exists, same content → transfer skipped (assert no
    bytes-written / no-temp-file-created).
  - Target exists, different content → copy proceeds, replaces target
    atomically.
- `LocalHost.fetch_bytes_from_host` returns full bytes.
- `LocalHost.run_command` returns `RunResult` with correct
  stdout/stderr/exit_code; `cwd` is honored.
- `RemoteHost.put_file_to_host` (against the existing test SSH
  harness) — same matrix as LocalHost, including the
  `skip_if_unchanged` cases (asserting `sha256sum` is invoked over
  SSH for the existing-target hash).
- `RemoteHost.run_command` honors cwd and env via asyncssh.
- `host_home` resolution: cached lookup via `pwd` /
  `os.path.expanduser("~")` returns expected home directory; tilde
  expansion in `put_file_to_host`-callers (the `HookContext` layer
  exercises this) lands files at the correct path on both host
  types.
- **Regression test for `install_opencode_binary`:** unchanged
  externally — still uploads, SHA-256 skips, chmods. Internally now
  goes through `put_file_to_host`. Test asserts the binary lands at
  the expected path with executable bit set, both for first-install
  and SHA-skip cases.

### `packages/optio-demo/tests/test_demo_smoke.py`

Minimal smoke test that imports `optio_demo.tasks.opencode.get_tasks()`
and asserts the returned list has one fully-formed `TaskInstance` with
`process_id="opencode-demo"`. No execution. Catches the most basic
regression (e.g. importing fails after rename).

### Out-of-scope

- No browser/iframe end-to-end tests. Opencode UI behavior is
  opencode's, not ours.
- No `optio-ui` changes; nothing to test there.
- No `optio-core` test changes. `ProcessContext` itself doesn't
  change.

## Risks & open questions

- **`__getattr__` discoverability.** IDE autocomplete on
  `HookContext` won't surface `ProcessContext` methods unless the
  consumer type-hints against `HookContextProtocol`. Mitigation: the
  AGENTS.md docs and the demo show the protocol form. We can
  reconsider an explicit-delegation variant later if real consumers
  trip on this.
- **GridFS blob handle import shape.** The `GridFSBlobHandle` type
  lives in optio-core today. We'll need it importable from
  `optio_opencode` (re-exported) so `HookContext.copy_file`'s type
  hint reads cleanly. If the type isn't yet a stable name, plan
  step 1 will pin that down.
- **Throttling of progress callbacks.** A 10 Hz cap is a guess;
  during implementation we may discover the optio-ui rendering layer
  prefers a different rate. Tunable via a module constant.
- **Workdir-relative `..` policy.** We reject any `..` segment in
  workdir-relative paths, even ones that resolve safely back inside
  the workdir — strictly conservative. Absolute and `~/`-prefixed
  paths are not subject to this check (consumer-trusted forms).
  If a real consumer scenario needs `..` inside workdir-relative
  paths, we can loosen by resolving and checking the absolute
  result against the workdir prefix.
- **`sha256sum` availability for `skip_if_unchanged` over SSH.**
  Most Linux hosts have `sha256sum`; macOS hosts have `shasum -a 256`
  but not `sha256sum` by default. If neither is available,
  `put_file_to_host` falls back to streaming the target via SFTP
  and hashing locally — slower but correct. Plan step will pin which
  detection logic we use.
