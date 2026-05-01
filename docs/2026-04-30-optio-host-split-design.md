# Split optio-opencode: extract optio-host base layer

**Base revision:** `191fd39f5880a7c13f06ed0daa1b174d1cb8703c` on branch `main` (as of 2026-04-30T00:00:00Z)

## Summary

Carve out a new `optio-host` package containing the generic remote-execution
primitives + the log/deliverables coordination protocol currently bundled inside
`optio-opencode`. Slim `optio-opencode` to its opencode-specific concerns
(binary install, opencode launch, snapshot/resume, AGENTS.md prompt, embedded
iframe widget). Public API of `optio-opencode` preserved via re-exports —
zero changes for downstream consumers (excavator engine).

This refactor is a prerequisite for a separate spec ("recipe execution task")
which will be the second consumer of `optio-host`. That spec is not part of
this work.

## Motivation

Two coupled goals:

1. **Reuse for new task types.** Excavator wants a recipe-execution optio task
   that runs `recipe-dsl` on local or remote hosts, with the same SSH-or-local
   abstraction + per-task workdir + progress-reporting protocol that
   `optio-opencode` already has. Today this would require duplicating
   `optio-opencode`'s host abstraction or importing from it as a dependency
   (which would also pull in opencode-specific code).

2. **Layering hygiene.** `optio-opencode`'s `host.py` (1341 LoC) currently mixes
   generic SSH/local primitives with opencode-specific actions
   (`launch_opencode`, `opencode_import/export`, etc.). The `Host` Protocol's
   public surface includes both. Reusing primitives without inheriting opencode
   noise requires separating them.

The split also clarifies what code is opencode-specific vs. what would apply
to any host-task: a useful invariant going forward.

## Out of scope

- Generalizing `snapshots.py` collection schema (single consumer; speculative).
- Replacing `tail` shell-out in `tail_file` with native asyncio file watcher
  (its own sub-project; current shell-out works).
- Moving `compose_agents_md` (resume-aware prompt) — stays in opencode.
- Refactoring `ensure_opencode_installed` / `install_opencode_binary` to
  fully eliminate isinstance dispatch (accepted internal leak; see leak
  audit below).
- Recipe-execution task design (separate spec, in a separate repo).

## Architecture overview

Three layers, two packages:

- **`optio-host`** (new package).
  - **L0 — host primitives.** `Host` Protocol + `LocalHost`/`RemoteHost`
    implementations. Generic remote-execution primitives only. Knows nothing
    about opencode.
  - **L1 — log/deliverables protocol.** Pure parser for the `STATUS:` /
    `DELIVERABLE:` / `DONE` / `ERROR` log convention; generic session driver
    that runs a body callback against a host while tail+dispatch and
    deliverable-fetch loops cooperate. Built on L0.
- **`optio-opencode`** (slimmed). All opencode-specific concerns:
  binary install, launch, tunnel + widget, resume + snapshot, AGENTS.md
  composition. Pure consumer of `optio-host`.

Layering rule: `optio_host.protocol` may import from `optio_host.host`/
`context`/`archive`; reverse forbidden. Verified by import-discipline review.

## Section 1 — Package layout

New package: `~/deai/optio/packages/optio-host/`. Single `pyproject.toml`.

```
optio_host/
  __init__.py            # top-level re-exports
  host.py                # Host Protocol + LocalHost + RemoteHost + ProcessHandle + make_host
  context.py             # HookContext, HookContextProtocol, RunResult, HostCommandError
  types.py               # SSHConfig
  archive.py             # workdir tar/untar
  paths.py               # task_dir(*, ssh, process_id, consumer_name)
  protocol/
    __init__.py          # re-exports
    parser.py            # parse_log_line + event types + validate_deliverable_path
    session.py           # run_log_protocol_session + DeliverableCallback + HookCallback + fetch_deliverable_text
```

`optio-opencode` becomes a downstream consumer. Imports flow one way only.

## Section 2 — `optio_host.host` surface

`Host` Protocol — generic methods only.

**Existing generic methods (unchanged semantics unless noted):**

```python
class Host(Protocol):
    workdir: str          # absolute path on host where work runs
    taskdir: str          # absolute path of per-process taskdir (workdir's parent)

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def setup_workdir(self) -> None: ...
        # CHANGED: now mkdir -p workdir only. Protocol artifacts (deliverables/, optio.log)
        # moved to optio_host.protocol.session's setup phase.
    async def write_text(self, relpath: str, content: str) -> None: ...
    async def run_command(
        self, command: str, *,
        cwd: str | None = None, env: dict[str, str] | None = None,
    ) -> RunResult: ...
    async def put_file_to_host(
        self, source, absolute_target: str, *,
        expected_sha256: str | None = None,
        skip_if_unchanged: bool = False,
        progress_cb=None,
    ) -> None: ...
    async def fetch_bytes_from_host(
        self, absolute_path: str, *, progress_cb=None,
    ) -> bytes: ...
    def archive_workdir(self, exclude: list[str] | None) -> AsyncIterator[bytes]: ...
    async def restore_workdir(self, stream: AsyncIterator[bytes]) -> None: ...
    async def cleanup_taskdir(self, aggressive: bool) -> None: ...
    async def resolve_host_home(self) -> str: ...
    async def establish_tunnel(self, remote_port: int) -> int: ...
        # RENAMED: parameter was opencode_port; behavior unchanged.
    async def remove_file(self, path: str) -> None: ...
    async def tail_file(self, absolute_path: str) -> AsyncIterator[str]: ...
        # RENAMED: from tail_log() with hardcoded path workdir/optio.log.
        # Caller passes path; opencode session passes workdir/optio.log.
```

**New primitives — sibling to `run_command`:**

```python
@dataclass
class ProcessHandle:
    pid_like: object              # opaque (asyncio.Process | asyncssh.SSHClientProcess)
    stdout: AsyncIterator[bytes]  # live stdout (caller can fold stderr in via 2>&1 in cmd)

async def launch_subprocess(
    self,
    cmd: list[str] | str,
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> ProcessHandle: ...

async def terminate_subprocess(
    self,
    handle: ProcessHandle,
    *,
    aggressive: bool,
) -> None: ...
```

Semantics:

- `launch_subprocess` returns BEFORE the subprocess exits. `stdout` yields
  chunks as they arrive; ends when the proc closes its stdout. Caller is
  responsible for terminating the proc (or letting it exit on its own).
- `aggressive=False` → SIGTERM, wait up to 5s, then SIGKILL. `aggressive=True`
  → SIGKILL immediately, no wait.
- LocalHost impl uses `asyncio.create_subprocess_exec` + iterates `proc.stdout`.
  RemoteHost impl uses `self._conn.create_process` + iterates `proc.stdout`.
  Both extracted from existing `launch_opencode` bodies.

**Factory function:**

```python
def make_host(*, ssh: SSHConfig | None, taskdir: str) -> Host:
    """Construct LocalHost (ssh=None) or RemoteHost. Used by consumers
    to avoid naming the implementation classes directly."""
    if ssh is None:
        return LocalHost(taskdir=taskdir)
    return RemoteHost(ssh_config=ssh, taskdir=taskdir)
```

Consumer's `_build_host()` reduces to one line: `host = make_host(ssh=config.ssh, taskdir=...)`.

**Removed from Host (becomes opencode-layer concern):**

The following methods are removed from the Host Protocol/impls. They move to
`optio_opencode.host_actions` as free functions, all taking `Host` as first
arg: `ensure_opencode_installed`, `install_opencode_binary`, `detect_target`,
`opencode_import`, `opencode_export`, `opencode_version`, `launch_opencode`,
`terminate_opencode`. See section 4.

`fetch_deliverable_text` → moves to `optio_host.protocol.session` as a free
function (thin wrapper over `fetch_bytes_from_host` + `.decode("utf-8")`).

**Removed types:**

- `LaunchedProcess` dataclass — deleted. Replaced by generic `ProcessHandle`.
  Its only opencode-specific field (`opencode_port`) is now plumbed back via
  `launch_opencode`'s tuple return type.

**Type relocations:**

- `RunResult`, `HostCommandError`, `HookContext`, `HookContextProtocol` —
  in `optio_host.context`.
- `SSHConfig` — in `optio_host.types`.

## Section 3 — `optio_host.protocol.session`

```python
async def run_log_protocol_session(
    host: Host,
    ctx: ProcessContext,
    *,
    body: Callable[[Host, HookContext], Awaitable[None]],
    on_deliverable: DeliverableCallback | None = None,
    before_execute: HookCallback | None = None,
    after_execute: HookCallback | None = None,
) -> None:
```

**Lifecycle:**

1. Build `hook_ctx = HookContext(ctx, host)`.
2. `host.setup_workdir()` (mkdir workdir).
3. Create `<workdir>/deliverables/` via `host.run_command("mkdir -p ...")`.
4. Create empty `<workdir>/optio.log` via `host.write_text("optio.log", "")`.
5. `await before_execute(hook_ctx)` if set.
6. Spawn three concurrent tasks:
   - `_tail_and_dispatch(host, ctx, queue, done_flag, error_flag)` — tails
     `<workdir>/optio.log` via `host.tail_file()`, runs `parse_log_line`,
     emits progress / queues deliverables / sets done/error flags.
   - `_deliverable_fetch_loop(host, on_deliverable, queue, ctx, hook_ctx)` —
     drains queue, fetches via `host.fetch_bytes_from_host(...).decode("utf-8")`,
     calls `on_deliverable(hook_ctx, display, text)`.
   - `body(host, hook_ctx)` — caller's work.
7. Await `{tail_task, body_task, cancel_task}` with `FIRST_COMPLETED`.
8. On `error_flag` set → raise `_SessionFailed`.
9. On body returning normally without `done_flag` → raise `_SessionFailed("body exited before DONE")`.
10. On cancel → mark cancelled, return cleanly.
11. Drain deliverable queue, cancel remaining watchers, gather with
    `return_exceptions=True`.
12. `await after_execute(hook_ctx)` if set, with current failure semantics:
    raises if session was healthy; logged via `report_progress` otherwise.

**Out of scope for the driver:**

- Workdir teardown / `host.cleanup_taskdir()` / `host.disconnect()` — caller
  owns. Caller may want to capture a snapshot first (opencode does).
- Subprocess termination — body owns its handles. Opencode body's caller
  (session.py) calls `host.terminate_subprocess(handle, aggressive=cancelled)`
  in its outer `finally`.
- Snapshot / resume — opencode session.py brackets around the call.

**Types relocated to this module:**

- `DeliverableCallback = Callable[[HookContext, str, str], Awaitable[None]]`
- `HookCallback = Callable[[HookContext], Awaitable[None]]`
- `DELIVERABLE_QUEUE_BOUND = 64`

**Free helper in same module:**

```python
async def fetch_deliverable_text(host: Host, absolute_path: str) -> str:
    return (await host.fetch_bytes_from_host(absolute_path)).decode("utf-8")
```

`_SessionFailed` (internal control-flow signal) — also in this module.

## Section 4 — `optio-opencode` after refactor

**File-level deltas:**

| Current path in optio-opencode | Action | New location |
|---|---|---|
| `host.py` (generic parts + new launch/terminate primitives) | move/extract | `optio_host.host` |
| `host.py` (opencode methods + state) | extract to free fns | `optio_opencode.host_actions` (new) |
| `hook_context.py` | move | `optio_host.context` |
| `archive.py` | move | `optio_host.archive` |
| `logparse.py` | move | `optio_host.protocol.parser` |
| `paths.py` | delete (lifted) | `optio_host.paths` |
| `prompt.py` | keep | unchanged |
| `snapshots.py` | keep | unchanged |
| `install.py` (target dataclass + helpers) | keep | unchanged |
| `types.py` `OpencodeTaskConfig` | keep | unchanged |
| `types.py` `SSHConfig` | move | `optio_host.types` |
| `types.py` `DeliverableCallback`, `HookCallback` | move | `optio_host.protocol.session` |
| `session.py` | rewrite, slimmer | `optio_opencode.session` |

**`optio_opencode/host_actions.py` (new) — free functions:**

```python
async def ensure_opencode_installed(host: Host, install_if_missing: bool) -> None:
    """Has internal isinstance(host, LocalHost) branch — local never installs;
    remote curl-installs if missing and install_if_missing=True."""

async def install_opencode_binary(
    host: Host, local_binary_path: str,
    progress: Callable[[int, int], None] | None = None,
) -> str:
    """Returns absolute path of installed binary on the host. Has internal
    isinstance(host, LocalHost) branch — local just returns local_binary_path
    verbatim; remote SFTP-uploads + chmod."""

async def detect_target(host: Host) -> OpencodeTarget:
    """Uniform via host.run_command('uname -s')/'-m', etc. Drops LocalHost's
    platform.system() micro-optimization in favor of uniform host-routed code."""

async def opencode_version(host: Host, *, opencode_executable: str = "opencode") -> str | None:
    """Uniform via host.run_command(f'{opencode_executable} --version')."""

async def opencode_import(
    host: Host, opencode_db_path: str, session_json: bytes,
    *, opencode_executable: str = "opencode",
) -> None:
    """Uniform: host.write_text(scratch, json) + host.run_command(import) + cleanup."""

async def opencode_export(
    host: Host, opencode_db_path: str, session_id: str,
    *, opencode_executable: str = "opencode",
) -> bytes:
    """Uniform: redirect-to-tmp + fetch_bytes_from_host + cleanup. Same anti-cancel-truncation
    pattern as today's RemoteHost.opencode_export, applied uniformly."""

async def launch_opencode(
    host: Host, password: str,
    *, ready_timeout_s: float = 30.0,
    opencode_executable: str = "opencode",
) -> tuple[ProcessHandle, int]:
    """Uniform via host.launch_subprocess. Writes password file, builds cmd
    that reads via $(cat password_file), iterates handle.stdout for ready URL,
    parses port. Returns (handle, opencode_port)."""

async def terminate_opencode(
    host: Host, handle: ProcessHandle, *, aggressive: bool,
) -> None:
    """Thin wrapper over host.terminate_subprocess(handle, aggressive=aggressive).
    Exists for naming symmetry; could be elided."""
```

**State plumbing.** `install_opencode_binary` returns the absolute install
path. `optio_opencode.session` captures it and threads it through subsequent
calls as `opencode_executable=...` to `launch_opencode`, `opencode_version`,
`opencode_import`, `opencode_export`. No host-side state mutation; explicit
data flow.

**`optio_opencode.session` (rewritten, ~300 LoC down from 688):**

Outer skeleton brackets the generic protocol session with opencode-specific
resume + snapshot handling:

```python
async def run_opencode_session(ctx: ProcessContext, config: OpencodeTaskConfig) -> None:
    taskdir = task_dir(ssh=config.ssh, process_id=ctx.process_id, consumer_name="optio-opencode")
    if config.ssh is None:
        os.makedirs(taskdir, exist_ok=True)
    host = make_host(ssh=config.ssh, taskdir=taskdir)
    opencode_db = f"{taskdir}/opencode.db"
    password = secrets.token_urlsafe(32)
    cancelled = False
    snapshot = None
    launched_handle: ProcessHandle | None = None
    opencode_exec = "opencode"
    session_id: str | None = None
    preserved_session_id: str | None = None

    # --- resume restore (around the body) ---
    if config.supports_resume and getattr(ctx, "resume", False):
        snapshot = await load_latest_snapshot(...)

    async def _opencode_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, opencode_exec, session_id, preserved_session_id

        # 1. resume restore (if applicable)
        # 2. write AGENTS.md, opencode.json, resume.log
        # 3. binary install: OPTIO_OPENCODE_BINARY_DIR path, or ensure_opencode_installed
        #    captures opencode_exec from install_opencode_binary's return
        # 4. launch + tunnel + widget
        handle, opencode_port = await host_actions.launch_opencode(
            host, password, ready_timeout_s=READY_TIMEOUT_S,
            opencode_executable=opencode_exec,
        )
        launched_handle = handle
        worker_port = await host.establish_tunnel(opencode_port)
        # ... pre-create session, set widget upstream + data ...
        # 5. await opencode subprocess exit OR done_flag (set by L1's tail dispatcher)

    try:
        await run_log_protocol_session(
            host, ctx,
            body=_opencode_body,
            on_deliverable=config.on_deliverable,
            before_execute=config.before_execute,
            after_execute=config.after_execute,
        )
    finally:
        if launched_handle is not None:
            try:
                await host.terminate_subprocess(launched_handle, aggressive=cancelled)
            except Exception:
                _LOG.exception("terminate_subprocess failed")
        if config.supports_resume and session_id is not None:
            try:
                await _capture_snapshot(...)
            except Exception:
                _LOG.exception("snapshot capture failed; proceeding with workdir wipe")
        try:
            await host.cleanup_taskdir(aggressive=cancelled)
        except Exception:
            _LOG.exception("cleanup_taskdir failed")
        try:
            await host.disconnect()
        except Exception:
            _LOG.exception("host.disconnect failed")
```

`_capture_snapshot` continues to use `host_actions.opencode_export(...)` +
`host.archive_workdir(...)`; the function moves to a private helper inside
session.py (or stays as today, just calls free fns instead of host methods).

`load_latest_snapshot` / `prune_snapshots` / `mark_has_saved_state` —
unchanged.

`compose_agents_md` — unchanged. Still knows about resume sections; that's
opencode-specific and stays.

**`optio_opencode/__init__.py` (back-compat re-exports):**

```python
"""optio-opencode — run opencode web as an optio task."""
import logging as _logging

from optio_host import (
    HookContext, HookContextProtocol, HostCommandError, RunResult, SSHConfig,
)
from optio_host.protocol import DeliverableCallback, HookCallback

from optio_opencode.session import create_opencode_task, run_opencode_session
from optio_opencode.types import OpencodeTaskConfig

_logging.getLogger("asyncssh").setLevel(_logging.WARNING)

__all__ = [
    "create_opencode_task",
    "run_opencode_session",
    "DeliverableCallback",
    "OpencodeTaskConfig",
    "SSHConfig",
    "HookContext",
    "HookContextProtocol",
    "HostCommandError",
    "RunResult",
    "HookCallback",
]
```

Excavator engine's existing imports (`from optio_opencode import HookContext, OpencodeTaskConfig, ...`) continue to resolve. No engine-side patches.

## Section 5 — Migration strategy

Phased, each phase ends green (full `make test-optio` passes; `make test-engine` passes for downstream verification).

**Phase 1 — Create `optio-host` package skeleton.**

- `~/deai/optio/packages/optio-host/pyproject.toml` mirroring optio-opencode's
  layout. Deps: asyncssh + bson (for any ObjectId types in HookContext;
  verify during impl). Drop motor unless protocol session needs it.
- `optio_host/__init__.py`, `optio_host/protocol/__init__.py` — empty.
- Add `optio-host` to optio's pnpm/uv workspace root. Add as dependency of
  `optio-opencode`'s pyproject.toml.
- No code yet. Tests dir empty.

**Phase 2 — Move generic primitives unchanged.**

- Move `hook_context.py` → `optio_host/context.py`.
- Move `archive.py` → `optio_host/archive.py`.
- Move `SSHConfig` → `optio_host/types.py`.
- Lift `paths.py` to `optio_host/paths.py` as new `task_dir(*, ssh, process_id, consumer_name)`.
  Opencode's call site updated to pass `consumer_name="optio-opencode"`.
  Env-var derivation rule: `consumer_name.upper().replace("-", "_") + "_TASK_ROOT"`
  (and `_REMOTE_TASK_ROOT`). Backwards-compatible for opencode's existing env vars.
- Move `Host` Protocol (generic methods only) + `LocalHost`/`RemoteHost`
  generic methods → `optio_host/host.py`. Opencode-named methods stay
  temporarily on the `LocalHost`/`RemoteHost` classes until phase 5.
- Add `make_host` factory.
- Rewrite imports in `optio_opencode/` (source + tests).
- Add re-exports in `optio_opencode/__init__.py` for back-compat.
- Run all optio-opencode tests + engine tests. Both green.

**Phase 3 — Add new subprocess primitives + rename existing.**

- Add `Host.launch_subprocess` + `Host.terminate_subprocess` to Protocol +
  LocalHost + RemoteHost. Implementation extracted from current
  `launch_opencode`/`terminate_opencode` bodies (subprocess machinery only —
  no opencode bits yet, just generic subprocess).
- Rename `tail_log()` → `tail_file(absolute_path)`. Caller-passed path. Update
  the one caller in opencode session.
- Rename `establish_tunnel`'s parameter `opencode_port` → `remote_port`.
- Slim `setup_workdir` to mkdir workdir only. Remove `deliverables/` +
  `optio.log` creation from inside; opencode session loop moves those to
  the L1 protocol's setup phase later in phase 4.
- Tests for new primitives — basic spawn-stream-terminate paths for both
  LocalHost and RemoteHost.
- All existing tests still pass.

**Phase 4 — Extract protocol session driver.**

- Move `logparse.py` → `optio_host/protocol/parser.py`. (Pure, no semantic
  change.)
- Move `DeliverableCallback`, `HookCallback`, `DELIVERABLE_QUEUE_BOUND`
  → `optio_host/protocol/session.py`.
- Implement `run_log_protocol_session(...)` in `optio_host/protocol/session.py`.
  Body lifted from `optio_opencode/session.py`'s `_tail_and_dispatch` +
  `_deliverable_fetch_loop` + the surrounding orchestration (workdir
  artifacts setup, before/after_execute calls, queue lifecycle).
- Add `fetch_deliverable_text(host, absolute_path)` free helper.
- Tests for the protocol driver — mock host, lifecycle paths, failure modes.
- Existing opencode session tests still pass (session.py still uses inlined
  loop until phase 6).

**Phase 5 — Extract opencode actions to free fns.**

- New `optio_opencode/host_actions.py` with the free functions.
- Each lifts from corresponding method on `LocalHost`/`RemoteHost`.
- For uniform actions (`detect_target`, `opencode_*`, `launch_opencode`):
  unified impl via `host.run_command` / `host.launch_subprocess`. No
  isinstance.
- For asymmetric actions (`ensure_opencode_installed`, `install_opencode_binary`):
  internal `isinstance(host, LocalHost)` branch (accepted leak — see leak
  audit).
- Remove the corresponding methods from `LocalHost`/`RemoteHost`. Remove the
  `_opencode_cmd` / `_opencode_exec` private fields from those classes.
- Tests updated: `host.X(...)` → `host_actions.X(host, ...)`.

**Phase 6 — Rewrite `optio_opencode.session`.**

- Replace the inlined session loop with a call to `run_log_protocol_session`.
- Plumb `opencode_executable` through `_opencode_body` after install.
- Capture launched `ProcessHandle` in outer scope; outer `finally` calls
  `host.terminate_subprocess(handle, aggressive=cancelled)`.
- Snapshot capture / resume restore stay as before. Update calls inside them
  to use `host_actions.opencode_export`/`opencode_import` instead of
  former Host methods.
- All tests pass.

**Phase 7 — Cleanup.**

- Delete `optio_opencode/hook_context.py`, `optio_opencode/archive.py`,
  `optio_opencode/logparse.py`, `optio_opencode/paths.py`.
- Verify back-compat re-exports cover all public symbols.
- Verify excavator engine test suite passes (no engine-side patches required).
- Verify optio-opencode test suite passes (unit + local integration + remote
  integration).

## Local/remote leak audit

After this spec:

| Source of distinction | Status |
|---|---|
| `_build_host` instance choice | Hidden in `optio_host.make_host()` factory. session.py no longer names LocalHost/RemoteHost. |
| `opencode_db` path computation | Uniform `f"{taskdir}/opencode.db"`. |
| Path helpers | Single `task_dir(*, ssh, process_id, consumer_name)` in optio-host. |
| `ensure_opencode_installed` | Free fn in `host_actions` with internal `isinstance(host, LocalHost)` branch. **Accepted leak** — local never installs; remote curl-installs. |
| `install_opencode_binary` | Free fn with internal isinstance branch. **Accepted leak** — local stores path verbatim; remote SFTP-uploads. |
| All other host actions (`detect_target`, `opencode_*`, `launch_opencode`) | Uniform via `host.run_command` / `host.launch_subprocess`. No isinstance. |

Two functions in `optio_opencode/host_actions.py` carry isinstance ladders.
The leak is contained inside the opencode layer (free fns inside opencode);
optio-host has zero opencode awareness. Future work to fully eliminate
(via subclassing or capability flags on Host) is out of scope until a
second consumer's needs justify the change.

## Section 6 — Testing

**`optio-host/tests/` (new):**

- `test_host_local.py` — generic LocalHost primitives. Lifted from current
  `optio-opencode/tests/test_host_local.py` minus opencode-specific bits.
- `test_host_remote.py` — same for RemoteHost. Continues to use
  `linuxserver/openssh-server` Docker fixture.
- `test_paths.py` — verify env-var derivation, fallback chain, validate.
- `test_archive.py` — generic workdir tar. Lifted.
- `test_context.py` — `HookContext`, path resolver, `RunResult`,
  `HostCommandError`. Lifted.
- `test_protocol_parser.py` — parser. Lifted from `test_logparse.py`.
- `test_protocol_session.py` — `run_log_protocol_session` with mock host.
  New. Verify lifecycle, failure modes (ERROR event, body exit without DONE,
  cancel), back-pressure on bounded queue.

**`optio-opencode/tests/` (slim):**

- Keeps tests for: `host_actions.launch_opencode` (with mock
  `launch_subprocess`), tunnel + widget data, snapshot capture/restore,
  resume restore, `host_actions.install_opencode_binary` (both branches),
  `host_actions.ensure_opencode_installed` (both branches),
  `host_actions.opencode_export`/`opencode_import`, AGENTS.md composition
  (resume sections), session integration end-to-end with `fake_opencode.py`
  test double.
- Deletes tests fully migrated to optio-host.

**Test-double migration.**

- `fake_opencode.py` — opencode-side test double. Stays in
  `optio-opencode/tests/`.
- Mock host for protocol-session tests in optio-host: simple in-memory
  implementation. New ~80 LoC fixture.

**`fake_opencode` plumbing.** Today `LocalHost.__init__(taskdir, opencode_cmd=[...])`
allows substituting a fake binary. After refactor LocalHost loses
`opencode_cmd`. Tests substitute via `host_actions.launch_opencode(...,
opencode_executable=...)` instead — pass the absolute path of
`fake_opencode.py`. Equivalent behavior.

**CI / make targets.**

- `make test-optio-host` — new.
- `make test-optio-opencode` — existing, leaner.
- `make test-optio` — aggregate. Updates to invoke both.

**Coverage gates.** Each migration phase ends with `make test-optio` green.
Strict gate before next phase merges.

**No new integration env required.** Same Docker openssh-server fixture as
today; same Mongo via Docker for snapshot tests.

## Section 7 — Public API & back-compat

**`optio-host` — new public API.**

Top-level imports:

```python
from optio_host import (
    Host, LocalHost, RemoteHost, ProcessHandle, make_host,
    HookContext, HookContextProtocol, RunResult, HostCommandError,
    SSHConfig,
)
from optio_host.paths import task_dir
from optio_host.protocol import (
    run_log_protocol_session,
    DeliverableCallback, HookCallback,
    parse_log_line,
    StatusEvent, DeliverableEvent, DoneEvent, ErrorEvent, UnknownLine,
    validate_deliverable_path, relativize_deliverable_path,
    fetch_deliverable_text,
)
```

Submodule paths also valid for granular imports.

**`optio-opencode` — back-compat surface.**

`optio_opencode/__init__.py` re-exports moved symbols from `optio_host` so
existing downstream imports keep working. See section 4 for the contents.

Excavator engine's existing imports unchanged. No engine-side patches.

**Internal-only opencode surface (not in `__all__`):**

Available via submodule import only:

- `optio_opencode.host_actions` — opencode action free fns.
- `optio_opencode.snapshots` — snapshot collection helpers.
- `optio_opencode.prompt` — `compose_agents_md`.
- `optio_opencode.install` — `OpencodeTarget`, `make_target`,
  `normalize_os`, `normalize_arch`.

**Deprecation path.** None needed. Re-exports remain canonical for opencode
consumers. New consumers (recipe-execution) bypass `optio_opencode` and
import from `optio_host` directly.

**SemVer.** Both packages bump minor versions in their pyproject.toml. No
major bump because public API preserved.

## Section 8 — Risks & verification

**Risk 1 — Subprocess primitive shape gets locked in wrong.**

Mitigation: kw-only args from day one. Add fields to `ProcessHandle` over
time without breaking. Concrete decision: stdout merged with stderr (caller
appends `2>&1` via cmd if wanted) — matches current opencode behavior.

**Risk 2 — Tests cross-package brittle during phased migration.**

Mitigation: each phase's commit runs full `make test-optio` in CI before
next phase merges. No phase shipped unless green.

**Risk 3 — Engine-side test suite needs a re-pass.**

Excavator engine imports from `optio_opencode`. Re-exports preserve these.
But engine tests depend on transitively-installed packages — `optio-host`
must be in engine's test venv.

Mitigation: After phase 1 (optio-host package shell), update engine's
`pyproject.toml` to add `optio-host` as transitive dependency (auto-pulled
via optio-opencode → optio-host). Verify engine venv resolves cleanly.

**Risk 4 — `_opencode_cmd` / `_opencode_exec` state migration breaks tests.**

Currently `LocalHost.__init__` accepts `opencode_cmd` for fake-binary
substitution. After Spec A, LocalHost is generic; this constructor arg
disappears. Tests using `LocalHost(taskdir, opencode_cmd=[...])` break.

Mitigation: substitute via `host_actions.launch_opencode(host, ...,
opencode_executable=fake_path)`. Equivalent test surface.

**Verification before merge.**

1. `make test-optio` passes.
2. `make test-engine` passes — confirms re-export contract.
3. Manual: launch one local opencode session, verify load + chat + deliverable
   + clean termination.
4. Manual: launch one remote opencode session via SSH integration fixture; same
   checks.
5. Resume sanity: launch session, kill mid-run, click Resume, verify
   restoration. Snapshot+resume code untouched in this spec — should be
   invariant.

## Open questions

None at the time of writing — see "Out of scope" for what's deliberately
deferred.
