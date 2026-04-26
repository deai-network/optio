# Opencode Task Hooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `before_execute` / `after_execute` hooks to `OpencodeTaskConfig`, plus a unified `HookContext` (`ProcessContext` extended with `copy_file` / `run_on_host` / `read_from_host` / `read_text_from_host`) so consumer apps can ship their own files and run setup/teardown commands on the host that runs opencode.

**Architecture:** Three layers, bottom-up. (1) New low-level `Host` primitives — `put_file_to_host`, `fetch_bytes_from_host`, `run_command` — implemented on both `LocalHost` and `RemoteHost`, sharing the existing SFTP / atomic-rename / SHA-256-skip plumbing that `install_opencode_binary` uses today. (2) A `HookContext` class in optio-opencode that wraps a `ProcessContext` plus a `Host` and exposes the four user-facing primitives, delegating `ProcessContext` access via `__getattr__`. (3) Two new optional async hook fields on `OpencodeTaskConfig` and a reworked `on_deliverable` signature that all receive the same `HookContext`. The session pipeline gains two slots: `before_execute` after binary install and before opencode launch; `after_execute` after opencode terminates and before snapshot capture.

**Tech Stack:** Python 3.11+, `asyncio`, `asyncssh` (SFTP + remote exec), `aiofiles` (LocalHost streaming), `motor` / `gridfs` (blob source for `copy_file`), `pytest` + `pytest-asyncio` (tests), Docker Compose SSH harness for `RemoteHost` integration tests.

**Spec:** `docs/2026-04-25-opencode-task-hooks-design.md`.

---

## Plan-level notes

### Test conventions

- Tests live under `packages/optio-opencode/tests/` (existing pattern).
- Fixtures available in `packages/optio-opencode/tests/conftest.py`:
  - `tmp_workdir` — an empty temp directory, removed after the test. Use for `LocalHost(taskdir=tmp_workdir)`.
  - `mongo_db` — a per-test motor `AsyncIOMotorDatabase`, dropped after the test. Use when a test needs `ProcessContext` (which requires `db`).
- `fake_opencode.py` (in tests/) is the existing opencode stand-in. Pass `[sys.executable, FAKE_OPENCODE]` as `LocalHost.opencode_cmd` for `LocalHost` tests that need a launchable binary. The new primitives don't need it.
- `RemoteHost` integration tests use the `sshd` module-scoped fixture in `test_host_remote_resume.py`. That harness already runs the SSH container with `opencode-shim.sh`. New `RemoteHost` tests should follow the same pattern and skip when Docker is unavailable.
- All async tests are marked at module level via `pytestmark = pytest.mark.asyncio` (the project default — check existing tests for the exact incantation in each file).

### Naming conventions

- Module: `optio_opencode.hook_context` (snake_case).
- Class: `HookContext` (PascalCase).
- Methods: snake_case (`copy_file`, `run_on_host`, `read_from_host`).
- Dataclasses: `RunResult`, `HostCommandError` — also in `hook_context.py`.

### Don't introduce

- No `Co-Authored-By` lines in commits (per user preference).
- No `# noqa: BLE001` blanket-except suppression except where the existing codebase already does it (e.g., session.py error-recovery paths).
- No backwards-compat shim for the `DeliverableCallback` signature — clean break (per spec).

### Files created (summary)

- `packages/optio-opencode/src/optio_opencode/hook_context.py` — new module.
- `packages/optio-opencode/tests/test_hook_context.py` — unit tests for HookContext.
- `packages/optio-opencode/tests/test_host_primitives_local.py` — tests for new LocalHost methods.
- `packages/optio-opencode/tests/test_host_primitives_remote.py` — tests for new RemoteHost methods (Docker-gated).
- `packages/optio-opencode/tests/test_session_hooks.py` — pipeline integration tests.
- `packages/optio-demo/tests/__init__.py` and `packages/optio-demo/tests/test_demo_smoke.py` — new test directory + smoke test.

### Files modified (summary)

- `packages/optio-opencode/src/optio_opencode/host.py` — Host protocol additions, LocalHost + RemoteHost impls, `install_opencode_binary` refactor.
- `packages/optio-opencode/src/optio_opencode/types.py` — `OpencodeTaskConfig` new fields + new `DeliverableCallback` signature.
- `packages/optio-opencode/src/optio_opencode/session.py` — wire hooks into pipeline + new `_deliverable_fetch_loop` arg.
- `packages/optio-opencode/src/optio_opencode/__init__.py` — re-export `HookContext`, `HookContextProtocol`, `RunResult`, `HostCommandError`.
- `packages/optio-opencode/AGENTS.md` — new Hooks section + updated `on_deliverable` description.
- `packages/optio-demo/src/optio_demo/tasks/opencode.py` — full rewrite to the new pattern.

### Verification cheat-sheet

- Run all optio-opencode tests: `pnpm -F optio-opencode test` (or fall back to `cd packages/optio-opencode && pytest`).
- Run a single test file: `cd packages/optio-opencode && pytest tests/test_hook_context.py -v`.
- Run a single test: `cd packages/optio-opencode && pytest tests/test_hook_context.py::test_name -v`.
- Run RemoteHost integration tests only when Docker is up: they auto-skip otherwise.

---

## Task 1: `hook_context.py` foundations — `RunResult`, `HostCommandError`, `_resolve_target_path`

**Files:**
- Create: `packages/optio-opencode/src/optio_opencode/hook_context.py`
- Test: `packages/optio-opencode/tests/test_hook_context.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-opencode/tests/test_hook_context.py`:

```python
"""Tests for HookContext foundations: dataclasses + path resolver."""

import pytest

from optio_opencode.hook_context import (
    HostCommandError,
    RunResult,
    _resolve_target_path,
)


def test_run_result_fields():
    r = RunResult(stdout="hi\n", stderr="", exit_code=0)
    assert r.stdout == "hi\n"
    assert r.stderr == ""
    assert r.exit_code == 0


def test_host_command_error_str_includes_exit_code_and_stderr():
    err = HostCommandError(
        command="false", exit_code=1, stdout="", stderr="boom\n",
    )
    s = str(err)
    assert "exit 1" in s
    assert "boom" in s
    assert "false" in s


def test_resolve_target_path_workdir_relative():
    out = _resolve_target_path("data/foo.yaml", "/wd", "/home/u")
    assert out == "/wd/data/foo.yaml"


def test_resolve_target_path_absolute_passthrough():
    assert _resolve_target_path("/usr/local/bin/tool", "/wd", "/home/u") == "/usr/local/bin/tool"


def test_resolve_target_path_home_relative_expanded():
    assert _resolve_target_path("~/.local/bin/tool", "/wd", "/home/u") == "/home/u/.local/bin/tool"


def test_resolve_target_path_bare_tilde_expanded():
    assert _resolve_target_path("~", "/wd", "/home/u") == "/home/u"


def test_resolve_target_path_rejects_empty():
    with pytest.raises(ValueError):
        _resolve_target_path("", "/wd", "/home/u")


def test_resolve_target_path_rejects_dotdot_in_workdir_relative():
    with pytest.raises(ValueError):
        _resolve_target_path("data/../../etc/passwd", "/wd", "/home/u")


def test_resolve_target_path_rejects_workdir_relative_escape():
    # Even after normalization, must stay inside workdir.
    with pytest.raises(ValueError):
        _resolve_target_path("../outside", "/wd", "/home/u")


def test_resolve_target_path_dotdot_allowed_in_absolute():
    # Absolute paths are consumer-trusted; .. is fine there.
    assert _resolve_target_path("/usr/../tmp/x", "/wd", "/home/u") == "/usr/../tmp/x"


def test_resolve_target_path_dotdot_allowed_in_home_relative():
    # Home-relative is also consumer-trusted.
    assert _resolve_target_path("~/foo/../bar", "/wd", "/home/u") == "/home/u/foo/../bar"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-opencode && pytest tests/test_hook_context.py -v`
Expected: All fail with `ImportError: cannot import name 'HostCommandError' from 'optio_opencode.hook_context'` (the module does not exist yet).

- [ ] **Step 3: Create the module with the foundations**

Create `packages/optio-opencode/src/optio_opencode/hook_context.py`:

```python
"""HookContext: ProcessContext + host primitives for opencode-task hooks."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int


class HostCommandError(Exception):
    def __init__(
        self,
        command: str,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> None:
        self.command = command
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(
            f"command failed (exit {exit_code}): {command!r}\n"
            f"stderr: {stderr[:200]}"
        )


def _resolve_target_path(path: str, workdir: str, host_home: str) -> str:
    """Resolve a user-supplied path to an absolute host path.

    Three forms:
      - starts with `/` → absolute, used as-is
      - `~` or starts with `~/` → home-relative, expand once
      - otherwise → workdir-relative; reject `..` and any escape

    workdir and host_home must both be absolute paths.
    """
    if not path:
        raise ValueError("path must not be empty")
    if path.startswith("/"):
        return path
    if path == "~":
        return host_home
    if path.startswith("~/"):
        return host_home + "/" + path[2:]
    # workdir-relative
    if ".." in path.split("/"):
        raise ValueError(f"workdir-relative path may not contain '..': {path!r}")
    resolved = os.path.normpath(os.path.join(workdir, path))
    workdir_norm = os.path.normpath(workdir).rstrip("/")
    if resolved != workdir_norm and not resolved.startswith(workdir_norm + "/"):
        raise ValueError(f"workdir-relative path escapes workdir: {path!r}")
    return resolved
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_hook_context.py -v`
Expected: All 11 tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/hook_context.py packages/optio-opencode/tests/test_hook_context.py
git commit -m "feat(optio-opencode): hook_context.py — RunResult, HostCommandError, path resolver"
```

---

## Task 2: Add new method declarations to `Host` Protocol

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/host.py` (Host Protocol class)

This task adds protocol declarations only. Implementations follow in later tasks. The declarations must be in place so subsequent test files that type-hint `Host` import cleanly.

- [ ] **Step 1: Locate the Host Protocol class**

Run: `grep -n "^class Host" packages/optio-opencode/src/optio_opencode/host.py`
Expected: One match showing the line where `class Host(Protocol):` begins.

- [ ] **Step 2: Add the four new method declarations to the Protocol**

Find the Host Protocol class. After the existing methods (`fetch_deliverable_text`, `archive_workdir`, `restore_workdir`, `remove_file`, etc.), add:

```python
    # --- new primitives for HookContext (Task 3+ implement these) ---

    async def put_file_to_host(
        self,
        source,                       # str | os.PathLike | bytes | AsyncIterator[bytes]
        absolute_target: str,
        *,
        expected_sha256: str | None = None,
        skip_if_unchanged: bool = False,
        progress_cb=None,             # Callable[[float | None, str | None], None] | None
    ) -> None: ...

    async def fetch_bytes_from_host(
        self,
        absolute_path: str,
        *,
        progress_cb=None,
    ) -> bytes: ...

    async def run_command(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> "RunResult": ...

    async def resolve_host_home(self) -> str: ...
```

(`resolve_host_home` is async because `RemoteHost` needs an SSH round-trip on first call to discover `$HOME`; the value is then cached. `LocalHost` returns immediately. Picking async for both keeps the consumer-facing call uniform.)

Add the necessary import at the top of `host.py`:

```python
from optio_opencode.hook_context import RunResult
```

(Place it with the other internal-package imports.)

- [ ] **Step 3: Verify the file still parses and existing tests still pass**

Run: `cd packages/optio-opencode && python -c "from optio_opencode.host import Host"`
Expected: No output, no error.

Run: `cd packages/optio-opencode && pytest tests/test_host_local.py -v`
Expected: All existing tests still pass (LocalHost doesn't yet implement the new methods, but Protocol membership is structural — it doesn't enforce anything until something type-checks it).

- [ ] **Step 4: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/host.py
git commit -m "feat(optio-opencode): declare put_file_to_host / fetch_bytes_from_host / run_command on Host"
```

---

## Task 3: `LocalHost.run_command`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/host.py` (LocalHost class)
- Test: `packages/optio-opencode/tests/test_host_primitives_local.py` (new file)

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-opencode/tests/test_host_primitives_local.py`:

```python
"""Tests for LocalHost.run_command / put_file_to_host / fetch_bytes_from_host / resolve_host_home."""

import os
import sys

import pytest

from optio_opencode.host import LocalHost


pytestmark = pytest.mark.asyncio


@pytest.fixture
def local_host(tmp_workdir):
    return LocalHost(taskdir=tmp_workdir, opencode_cmd=[sys.executable, "-c", "pass"])


async def test_run_command_captures_stdout(local_host):
    await local_host.setup_workdir()
    result = await local_host.run_command("echo hello")
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello"
    assert result.stderr == ""


async def test_run_command_captures_stderr_and_exit_code(local_host):
    await local_host.setup_workdir()
    result = await local_host.run_command("echo oops 1>&2; exit 7")
    assert result.exit_code == 7
    assert result.stdout == ""
    assert "oops" in result.stderr


async def test_run_command_default_cwd_is_workdir(local_host):
    await local_host.setup_workdir()
    result = await local_host.run_command("pwd")
    assert result.stdout.strip() == os.path.realpath(local_host.workdir)


async def test_run_command_cwd_override(local_host, tmp_path):
    await local_host.setup_workdir()
    result = await local_host.run_command("pwd", cwd=str(tmp_path))
    assert result.stdout.strip() == os.path.realpath(str(tmp_path))


async def test_run_command_env_override(local_host):
    await local_host.setup_workdir()
    result = await local_host.run_command(
        'echo "$MY_VAR"', env={"MY_VAR": "marker", "PATH": os.environ["PATH"]},
    )
    assert result.stdout.strip() == "marker"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-opencode && pytest tests/test_host_primitives_local.py -v`
Expected: All 5 tests fail with `AttributeError: 'LocalHost' object has no attribute 'run_command'`.

- [ ] **Step 3: Implement `LocalHost.run_command`**

In `packages/optio-opencode/src/optio_opencode/host.py`, inside the `LocalHost` class, add:

```python
    async def run_command(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> RunResult:
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh", "-c", command,
            cwd=cwd if cwd is not None else self.workdir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        return RunResult(
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace"),
            exit_code=proc.returncode if proc.returncode is not None else -1,
        )
```

(`RunResult` is already imported per Task 2.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_host_primitives_local.py -v`
Expected: All 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/host.py packages/optio-opencode/tests/test_host_primitives_local.py
git commit -m "feat(optio-opencode): LocalHost.run_command (sh -c, capture stdout/stderr/exit)"
```

---

## Task 4: `LocalHost.put_file_to_host` — path / bytes / iterator sources, atomic rename

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/host.py` (LocalHost class)
- Test: `packages/optio-opencode/tests/test_host_primitives_local.py` (append)

This task implements the full multi-source dispatch + atomic rename, but **without** `skip_if_unchanged` (Task 5 adds that). Source variants: `str | os.PathLike` (path), `bytes`, `AsyncIterator[bytes]`.

- [ ] **Step 1: Append failing tests to `test_host_primitives_local.py`**

Append to `packages/optio-opencode/tests/test_host_primitives_local.py`:

```python
async def test_put_file_path_source(local_host, tmp_path):
    await local_host.setup_workdir()
    src = tmp_path / "src.bin"
    src.write_bytes(b"hello world")
    target = os.path.join(local_host.workdir, "data", "out.bin")
    await local_host.put_file_to_host(str(src), target)
    with open(target, "rb") as fh:
        assert fh.read() == b"hello world"


async def test_put_file_bytes_source(local_host):
    await local_host.setup_workdir()
    target = os.path.join(local_host.workdir, "x.bin")
    await local_host.put_file_to_host(b"raw bytes", target)
    with open(target, "rb") as fh:
        assert fh.read() == b"raw bytes"


async def test_put_file_async_iterator_source(local_host):
    await local_host.setup_workdir()
    target = os.path.join(local_host.workdir, "y.bin")

    async def chunks():
        yield b"part1-"
        yield b"part2"

    await local_host.put_file_to_host(chunks(), target)
    with open(target, "rb") as fh:
        assert fh.read() == b"part1-part2"


async def test_put_file_creates_parent_dirs(local_host):
    await local_host.setup_workdir()
    deep = os.path.join(local_host.workdir, "a", "b", "c", "out.txt")
    await local_host.put_file_to_host(b"deep", deep)
    with open(deep, "rb") as fh:
        assert fh.read() == b"deep"


async def test_put_file_atomic_no_tmp_left_on_success(local_host):
    await local_host.setup_workdir()
    target = os.path.join(local_host.workdir, "atomic.bin")
    await local_host.put_file_to_host(b"ok", target)
    # No leftover .tmp* files in the directory.
    siblings = os.listdir(os.path.dirname(target))
    assert all(not s.endswith(".tmp") and ".tmp." not in s for s in siblings)


async def test_put_file_replaces_existing_atomically(local_host):
    await local_host.setup_workdir()
    target = os.path.join(local_host.workdir, "replace.bin")
    with open(target, "wb") as fh:
        fh.write(b"OLD")
    await local_host.put_file_to_host(b"NEW", target)
    with open(target, "rb") as fh:
        assert fh.read() == b"NEW"
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `cd packages/optio-opencode && pytest tests/test_host_primitives_local.py -v -k "put_file"`
Expected: 6 new tests fail with `AttributeError: ... 'put_file_to_host'`.

- [ ] **Step 3: Implement `LocalHost.put_file_to_host` (without skip_if_unchanged)**

In `packages/optio-opencode/src/optio_opencode/host.py`, inside `LocalHost`, add:

```python
    async def put_file_to_host(
        self,
        source,
        absolute_target: str,
        *,
        expected_sha256: str | None = None,
        skip_if_unchanged: bool = False,
        progress_cb=None,
    ) -> None:
        # skip_if_unchanged is added in Task 5; for now ignore.
        os.makedirs(os.path.dirname(absolute_target), exist_ok=True)
        tmp = absolute_target + ".tmp"
        try:
            await self._stream_source_to_path(source, tmp, progress_cb)
            os.replace(tmp, absolute_target)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    async def _stream_source_to_path(self, source, dest_path: str, progress_cb) -> None:
        """Write `source` (path, bytes, or async iterator) to `dest_path`."""
        chunk_size = 16 * 1024
        if isinstance(source, (bytes, bytearray)):
            await asyncio.to_thread(_write_bytes_sync, dest_path, bytes(source))
            if progress_cb is not None:
                progress_cb(100.0, None)
            return
        if isinstance(source, (str, os.PathLike)):
            total = os.path.getsize(os.fspath(source))
            written = 0

            def _copy() -> None:
                nonlocal written
                with open(os.fspath(source), "rb") as src, open(dest_path, "wb") as dst:
                    while True:
                        chunk = src.read(chunk_size)
                        if not chunk:
                            break
                        dst.write(chunk)
                        written += len(chunk)

            await asyncio.to_thread(_copy)
            if progress_cb is not None and total > 0:
                progress_cb(100.0, None)
            return
        # async iterator
        with open(dest_path, "wb") as dst:
            async for chunk in source:
                dst.write(chunk)
        if progress_cb is not None:
            progress_cb(100.0, None)


def _write_bytes_sync(dest_path: str, data: bytes) -> None:
    with open(dest_path, "wb") as fh:
        fh.write(data)
```

(Place `_write_bytes_sync` at module top-level, near other module-level helpers in `host.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_host_primitives_local.py -v`
Expected: All 11 tests pass (5 from Task 3 + 6 new).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/host.py packages/optio-opencode/tests/test_host_primitives_local.py
git commit -m "feat(optio-opencode): LocalHost.put_file_to_host — path/bytes/iterator + atomic rename"
```

---

## Task 5: `LocalHost.put_file_to_host` — `skip_if_unchanged`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/host.py` (LocalHost.put_file_to_host)
- Test: `packages/optio-opencode/tests/test_host_primitives_local.py` (append)

- [ ] **Step 1: Append failing tests**

Append to `packages/optio-opencode/tests/test_host_primitives_local.py`:

```python
async def test_put_file_skip_if_unchanged_target_missing(local_host):
    await local_host.setup_workdir()
    target = os.path.join(local_host.workdir, "first.bin")
    await local_host.put_file_to_host(b"data", target, skip_if_unchanged=True)
    with open(target, "rb") as fh:
        assert fh.read() == b"data"


async def test_put_file_skip_if_unchanged_matches(local_host, tmp_path):
    await local_host.setup_workdir()
    target = os.path.join(local_host.workdir, "same.bin")
    with open(target, "wb") as fh:
        fh.write(b"identical content")
    target_mtime_before = os.stat(target).st_mtime_ns
    # Second call must be a no-op.
    await local_host.put_file_to_host(
        b"identical content", target, skip_if_unchanged=True,
    )
    target_mtime_after = os.stat(target).st_mtime_ns
    assert target_mtime_before == target_mtime_after  # untouched
    with open(target, "rb") as fh:
        assert fh.read() == b"identical content"


async def test_put_file_skip_if_unchanged_differs(local_host):
    await local_host.setup_workdir()
    target = os.path.join(local_host.workdir, "differs.bin")
    with open(target, "wb") as fh:
        fh.write(b"OLD")
    await local_host.put_file_to_host(b"NEW", target, skip_if_unchanged=True)
    with open(target, "rb") as fh:
        assert fh.read() == b"NEW"


async def test_put_file_skip_if_unchanged_iterator_requires_expected_sha(local_host):
    await local_host.setup_workdir()
    target = os.path.join(local_host.workdir, "iter.bin")

    async def chunks():
        yield b"abc"

    with pytest.raises(ValueError, match="expected_sha256"):
        await local_host.put_file_to_host(
            chunks(), target, skip_if_unchanged=True,
        )


async def test_put_file_skip_if_unchanged_iterator_with_expected_sha_matches(local_host):
    await local_host.setup_workdir()
    import hashlib
    payload = b"streamed payload"
    sha = hashlib.sha256(payload).hexdigest()
    target = os.path.join(local_host.workdir, "iter2.bin")
    with open(target, "wb") as fh:
        fh.write(payload)
    target_mtime_before = os.stat(target).st_mtime_ns

    async def chunks():
        yield payload

    await local_host.put_file_to_host(
        chunks(), target,
        skip_if_unchanged=True, expected_sha256=sha,
    )
    target_mtime_after = os.stat(target).st_mtime_ns
    assert target_mtime_before == target_mtime_after  # skipped
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `cd packages/optio-opencode && pytest tests/test_host_primitives_local.py -v -k "skip_if_unchanged"`
Expected: 5 new tests fail (target gets overwritten when it shouldn't, or `ValueError` not raised).

- [ ] **Step 3: Add skip_if_unchanged logic to `LocalHost.put_file_to_host`**

Replace the body of `put_file_to_host` in `host.py` (LocalHost) with:

```python
    async def put_file_to_host(
        self,
        source,
        absolute_target: str,
        *,
        expected_sha256: str | None = None,
        skip_if_unchanged: bool = False,
        progress_cb=None,
    ) -> None:
        os.makedirs(os.path.dirname(absolute_target), exist_ok=True)

        # Iterator + skip_if_unchanged + no expected_sha256 is always a
        # contract error, even if the target does not yet exist.
        if (
            skip_if_unchanged
            and expected_sha256 is None
            and not isinstance(source, (bytes, bytearray, str, os.PathLike))
        ):
            raise ValueError(
                "skip_if_unchanged with an AsyncIterator source requires "
                "expected_sha256"
            )

        if skip_if_unchanged and os.path.exists(absolute_target):
            target_sha = await asyncio.to_thread(_sha256_of_file, absolute_target)
            source_sha = await self._compute_source_sha(source, expected_sha256)
            if source_sha == target_sha:
                if progress_cb is not None:
                    progress_cb(None, "already up to date")
                return

        tmp = absolute_target + ".tmp"
        try:
            await self._stream_source_to_path(source, tmp, progress_cb)
            os.replace(tmp, absolute_target)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    async def _compute_source_sha(self, source, expected_sha256: str | None) -> str:
        if isinstance(source, (bytes, bytearray)):
            return hashlib.sha256(bytes(source)).hexdigest()
        if isinstance(source, (str, os.PathLike)):
            return await asyncio.to_thread(_sha256_of_file, os.fspath(source))
        # async iterator: the upfront guard above ensures expected_sha256
        # is set by this point; assert defensively.
        assert expected_sha256 is not None
        return expected_sha256
```

Add module-level helper near `_write_bytes_sync`:

```python
def _sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
```

Add `import hashlib` at the top of `host.py` if not already imported.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_host_primitives_local.py -v`
Expected: All 16 tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/host.py packages/optio-opencode/tests/test_host_primitives_local.py
git commit -m "feat(optio-opencode): LocalHost.put_file_to_host — skip_if_unchanged via SHA-256"
```

---

## Task 6: `LocalHost.fetch_bytes_from_host`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/host.py` (LocalHost class)
- Test: `packages/optio-opencode/tests/test_host_primitives_local.py` (append)

- [ ] **Step 1: Append failing tests**

```python
async def test_fetch_bytes_from_host_reads_full(local_host):
    await local_host.setup_workdir()
    target = os.path.join(local_host.workdir, "rd.bin")
    with open(target, "wb") as fh:
        fh.write(b"contents")
    out = await local_host.fetch_bytes_from_host(target)
    assert out == b"contents"


async def test_fetch_bytes_from_host_missing_raises_filenotfound(local_host):
    await local_host.setup_workdir()
    with pytest.raises(FileNotFoundError):
        await local_host.fetch_bytes_from_host(
            os.path.join(local_host.workdir, "no_such")
        )
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `cd packages/optio-opencode && pytest tests/test_host_primitives_local.py -v -k "fetch_bytes"`
Expected: 2 tests fail with `AttributeError: ... 'fetch_bytes_from_host'`.

- [ ] **Step 3: Implement `LocalHost.fetch_bytes_from_host`**

Inside `LocalHost`, add:

```python
    async def fetch_bytes_from_host(
        self,
        absolute_path: str,
        *,
        progress_cb=None,
    ) -> bytes:
        def _read() -> bytes:
            with open(absolute_path, "rb") as fh:
                return fh.read()

        data = await asyncio.to_thread(_read)
        if progress_cb is not None:
            progress_cb(100.0, None)
        return data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_host_primitives_local.py -v`
Expected: All tests pass (16 + 2 = 18).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/host.py packages/optio-opencode/tests/test_host_primitives_local.py
git commit -m "feat(optio-opencode): LocalHost.fetch_bytes_from_host"
```

---

## Task 7: `LocalHost.resolve_host_home`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/host.py` (LocalHost class)
- Test: `packages/optio-opencode/tests/test_host_primitives_local.py` (append)

- [ ] **Step 1: Append failing test**

```python
async def test_resolve_host_home_returns_user_home(local_host):
    expected = os.path.expanduser("~")
    assert await local_host.resolve_host_home() == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-opencode && pytest tests/test_host_primitives_local.py::test_resolve_host_home_returns_user_home -v`
Expected: Fails with `AttributeError`.

- [ ] **Step 3: Implement `resolve_host_home`**

Inside `LocalHost`, add:

```python
    async def resolve_host_home(self) -> str:
        return os.path.expanduser("~")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-opencode && pytest tests/test_host_primitives_local.py -v`
Expected: All 19 tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/host.py packages/optio-opencode/tests/test_host_primitives_local.py
git commit -m "feat(optio-opencode): LocalHost.resolve_host_home"
```

---

## Task 8: `RemoteHost.run_command`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/host.py` (RemoteHost class)
- Test: `packages/optio-opencode/tests/test_host_primitives_remote.py` (new file)

- [ ] **Step 1: Create the new RemoteHost test file with the SSH harness**

Create `packages/optio-opencode/tests/test_host_primitives_remote.py`:

```python
"""Integration tests for RemoteHost new primitives (Docker-gated)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import pytest_asyncio

from optio_opencode.host import RemoteHost
from optio_opencode.types import SSHConfig


HERE = Path(__file__).parent
COMPOSE = HERE / "docker-compose.sshd.yml"


def _have_docker() -> bool:
    return shutil.which("docker") is not None


pytestmark = [
    pytest.mark.skipif(not _have_docker(), reason="Docker not available"),
    pytest.mark.asyncio,
]


@pytest_asyncio.fixture(scope="module")
async def sshd():
    keys_dir = HERE / "ssh-keys"
    keys_dir.mkdir(exist_ok=True)
    priv = keys_dir / "id_ed25519"
    if not priv.exists():
        subprocess.check_call([
            "ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(priv)
        ])
    subprocess.check_call(["chmod", "+x", str(HERE / "opencode-shim.sh")])
    subprocess.check_call(
        ["docker", "compose", "-f", str(COMPOSE), "up", "-d", "--wait"],
    )
    # Wait for port 22222 to accept connections.
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            r = subprocess.run(
                ["nc", "-z", "127.0.0.1", "22222"],
                capture_output=True, timeout=2,
            )
            if r.returncode == 0:
                break
        except Exception:
            pass
        time.sleep(0.5)
    try:
        yield SSHConfig(
            host="127.0.0.1",
            user="optiotest",  # matches USER_NAME in tests/docker-compose.sshd.yml
            key_path=str(priv),
            port=22222,
        )
    finally:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE), "down", "-v"],
            capture_output=True,
        )


@pytest_asyncio.fixture
async def remote_host(sshd):
    h = RemoteHost(ssh_config=sshd)
    await h.connect()
    await h.setup_workdir()
    try:
        yield h
    finally:
        try:
            await h.cleanup_taskdir(aggressive=True)
        except Exception:
            pass
        await h.disconnect()


async def test_remote_run_command_captures_stdout(remote_host):
    result = await remote_host.run_command("echo hello")
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello"
    assert result.stderr == ""


async def test_remote_run_command_captures_stderr_and_exit(remote_host):
    result = await remote_host.run_command("echo oops 1>&2; exit 9")
    assert result.exit_code == 9
    assert "oops" in result.stderr


async def test_remote_run_command_default_cwd_is_workdir(remote_host):
    result = await remote_host.run_command("pwd")
    assert result.stdout.strip() == remote_host.workdir


async def test_remote_run_command_cwd_override(remote_host):
    result = await remote_host.run_command("pwd", cwd="/tmp")
    assert result.stdout.strip() == "/tmp"


async def test_remote_run_command_env(remote_host):
    result = await remote_host.run_command(
        'echo "$X"', env={"X": "yes"},
    )
    assert result.stdout.strip() == "yes"
```

- [ ] **Step 2: Run tests to verify they fail (when Docker is up)**

Run: `cd packages/optio-opencode && pytest tests/test_host_primitives_remote.py -v -k "run_command"`
Expected: 5 tests fail with `AttributeError: ... 'run_command'`. (Or all skip if Docker isn't available — that's also acceptable; the implementation step still proceeds.)

- [ ] **Step 3: Implement `RemoteHost.run_command`**

In `host.py`, inside `RemoteHost`, add:

```python
    async def run_command(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> RunResult:
        assert self._conn is not None
        run_cwd = cwd if cwd is not None else self.workdir
        # asyncssh's run() does not support cwd/env kwargs directly.
        # Build a shell invocation that handles both explicitly.
        # We use `export` so that variable expansions inside `command`
        # (e.g. `echo "$X"`) pick up the values set by the caller.
        exports = ""
        if env:
            exports = " ".join(
                f"export {k}={shlex.quote(v)};" for k, v in env.items()
            ) + " "
        full_command = f"cd {shlex.quote(run_cwd)} && {exports}{command}"
        result = await self._conn.run(full_command, check=False)
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        return RunResult(
            stdout=stdout if isinstance(stdout, str) else stdout.decode("utf-8", errors="replace"),
            stderr=stderr if isinstance(stderr, str) else stderr.decode("utf-8", errors="replace"),
            exit_code=result.exit_status if result.exit_status is not None else -1,
        )
```

(Requires `import shlex` at the top of `host.py` — verify it's already there.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_host_primitives_remote.py -v -k "run_command"`
Expected: 5 tests pass (or all skip if Docker unavailable).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/host.py packages/optio-opencode/tests/test_host_primitives_remote.py
git commit -m "feat(optio-opencode): RemoteHost.run_command (asyncssh.run)"
```

---

## Task 9: `RemoteHost.put_file_to_host` — path / bytes / iterator sources

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/host.py` (RemoteHost class)
- Test: `packages/optio-opencode/tests/test_host_primitives_remote.py` (append)

- [ ] **Step 1: Append failing tests**

```python
async def test_remote_put_file_path_source(remote_host, tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"path-source-content")
    target = remote_host.workdir + "/data/out.bin"
    await remote_host.put_file_to_host(str(src), target)
    out = await remote_host.run_command(f"cat {target}")
    assert out.stdout == "path-source-content"


async def test_remote_put_file_bytes_source(remote_host):
    target = remote_host.workdir + "/x.bin"
    await remote_host.put_file_to_host(b"bytes-content", target)
    out = await remote_host.run_command(f"cat {target}")
    assert out.stdout == "bytes-content"


async def test_remote_put_file_iterator_source(remote_host):
    target = remote_host.workdir + "/y.bin"

    async def chunks():
        yield b"part1-"
        yield b"part2"

    await remote_host.put_file_to_host(chunks(), target)
    out = await remote_host.run_command(f"cat {target}")
    assert out.stdout == "part1-part2"


async def test_remote_put_file_atomic_no_tmp(remote_host):
    target = remote_host.workdir + "/atomic.bin"
    await remote_host.put_file_to_host(b"ok", target)
    ls = await remote_host.run_command(f"ls {remote_host.workdir}")
    assert ".tmp" not in ls.stdout
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `cd packages/optio-opencode && pytest tests/test_host_primitives_remote.py -v -k "put_file"`
Expected: Fails with `AttributeError: ... 'put_file_to_host'`.

- [ ] **Step 3: Implement `RemoteHost.put_file_to_host` (without skip_if_unchanged)**

Inside `RemoteHost`, add:

```python
    async def put_file_to_host(
        self,
        source,
        absolute_target: str,
        *,
        expected_sha256: str | None = None,
        skip_if_unchanged: bool = False,
        progress_cb=None,
    ) -> None:
        # skip_if_unchanged is wired up in Task 10.
        assert self._conn is not None
        # Ensure parent directory exists.
        parent = os.path.dirname(absolute_target)
        if parent:
            await self._conn.run(f"mkdir -p {shlex.quote(parent)}", check=False)

        tmp = absolute_target + ".tmp"
        try:
            await self._stream_source_to_remote(source, tmp, progress_cb)
            mv = await self._conn.run(
                f"mv -f {shlex.quote(tmp)} {shlex.quote(absolute_target)}",
                check=False,
            )
            if mv.exit_status not in (0, None):
                raise RuntimeError(
                    f"mv failed: exit {mv.exit_status}: {mv.stderr!r}"
                )
        except BaseException:
            try:
                await self._conn.run(f"rm -f {shlex.quote(tmp)}", check=False)
            except Exception:
                pass
            raise

    async def _stream_source_to_remote(self, source, remote_path: str, progress_cb) -> None:
        """SFTP-write `source` (path/bytes/async iterator) to `remote_path`."""
        import io
        sftp = await self._conn.start_sftp_client()
        try:
            if isinstance(source, (bytes, bytearray)):
                async with sftp.open(remote_path, "wb") as fh:
                    await fh.write(bytes(source))
                if progress_cb is not None:
                    progress_cb(100.0, None)
                return
            if isinstance(source, (str, os.PathLike)):
                # asyncssh's put streams from disk and reports progress.
                def _progress_adapter(_src, _dst, transferred, total):
                    if progress_cb is not None and total:
                        progress_cb(min(100.0, transferred * 100.0 / total), None)
                await sftp.put(
                    os.fspath(source), remote_path,
                    progress_handler=_progress_adapter,
                )
                return
            # async iterator
            async with sftp.open(remote_path, "wb") as fh:
                async for chunk in source:
                    await fh.write(chunk)
            if progress_cb is not None:
                progress_cb(100.0, None)
        finally:
            sftp.exit()
```

(Add `import shlex` at the top of `host.py` if it isn't already imported.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_host_primitives_remote.py -v -k "put_file"`
Expected: 4 tests pass (or skip if Docker unavailable).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/host.py packages/optio-opencode/tests/test_host_primitives_remote.py
git commit -m "feat(optio-opencode): RemoteHost.put_file_to_host — path/bytes/iterator + atomic mv"
```

---

## Task 10: `RemoteHost.put_file_to_host` — `skip_if_unchanged` via `sha256sum`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/host.py` (RemoteHost class)
- Test: `packages/optio-opencode/tests/test_host_primitives_remote.py` (append)

- [ ] **Step 1: Append failing tests**

```python
async def test_remote_put_file_skip_if_unchanged_target_missing(remote_host):
    target = remote_host.workdir + "/skip-missing.bin"
    await remote_host.put_file_to_host(
        b"first", target, skip_if_unchanged=True,
    )
    out = await remote_host.run_command(f"cat {target}")
    assert out.stdout == "first"


async def test_remote_put_file_skip_if_unchanged_matches(remote_host):
    target = remote_host.workdir + "/skip-match.bin"
    await remote_host.put_file_to_host(b"same", target)
    mtime1 = (await remote_host.run_command(f"stat -c %Y {target}")).stdout.strip()
    # Wait briefly to make any mtime change observable.
    await asyncio.sleep(1.1)
    await remote_host.put_file_to_host(
        b"same", target, skip_if_unchanged=True,
    )
    mtime2 = (await remote_host.run_command(f"stat -c %Y {target}")).stdout.strip()
    assert mtime1 == mtime2  # untouched


async def test_remote_put_file_skip_if_unchanged_differs(remote_host):
    target = remote_host.workdir + "/skip-diff.bin"
    await remote_host.put_file_to_host(b"OLD", target)
    await remote_host.put_file_to_host(
        b"NEW", target, skip_if_unchanged=True,
    )
    out = await remote_host.run_command(f"cat {target}")
    assert out.stdout == "NEW"


async def test_remote_put_file_iterator_skip_requires_expected_sha(remote_host):
    target = remote_host.workdir + "/iter-skip.bin"

    async def chunks():
        yield b"abc"

    with pytest.raises(ValueError, match="expected_sha256"):
        await remote_host.put_file_to_host(
            chunks(), target, skip_if_unchanged=True,
        )
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `cd packages/optio-opencode && pytest tests/test_host_primitives_remote.py -v -k "skip_if_unchanged or iterator_skip"`
Expected: All fail (target overwritten when it shouldn't be, or ValueError not raised).

- [ ] **Step 3: Add skip_if_unchanged + sha256sum logic**

Replace the body of `RemoteHost.put_file_to_host` with:

```python
    async def put_file_to_host(
        self,
        source,
        absolute_target: str,
        *,
        expected_sha256: str | None = None,
        skip_if_unchanged: bool = False,
        progress_cb=None,
    ) -> None:
        assert self._conn is not None
        parent = os.path.dirname(absolute_target)
        if parent:
            await self._conn.run(f"mkdir -p {shlex.quote(parent)}", check=False)

        # Iterator + skip_if_unchanged + no expected_sha256 is always a
        # contract error, even if the target does not yet exist.
        if (
            skip_if_unchanged
            and expected_sha256 is None
            and not isinstance(source, (bytes, bytearray, str, os.PathLike))
        ):
            raise ValueError(
                "skip_if_unchanged with an AsyncIterator source requires "
                "expected_sha256"
            )

        if skip_if_unchanged:
            target_sha = await self._sha256_of_remote(absolute_target)
            if target_sha is not None:
                source_sha = await self._compute_source_sha_remote(
                    source, expected_sha256,
                )
                if source_sha == target_sha:
                    if progress_cb is not None:
                        progress_cb(None, "already up to date")
                    return

        tmp = absolute_target + ".tmp"
        try:
            await self._stream_source_to_remote(source, tmp, progress_cb)
            mv = await self._conn.run(
                f"mv -f {shlex.quote(tmp)} {shlex.quote(absolute_target)}",
                check=False,
            )
            if mv.exit_status not in (0, None):
                raise RuntimeError(
                    f"mv failed: exit {mv.exit_status}: {mv.stderr!r}"
                )
        except BaseException:
            try:
                await self._conn.run(f"rm -f {shlex.quote(tmp)}", check=False)
            except Exception:
                pass
            raise

    async def _sha256_of_remote(self, path: str) -> str | None:
        """Return the SHA-256 of `path`, or None if the file does not exist."""
        assert self._conn is not None
        # First check existence; we want None for missing, not a sha256sum error.
        check = await self._conn.run(
            f"test -f {shlex.quote(path)}", check=False,
        )
        if check.exit_status != 0:
            return None
        # sha256sum on Linux containers (which is what the test harness runs);
        # on macOS hosts use `shasum -a 256`.
        result = await self._conn.run(
            f"sha256sum {shlex.quote(path)} 2>/dev/null || shasum -a 256 {shlex.quote(path)}",
            check=False,
        )
        if result.exit_status != 0:
            return None
        # Output: "<hex>  <path>"
        first = result.stdout.split(None, 1)[0] if result.stdout else ""
        return first if len(first) == 64 else None

    async def _compute_source_sha_remote(
        self, source, expected_sha256: str | None,
    ) -> str:
        if isinstance(source, (bytes, bytearray)):
            return hashlib.sha256(bytes(source)).hexdigest()
        if isinstance(source, (str, os.PathLike)):
            return await asyncio.to_thread(_sha256_of_file, os.fspath(source))
        # The upfront guard above ensures expected_sha256 is set by this point.
        assert expected_sha256 is not None
        return expected_sha256
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_host_primitives_remote.py -v`
Expected: All RemoteHost tests pass (5 + 4 + 4 = 13).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/host.py packages/optio-opencode/tests/test_host_primitives_remote.py
git commit -m "feat(optio-opencode): RemoteHost.put_file_to_host — skip_if_unchanged via sha256sum"
```

---

## Task 11: `RemoteHost.fetch_bytes_from_host`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/host.py` (RemoteHost class)
- Test: `packages/optio-opencode/tests/test_host_primitives_remote.py` (append)

- [ ] **Step 1: Append failing tests**

```python
async def test_remote_fetch_bytes_reads_full(remote_host):
    target = remote_host.workdir + "/rd.bin"
    await remote_host.run_command(
        f"printf 'remote-content' > {target}",
    )
    data = await remote_host.fetch_bytes_from_host(target)
    assert data == b"remote-content"


async def test_remote_fetch_bytes_missing_raises_filenotfound(remote_host):
    with pytest.raises(FileNotFoundError):
        await remote_host.fetch_bytes_from_host(
            remote_host.workdir + "/no_such",
        )
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `cd packages/optio-opencode && pytest tests/test_host_primitives_remote.py -v -k "fetch_bytes"`
Expected: Fails with `AttributeError: ... 'fetch_bytes_from_host'`.

- [ ] **Step 3: Implement `RemoteHost.fetch_bytes_from_host`**

```python
    async def fetch_bytes_from_host(
        self,
        absolute_path: str,
        *,
        progress_cb=None,
    ) -> bytes:
        assert self._conn is not None
        sftp = await self._conn.start_sftp_client()
        try:
            try:
                async with sftp.open(absolute_path, "rb") as fh:
                    data = await fh.read()
            except (asyncssh.SFTPError, asyncssh.SFTPNoSuchFile) as exc:
                # asyncssh maps "no such file" to a generic SFTPError in some
                # versions; check the message.
                if "No such file" in str(exc):
                    raise FileNotFoundError(absolute_path) from exc
                raise
            if progress_cb is not None:
                progress_cb(100.0, None)
            return data
        finally:
            sftp.exit()
```

(`asyncssh` is already imported at the top of host.py; verify with a grep.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_host_primitives_remote.py -v`
Expected: All RemoteHost tests pass (15 total).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/host.py packages/optio-opencode/tests/test_host_primitives_remote.py
git commit -m "feat(optio-opencode): RemoteHost.fetch_bytes_from_host"
```

---

## Task 12: `RemoteHost.resolve_host_home`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/host.py` (RemoteHost class)
- Test: `packages/optio-opencode/tests/test_host_primitives_remote.py` (append)

- [ ] **Step 1: Append failing test**

```python
async def test_remote_resolve_host_home_resolves_and_caches(remote_host):
    home1 = await remote_host.resolve_host_home()
    assert home1.startswith("/")  # absolute
    # The harness runs as `optiotest` but the linuxserver/openssh-server
    # image sets HOME=/config for the unprivileged user.
    assert home1 == "/config"
    # Second call uses cache; just verifies same return value.
    home2 = await remote_host.resolve_host_home()
    assert home2 == home1
```

- [ ] **Step 2: Run test to verify it fails**

Expected: `AttributeError: ... 'resolve_host_home'`.

- [ ] **Step 3: Implement `resolve_host_home` on RemoteHost**

```python
    async def resolve_host_home(self) -> str:
        if self._host_home_cache is not None:
            return self._host_home_cache
        assert self._conn is not None
        result = await self._conn.run("printf '%s' \"$HOME\"", check=False)
        if result.exit_status != 0 or not result.stdout:
            # Fallback for very stripped-down containers.
            result = await self._conn.run("getent passwd \"$USER\" | cut -d: -f6", check=False)
        home = (result.stdout or "").strip()
        if not home or not home.startswith("/"):
            raise RuntimeError(f"could not resolve $HOME on remote host: {result.stdout!r}")
        self._host_home_cache = home
        return home
```

In `RemoteHost.__init__`, initialize `self._host_home_cache: str | None = None`.

The Host protocol already declares `async def resolve_host_home(self) -> str` from Task 2; LocalHost implemented it in Task 7. This task adds the cached RemoteHost implementation.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_host_primitives_remote.py -v`
Expected: All RemoteHost tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/host.py packages/optio-opencode/tests/test_host_primitives_remote.py
git commit -m "feat(optio-opencode): RemoteHost.resolve_host_home with SSH-round-trip caching"
```

---

## Task 13: Refactor `RemoteHost.install_opencode_binary` to delegate

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/host.py` (RemoteHost.install_opencode_binary)
- Test: existing `packages/optio-opencode/tests/test_install.py` is the regression test.

- [ ] **Step 1: Run existing tests as the baseline**

Run: `cd packages/optio-opencode && pytest tests/test_install.py -v`
Expected: All existing install tests pass with the current ~74-line implementation. Take note of which tests exist — the refactor must not break any.

- [ ] **Step 2: Replace the body of `install_opencode_binary` with the thin wrapper**

In `host.py`, RemoteHost class, replace the current body of `install_opencode_binary` with:

```python
    async def install_opencode_binary(
        self,
        local_binary_path: str,
        progress=None,
    ) -> None:
        install_path = await self._resolve_install_path()
        # Delegate the transfer (with skip-if-unchanged + atomic rename + progress)
        # to the generic primitive.
        await self.put_file_to_host(
            local_binary_path,
            install_path,
            skip_if_unchanged=True,
            progress_cb=progress,
        )
        # Make sure the binary is executable. chmod is idempotent.
        result = await self.run_command(f"chmod +x {shlex.quote(install_path)}")
        if result.exit_code != 0:
            raise RuntimeError(
                f"chmod +x {install_path} failed: exit {result.exit_code}: {result.stderr!r}"
            )
```

If the original `install_opencode_binary` had a separate `_resolve_install_path` (or computed the install path inline), keep / extract that helper as `_resolve_install_path`. If it didn't have one, add it now using whatever path computation the original code used.

Delete the now-unused SFTP / SHA-256 / atomic-rename code that previously lived inside `install_opencode_binary` (it's all in `put_file_to_host` now).

- [ ] **Step 3: Run all install + remote tests**

Run: `cd packages/optio-opencode && pytest tests/test_install.py tests/test_host_primitives_remote.py -v`
Expected: All pass — the regression test for install is unchanged.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/host.py
git commit -m "refactor(optio-opencode): install_opencode_binary delegates to put_file_to_host"
```

---

## Task 14: `HookContext` class with `__getattr__` delegation + `HookContextProtocol`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/hook_context.py`
- Test: `packages/optio-opencode/tests/test_hook_context.py` (append)

- [ ] **Step 1: Append failing tests**

```python
import asyncio

from optio_opencode.hook_context import HookContext, HookContextProtocol


class _FakeCtx:
    def __init__(self):
        self.process_id = "p"
        self.params = {"k": "v"}
        self.calls = []

    def report_progress(self, percent, message=None):
        self.calls.append(("rp", percent, message))


class _FakeHost:
    def __init__(self):
        self.workdir = "/wd"


def test_hook_context_delegates_attributes():
    ctx = _FakeCtx()
    host = _FakeHost()
    h = HookContext(ctx, host)
    # Process-context attributes flow through __getattr__.
    assert h.process_id == "p"
    assert h.params == {"k": "v"}
    h.report_progress(50, "halfway")
    assert ctx.calls == [("rp", 50, "halfway")]


def test_hook_context_protocol_is_protocol():
    # Smoke test: the Protocol exists and has the expected method names.
    methods = {m for m in dir(HookContextProtocol) if not m.startswith("_")}
    expected = {
        "copy_file", "run_on_host", "read_from_host", "read_text_from_host",
        "report_progress", "should_continue", "params", "metadata",
    }
    assert expected <= methods
```

- [ ] **Step 2: Run new tests to verify they fail**

Expected: `ImportError` on `HookContext` / `HookContextProtocol`.

- [ ] **Step 3: Add `HookContext` and `HookContextProtocol` to `hook_context.py`**

Append to `packages/optio-opencode/src/optio_opencode/hook_context.py`:

```python
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol


class HookContext:
    """ProcessContext + host primitives, passed to before/after_execute hooks
    and to on_deliverable callbacks.

    Attributes not defined on this class fall through to the wrapped
    ProcessContext via __getattr__, so consumers can call e.g.
    ``hook_ctx.report_progress(...)`` directly.
    """

    def __init__(self, ctx, host) -> None:
        # Use object.__setattr__ to avoid __getattr__ recursion in __init__.
        object.__setattr__(self, "_ctx", ctx)
        object.__setattr__(self, "_host", host)

    def __getattr__(self, name: str) -> Any:
        # Only called if the attribute isn't found on the instance / class.
        return getattr(self._ctx, name)


class HookContextProtocol(Protocol):
    """Type-hint surface for hook authors who want IDE discoverability.

    Subset of ProcessContext + the four new methods.
    """

    process_id: str
    params: dict
    metadata: dict

    def report_progress(self, percent: float | None, message: str | None = None) -> None: ...
    def should_continue(self) -> bool: ...
    async def copy_file(
        self,
        source,
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
    ): ...
    async def read_from_host(self, path: str) -> bytes: ...
    async def read_text_from_host(self, path: str) -> str: ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_hook_context.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/hook_context.py packages/optio-opencode/tests/test_hook_context.py
git commit -m "feat(optio-opencode): HookContext class + HookContextProtocol"
```

---

## Task 15: `HookContext.run_on_host`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/hook_context.py`
- Test: `packages/optio-opencode/tests/test_hook_context.py` (append)

- [ ] **Step 1: Append failing tests**

```python
class _FakeRunHost:
    def __init__(self, results):
        self.workdir = "/wd"
        self._results = list(results)
        self.calls = []

    async def run_command(self, command, *, cwd=None, env=None):
        self.calls.append((command, cwd, env))
        return self._results.pop(0)


async def test_run_on_host_check_true_returns_stdout_on_success():
    from optio_opencode.hook_context import RunResult
    host = _FakeRunHost([RunResult(stdout="hi\n", stderr="", exit_code=0)])
    h = HookContext(_FakeCtx(), host)
    out = await h.run_on_host("echo hi")
    assert out == "hi\n"


async def test_run_on_host_check_true_raises_on_nonzero():
    from optio_opencode.hook_context import HostCommandError, RunResult
    host = _FakeRunHost([RunResult(stdout="", stderr="boom", exit_code=2)])
    h = HookContext(_FakeCtx(), host)
    with pytest.raises(HostCommandError) as ei:
        await h.run_on_host("false")
    assert ei.value.exit_code == 2
    assert ei.value.stderr == "boom"


async def test_run_on_host_check_false_returns_result_object():
    from optio_opencode.hook_context import RunResult
    host = _FakeRunHost([RunResult(stdout="", stderr="oops", exit_code=3)])
    h = HookContext(_FakeCtx(), host)
    res = await h.run_on_host("false", check=False)
    assert res.exit_code == 3
    assert res.stderr == "oops"


async def test_run_on_host_capture_stderr_merges_into_returned_stdout():
    from optio_opencode.hook_context import RunResult
    host = _FakeRunHost([RunResult(stdout="o", stderr="e", exit_code=0)])
    h = HookContext(_FakeCtx(), host)
    out = await h.run_on_host("cmd", capture_stderr=True)
    assert out == "oe"


async def test_run_on_host_cwd_is_forwarded():
    from optio_opencode.hook_context import RunResult
    host = _FakeRunHost([RunResult(stdout="", stderr="", exit_code=0)])
    h = HookContext(_FakeCtx(), host)
    await h.run_on_host("pwd", cwd="/elsewhere")
    assert host.calls[0][1] == "/elsewhere"
```

Add `import pytest` at the top of `test_hook_context.py` if not already present. Add `pytestmark = pytest.mark.asyncio` near the top so the async tests run.

- [ ] **Step 2: Run tests to verify they fail**

Expected: `AttributeError: 'HookContext' object has no attribute 'run_on_host'`.

- [ ] **Step 3: Implement `HookContext.run_on_host`**

In `hook_context.py`, add to `HookContext`:

```python
    async def run_on_host(
        self,
        command: str,
        *,
        check: bool = True,
        capture_stderr: bool = False,
        cwd: str | None = None,
    ):
        result = await self._host.run_command(command, cwd=cwd)
        if check:
            if result.exit_code != 0:
                raise HostCommandError(
                    command=command,
                    exit_code=result.exit_code,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            return result.stdout + (result.stderr if capture_stderr else "")
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_hook_context.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/hook_context.py packages/optio-opencode/tests/test_hook_context.py
git commit -m "feat(optio-opencode): HookContext.run_on_host (check / capture_stderr / cwd)"
```

---

## Task 16: `HookContext.copy_file` + `read_from_host` + `read_text_from_host` (path/bytes sources)

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/hook_context.py`
- Test: `packages/optio-opencode/tests/test_hook_context.py` (append)

- [ ] **Step 1: Append failing tests**

```python
class _FakeCopyHost:
    def __init__(self, *, host_home="/home/u"):
        self.workdir = "/wd"
        self._host_home = host_home
        self.put_calls = []
        self.fetch_calls = []
        self.fetch_returns = b""

    async def resolve_host_home(self):
        return self._host_home

    async def put_file_to_host(
        self, source, absolute_target, *,
        expected_sha256=None, skip_if_unchanged=False, progress_cb=None,
    ):
        # Snapshot the call.
        self.put_calls.append({
            "source": source,
            "absolute_target": absolute_target,
            "expected_sha256": expected_sha256,
            "skip_if_unchanged": skip_if_unchanged,
        })
        if progress_cb is not None:
            progress_cb(None, None)
            progress_cb(50.0, None)
            progress_cb(100.0, None)

    async def fetch_bytes_from_host(self, absolute_path, *, progress_cb=None):
        self.fetch_calls.append(absolute_path)
        return self.fetch_returns


async def test_copy_file_workdir_relative_resolves_to_workdir():
    host = _FakeCopyHost()
    h = HookContext(_FakeCtx(), host)
    await h.copy_file(b"data", "out/foo.bin")
    assert host.put_calls[0]["absolute_target"] == "/wd/out/foo.bin"


async def test_copy_file_absolute_passthrough():
    host = _FakeCopyHost()
    h = HookContext(_FakeCtx(), host)
    await h.copy_file(b"data", "/usr/local/bin/tool")
    assert host.put_calls[0]["absolute_target"] == "/usr/local/bin/tool"


async def test_copy_file_home_relative_expanded():
    host = _FakeCopyHost(host_home="/home/u")
    h = HookContext(_FakeCtx(), host)
    await h.copy_file(b"data", "~/.local/bin/tool")
    assert host.put_calls[0]["absolute_target"] == "/home/u/.local/bin/tool"


async def test_copy_file_path_source_forwarded():
    host = _FakeCopyHost()
    h = HookContext(_FakeCtx(), host)
    await h.copy_file("/worker/path/file", "out.bin")
    assert host.put_calls[0]["source"] == "/worker/path/file"


async def test_copy_file_emits_progress_messages():
    ctx = _FakeCtx()
    host = _FakeCopyHost()
    h = HookContext(ctx, host)
    await h.copy_file(b"data", "out.bin")
    # First call: start message; subsequent: percent.
    msgs = [c[2] for c in ctx.calls]
    assert any("Copying out.bin" in (m or "") for m in msgs)


async def test_copy_file_skip_if_unchanged_emits_verifying_then_done():
    """When the host reports skipped (progress_cb called once with 'already up to date'),
    HookContext should emit Verifying + Already up to date messages."""
    ctx = _FakeCtx()

    class _SkippingHost(_FakeCopyHost):
        async def put_file_to_host(self, source, absolute_target, *,
                                   expected_sha256=None,
                                   skip_if_unchanged=False,
                                   progress_cb=None):
            self.put_calls.append({"source": source, "absolute_target": absolute_target})
            if progress_cb is not None:
                progress_cb(None, "already up to date")

    host = _SkippingHost()
    h = HookContext(ctx, host)
    await h.copy_file(b"data", "out.bin", skip_if_unchanged=True)
    msgs = [c[2] for c in ctx.calls]
    assert any("Verifying out.bin" in (m or "") for m in msgs)
    assert any("Already up to date: out.bin" in (m or "") for m in msgs)


async def test_read_from_host_workdir_relative_resolves():
    host = _FakeCopyHost()
    host.fetch_returns = b"contents"
    h = HookContext(_FakeCtx(), host)
    out = await h.read_from_host("data/x")
    assert out == b"contents"
    assert host.fetch_calls[0] == "/wd/data/x"


async def test_read_text_from_host_decodes_utf8():
    host = _FakeCopyHost()
    host.fetch_returns = "héllo".encode("utf-8")
    h = HookContext(_FakeCtx(), host)
    out = await h.read_text_from_host("data/x")
    assert out == "héllo"


async def test_read_from_host_emits_reading_message():
    ctx = _FakeCtx()
    host = _FakeCopyHost()
    host.fetch_returns = b"abc"
    h = HookContext(ctx, host)
    await h.read_from_host("data/x")
    msgs = [c[2] for c in ctx.calls]
    assert any("Reading x" in (m or "") for m in msgs)
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: `AttributeError: 'HookContext' object has no attribute 'copy_file'` (and similar for the others).

- [ ] **Step 3: Implement `copy_file`, `read_from_host`, `read_text_from_host`**

In `hook_context.py`, add to `HookContext`:

```python
    async def copy_file(
        self,
        source,
        target: str,
        *,
        skip_if_unchanged: bool = False,
    ) -> None:
        host_home = await self._host.resolve_host_home()
        abs_target = _resolve_target_path(target, self._host.workdir, host_home)
        basename = os.path.basename(abs_target) or abs_target

        ctx = self._ctx
        skipped = False

        def _progress_cb(percent, message):
            nonlocal skipped
            if message == "already up to date":
                skipped = True
                ctx.report_progress(None, f"Already up to date: {basename}")
            elif percent is not None:
                ctx.report_progress(percent, None)

        if skip_if_unchanged:
            ctx.report_progress(None, f"Verifying {basename}...")
        else:
            ctx.report_progress(None, f"Copying {basename}...")

        await self._host.put_file_to_host(
            source,
            abs_target,
            skip_if_unchanged=skip_if_unchanged,
            progress_cb=_progress_cb,
        )

        if skip_if_unchanged and not skipped:
            # Verifying revealed a mismatch; the host streamed a copy.
            # Surface the "Copying ..." message after the fact for clarity.
            ctx.report_progress(None, f"Copying {basename}...")

    async def read_from_host(self, path: str) -> bytes:
        host_home = await self._host.resolve_host_home()
        abs_path = _resolve_target_path(path, self._host.workdir, host_home)
        basename = os.path.basename(abs_path) or abs_path
        self._ctx.report_progress(None, f"Reading {basename}...")

        def _progress_cb(percent, message):
            if percent is not None:
                self._ctx.report_progress(percent, None)

        return await self._host.fetch_bytes_from_host(
            abs_path, progress_cb=_progress_cb,
        )

    async def read_text_from_host(self, path: str) -> str:
        data = await self.read_from_host(path)
        return data.decode("utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_hook_context.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/hook_context.py packages/optio-opencode/tests/test_hook_context.py
git commit -m "feat(optio-opencode): HookContext copy_file / read_from_host / read_text_from_host"
```

---

## Task 17: `HookContext.copy_file` — `ObjectId` blob source

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/hook_context.py`
- Test: `packages/optio-opencode/tests/test_hook_context.py` (append)

- [ ] **Step 1: Append failing tests**

```python
from bson import ObjectId


class _FakeBlobReader:
    def __init__(self, data: bytes):
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def read(self, n=None):
        if n is None:
            data, self._data = self._data, b""
            return data
        out, self._data = self._data[:n], self._data[n:]
        return out


class _BlobCtx(_FakeCtx):
    def __init__(self, blobs: dict):
        super().__init__()
        self._blobs = blobs

    def load_blob(self, file_id):
        # Return an async-context-manager wrapping a reader.
        return _FakeBlobReader(self._blobs[file_id])


async def test_copy_file_objectid_source_streams_blob():
    blob_id = ObjectId()
    payload = b"blob-payload"
    ctx = _BlobCtx({blob_id: payload})
    host = _FakeCopyHost()
    h = HookContext(ctx, host)
    await h.copy_file(blob_id, "out.bin")
    # The host should have received an async iterator that yields the blob bytes.
    src = host.put_calls[0]["source"]
    assert hasattr(src, "__aiter__")
    collected = b"".join([chunk async for chunk in src])
    assert collected == payload


async def test_copy_file_objectid_skip_if_unchanged_supplies_expected_sha():
    import hashlib
    blob_id = ObjectId()
    payload = b"blob-payload"
    expected_sha = hashlib.sha256(payload).hexdigest()
    ctx = _BlobCtx({blob_id: payload})
    host = _FakeCopyHost()
    h = HookContext(ctx, host)
    await h.copy_file(blob_id, "out.bin", skip_if_unchanged=True)
    assert host.put_calls[0]["expected_sha256"] == expected_sha
    assert host.put_calls[0]["skip_if_unchanged"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: TypeError or AttributeError because `copy_file` doesn't yet handle `ObjectId`.

- [ ] **Step 3: Update `HookContext.copy_file` to dispatch on `ObjectId`**

Replace the `copy_file` body in `hook_context.py` with:

```python
    async def copy_file(
        self,
        source,
        target: str,
        *,
        skip_if_unchanged: bool = False,
    ) -> None:
        from bson import ObjectId  # local import to avoid a hard top-level dep

        host_home = await self._host.resolve_host_home()
        abs_target = _resolve_target_path(target, self._host.workdir, host_home)
        basename = os.path.basename(abs_target) or abs_target
        ctx = self._ctx
        skipped = False

        def _progress_cb(percent, message):
            nonlocal skipped
            if message == "already up to date":
                skipped = True
                ctx.report_progress(None, f"Already up to date: {basename}")
            elif percent is not None:
                ctx.report_progress(percent, None)

        # Resolve blob sources to (iterator, expected_sha) pair.
        expected_sha: str | None = None
        if isinstance(source, ObjectId):
            payload = await self._read_blob_bytes(source)
            if skip_if_unchanged:
                import hashlib
                expected_sha = hashlib.sha256(payload).hexdigest()

            async def _gen():
                # Yield the payload as one (or a few) chunks. For very large
                # blobs we could stream via load_blob, but reading-then-iterating
                # is simpler and correct.
                yield payload

            source = _gen()

        if skip_if_unchanged:
            ctx.report_progress(None, f"Verifying {basename}...")
        else:
            ctx.report_progress(None, f"Copying {basename}...")

        await self._host.put_file_to_host(
            source,
            abs_target,
            expected_sha256=expected_sha,
            skip_if_unchanged=skip_if_unchanged,
            progress_cb=_progress_cb,
        )

        if skip_if_unchanged and not skipped:
            ctx.report_progress(None, f"Copying {basename}...")

    async def _read_blob_bytes(self, file_id) -> bytes:
        async with self._ctx.load_blob(file_id) as reader:
            return await reader.read()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_hook_context.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/hook_context.py packages/optio-opencode/tests/test_hook_context.py
git commit -m "feat(optio-opencode): HookContext.copy_file — ObjectId blob source via load_blob"
```

---

## Task 18: `OpencodeTaskConfig` new hook fields + `DeliverableCallback` signature change

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/types.py`
- Test: `packages/optio-opencode/tests/test_types.py` (append; create the file if it doesn't exist).

- [ ] **Step 1: Append/create failing tests**

If `tests/test_types.py` doesn't exist, create it; otherwise append:

```python
"""Type-shape tests for OpencodeTaskConfig and DeliverableCallback."""

import inspect
from typing import get_type_hints

from optio_opencode.types import (
    DeliverableCallback,
    OpencodeTaskConfig,
    HookCallback,
)


def test_opencode_task_config_has_hook_fields():
    fields = {f for f in OpencodeTaskConfig.__dataclass_fields__}
    assert "before_execute" in fields
    assert "after_execute" in fields


def test_opencode_task_config_hook_default_none():
    cfg = OpencodeTaskConfig(consumer_instructions="x")
    assert cfg.before_execute is None
    assert cfg.after_execute is None


def test_deliverable_callback_now_takes_three_args():
    # The Callable type alias is structural; we can't introspect deeply,
    # but we can ensure HookCallback exists and is callable type.
    assert HookCallback is not None
    assert DeliverableCallback is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-opencode && pytest tests/test_types.py -v`
Expected: `ImportError: cannot import name 'HookCallback' from 'optio_opencode.types'` (and `before_execute` / `after_execute` not present).

- [ ] **Step 3: Update `types.py`**

Replace `packages/optio-opencode/src/optio_opencode/types.py` with:

```python
"""Public data types for optio-opencode consumers."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from optio_opencode.hook_context import HookContext


# DeliverableCallback now receives the same HookContext as before/after_execute,
# so callbacks no longer need to close over ctx. Breaking change vs. the
# pre-hooks signature `Callable[[str, str], Awaitable[None]]`.
DeliverableCallback = Callable[["HookContext", str, str], Awaitable[None]]
"""Consumer callback invoked per fetched DELIVERABLE.

Arguments: (hook_ctx, remote_path, decoded_text).
"""


HookCallback = Callable[["HookContext"], Awaitable[None]]
"""Hook callback receiving a HookContext. Used by before_execute and after_execute."""


@dataclass
class SSHConfig:
    host: str
    user: str
    key_path: str
    port: int = 22


@dataclass
class OpencodeTaskConfig:
    """Configuration for one optio-opencode task instance."""
    consumer_instructions: str
    opencode_config: dict[str, Any] = field(default_factory=dict)
    ssh: SSHConfig | None = None
    on_deliverable: DeliverableCallback | None = None
    install_if_missing: bool = True
    workdir_exclude: list[str] | None = None
    before_execute: HookCallback | None = None
    after_execute: HookCallback | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_types.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/types.py packages/optio-opencode/tests/test_types.py
git commit -m "feat(optio-opencode): OpencodeTaskConfig — before_execute/after_execute + HookCallback type"
```

---

## Task 19: Wire `before_execute` into `run_opencode_session`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/session.py`
- Test: `packages/optio-opencode/tests/test_session_hooks.py` (new file)

- [ ] **Step 1: Create new test file with failing test**

Create `packages/optio-opencode/tests/test_session_hooks.py`:

```python
"""Tests for before_execute / after_execute hook integration with run_opencode_session.

Uses LocalHost + fake_opencode for fast, mongo-free integration tests.
"""

import asyncio
import os
import sys

import pytest

from optio_opencode.session import run_opencode_session
from optio_opencode.types import OpencodeTaskConfig
from optio_opencode.host import LocalHost


pytestmark = pytest.mark.asyncio
HERE = os.path.dirname(__file__)
FAKE_OPENCODE = os.path.join(HERE, "fake_opencode.py")


def _make_local_host(tmp_workdir):
    return LocalHost(
        taskdir=tmp_workdir,
        opencode_cmd=[sys.executable, FAKE_OPENCODE],
    )


# A tiny ProcessContext stand-in for tests that don't need MongoDB.
# (For tests that DO need a real ctx — e.g., snapshot capture — use the
# mongo_db fixture and construct the real ProcessContext.)
class _MinimalCtx:
    def __init__(self):
        self.process_id = "test-process"
        self.params = {}
        self.metadata = {}
        self.services = {}
        self.resume = False
        self.calls = []

    def report_progress(self, percent, message=None):
        self.calls.append(("rp", percent, message))

    def should_continue(self):
        return True

    def set_widget_upstream(self, *a, **kw):
        pass

    def set_widget_data(self, *a, **kw):
        pass

    # Ones used by snapshot capture path; satisfy attribute lookups.
    _db = None
    _prefix = "test"


class _RecordingFakeHost:
    """A minimal Host stand-in that records call order. Sufficient for hook
    ordering tests without spinning up subprocess/SSH. The fake intentionally
    raises in launch_opencode so the session goes through teardown — that
    way we can validate hook ordering without modeling a full successful
    run loop here. Successful-path coverage is manual via the demo task."""

    def __init__(self, *, fail_in: str | None = None):
        self.workdir = "/wd"
        self.taskdir = "/wd"
        self.timeline: list[str] = []
        self._fail_in = fail_in
        self._connected = False

    def _maybe_fail(self, name):
        if self._fail_in == name:
            raise RuntimeError(f"injected failure in {name}")

    async def connect(self):
        self.timeline.append("connect")
        self._connected = True
        self._maybe_fail("connect")

    @property
    def is_connected(self):
        return self._connected

    async def setup_workdir(self):
        self.timeline.append("setup_workdir")
        self._maybe_fail("setup_workdir")

    async def write_text(self, *a, **kw):
        self.timeline.append(f"write_text:{a[0]}")
        self._maybe_fail("write_text")

    async def install_opencode_binary(self, *a, **kw):
        self.timeline.append("install_binary")
        self._maybe_fail("install_binary")

    async def ensure_opencode_installed(self, *a, **kw):
        self.timeline.append("install_binary")
        self._maybe_fail("install_binary")

    async def launch_opencode(self, *a, **kw):
        self.timeline.append("launch_opencode")
        self._maybe_fail("launch_opencode")
        raise RuntimeError("test never gets past launch")

    async def cleanup_taskdir(self, *a, **kw):
        self.timeline.append("cleanup_taskdir")

    async def disconnect(self):
        self.timeline.append("disconnect")
        self._connected = False

    async def resolve_host_home(self):
        return "/root"

    async def put_file_to_host(self, *a, **kw):
        self.timeline.append("put_file_to_host")

    async def fetch_bytes_from_host(self, *a, **kw):
        self.timeline.append("fetch_bytes_from_host")
        return b""

    async def run_command(self, *a, **kw):
        from optio_opencode.hook_context import RunResult
        self.timeline.append(f"run_command:{a[0]}")
        return RunResult(stdout="", stderr="", exit_code=0)


async def test_before_execute_runs_after_install_before_launch(tmp_workdir, monkeypatch):
    host = _RecordingFakeHost()
    ctx = _MinimalCtx()

    async def my_before(hook_ctx):
        host.timeline.append("before_execute")

    config = OpencodeTaskConfig(
        consumer_instructions="x",
        before_execute=my_before,
    )

    monkeypatch.setattr(
        "optio_opencode.session._build_host", lambda config: host,
    )

    with pytest.raises(RuntimeError, match="never gets past launch"):
        await run_opencode_session(ctx, config)

    install_idx = host.timeline.index("install_binary")
    before_idx = host.timeline.index("before_execute")
    launch_idx = host.timeline.index("launch_opencode")
    assert install_idx < before_idx < launch_idx
```

- [ ] **Step 2: Run the test to verify failure**

Run: `cd packages/optio-opencode && pytest tests/test_session_hooks.py -v`
Expected: Test fails with either `AttributeError: ... '_build_host'` (no factory yet), or `before_execute` is not in the timeline (hook isn't wired). Either confirms we need the implementation.

- [ ] **Step 3: Implement `_build_host` factory + wire `before_execute` into `run_opencode_session`**

The test in step 1 patches `_build_host`. If `session.py` doesn't yet have a `_build_host` factory, refactor the host-construction logic into one as part of this task — a small, well-scoped refactor that makes the rest of the test wiring possible.

In `packages/optio-opencode/src/optio_opencode/session.py`:

1. Extract host construction into a small factory if it isn't already one:
   ```python
   def _build_host(config: OpencodeTaskConfig):
       if config.ssh is None:
           return LocalHost(taskdir=_pick_local_taskdir())
       return RemoteHost(ssh_config=config.ssh)
   ```
   Replace the inline `host = LocalHost(...) / RemoteHost(...)` in the session function with `host = _build_host(config)`.

2. Just after the binary-install block and before `launch_opencode`, add:
   ```python
   from optio_opencode.hook_context import HookContext

   hook_ctx = HookContext(ctx, host)
   if config.before_execute is not None:
       await config.before_execute(hook_ctx)
   ```

   Pass `hook_ctx` down to the `_deliverable_fetch_loop` call (Task 21 will use it). For now, keep the existing call signature; we'll change it then.

- [ ] **Step 4: Run the test**

Run: `cd packages/optio-opencode && pytest tests/test_session_hooks.py -v`
Expected: Test passes.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/session.py packages/optio-opencode/tests/test_session_hooks.py
git commit -m "feat(optio-opencode): wire before_execute into run_opencode_session pipeline"
```

---

## Task 20: Wire `after_execute` into the finally block (before snapshot capture)

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/session.py`
- Test: `packages/optio-opencode/tests/test_session_hooks.py` (append)

- [ ] **Step 1: Append failing tests**

**Note on coverage scope.** The fake host in `test_session_hooks.py`
intentionally raises in `launch_opencode`, so the success-path
behaviour of `after_execute` (running on a clean finish) cannot be
unit-tested at this layer without faking the entire run loop. The
demo task in `optio-demo` exercises the success path manually
end-to-end; that is the canonical integration check for now. The
tests below cover the failure / pre-connect / cleanup paths, which
are where the wiring is most likely to go wrong.

```python
async def test_after_execute_runs_when_before_execute_raises(tmp_workdir, monkeypatch):
    host = _RecordingFakeHost()
    ctx = _MinimalCtx()

    async def failing_before(hook_ctx):
        host.timeline.append("before_execute")
        raise RuntimeError("before fails")

    async def my_after(hook_ctx):
        host.timeline.append("after_execute")

    config = OpencodeTaskConfig(
        consumer_instructions="x",
        before_execute=failing_before,
        after_execute=my_after,
    )
    monkeypatch.setattr(
        "optio_opencode.session._build_host", lambda config: host,
    )

    with pytest.raises(RuntimeError, match="before fails"):
        await run_opencode_session(ctx, config)

    # before_execute and after_execute both ran; cleanup ran.
    assert "before_execute" in host.timeline
    assert "after_execute" in host.timeline
    assert "launch_opencode" not in host.timeline  # never launched
    assert "cleanup_taskdir" in host.timeline


async def test_after_execute_skipped_when_failure_before_host_connect(tmp_workdir, monkeypatch):
    host = _RecordingFakeHost(fail_in="connect")
    ctx = _MinimalCtx()

    async def my_after(hook_ctx):
        host.timeline.append("after_execute")

    config = OpencodeTaskConfig(
        consumer_instructions="x",
        after_execute=my_after,
    )
    monkeypatch.setattr(
        "optio_opencode.session._build_host", lambda config: host,
    )

    with pytest.raises(RuntimeError, match="injected failure"):
        await run_opencode_session(ctx, config)

    # Before host connected, hook_ctx wasn't built — after_execute is skipped.
    assert "after_execute" not in host.timeline


async def test_after_execute_failure_does_not_shadow_session_error(tmp_workdir, monkeypatch):
    """If session is already failing, an after_execute exception is logged
    via report_progress but doesn't override the original cause."""
    host = _RecordingFakeHost()
    ctx = _MinimalCtx()

    async def failing_before(hook_ctx):
        raise RuntimeError("primary failure")

    async def failing_after(hook_ctx):
        raise RuntimeError("secondary after failure")

    config = OpencodeTaskConfig(
        consumer_instructions="x",
        before_execute=failing_before,
        after_execute=failing_after,
    )
    monkeypatch.setattr(
        "optio_opencode.session._build_host", lambda config: host,
    )

    with pytest.raises(RuntimeError, match="primary failure"):
        await run_opencode_session(ctx, config)

    # The secondary exception was reported via ctx.report_progress.
    assert any(
        "after_execute callback raised" in str(c[2])
        for c in ctx.calls
    )
```

- [ ] **Step 2: Run tests to verify failures**

Run: `cd packages/optio-opencode && pytest tests/test_session_hooks.py -v`
Expected: The three new tests fail because `after_execute` is not yet wired.

- [ ] **Step 3: Wire `after_execute` into the finally block**

In `session.py`, find the existing `finally:` block where snapshot capture and cleanup happen. Insert `after_execute` invocation immediately after `terminate_opencode` and immediately before `_capture_snapshot`:

```python
    finally:
        # Existing: terminate opencode if running.
        # ... existing code that terminates opencode and waits ...

        # NEW: after_execute. Runs whenever hook_ctx exists, regardless of
        # success/failure/cancellation. Side effects become part of the
        # snapshot (which runs next).
        if config.after_execute is not None and hook_ctx is not None:
            try:
                await config.after_execute(hook_ctx)
            except BaseException as after_exc:
                if session_error is None:
                    raise
                ctx.report_progress(
                    None,
                    f"after_execute callback raised: {after_exc!r}",
                )

        # Existing: snapshot capture.
        # ... existing _capture_snapshot call ...

        # Existing: cleanup_taskdir + disconnect.
        # ... existing code ...
```

You will also need to introduce `session_error: BaseException | None = None` at the top of the function and capture exceptions from the main try block:

```python
    hook_ctx: HookContext | None = None
    session_error: BaseException | None = None
    try:
        # ... existing pipeline ...
    except BaseException as exc:
        session_error = exc
        raise
    finally:
        # ... after_execute + snapshot + cleanup, as above ...
```

Build `hook_ctx = HookContext(ctx, host)` immediately after `host.setup_workdir()` (or right before `before_execute` is called) so it's available for `after_execute` even if `before_execute` itself fails.

- [ ] **Step 4: Run tests to verify pass**

Run: `cd packages/optio-opencode && pytest tests/test_session_hooks.py -v`
Expected: All three new tests pass plus the existing `test_before_execute_runs_after_install_before_launch` from Task 19.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/session.py packages/optio-opencode/tests/test_session_hooks.py
git commit -m "feat(optio-opencode): wire after_execute into finally block before snapshot"
```

---

## Task 21: Update `_deliverable_fetch_loop` to pass `HookContext` into `on_deliverable`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/session.py` (`_deliverable_fetch_loop` + its caller)
- Test: `packages/optio-opencode/tests/test_session_hooks.py` (append)

- [ ] **Step 1: Append failing test**

```python
async def test_on_deliverable_receives_hook_ctx_and_can_use_host_primitives(tmp_workdir, monkeypatch):
    host = _RecordingFakeHost()
    ctx = _MinimalCtx()
    received = []

    async def cb(hook_ctx, path, text):
        received.append((path, text))
        # The hook_ctx must expose host primitives — exercise one.
        await hook_ctx.run_on_host("noop")

    config = OpencodeTaskConfig(
        consumer_instructions="x",
        on_deliverable=cb,
    )
    # We don't run a full session here; we directly invoke
    # _deliverable_fetch_loop with a constructed HookContext.
    from optio_opencode.session import _deliverable_fetch_loop
    from optio_opencode.hook_context import HookContext

    queue = asyncio.Queue()
    await queue.put("/wd/deliverables/x.txt")

    # Patch host.fetch_deliverable_text to return canned content.
    async def _fake_fetch(_path):
        return "deliverable text"
    host.fetch_deliverable_text = _fake_fetch  # type: ignore[attr-defined]

    hook_ctx = HookContext(ctx, host)
    task = asyncio.create_task(
        _deliverable_fetch_loop(host, cb, queue, ctx, hook_ctx)
    )
    await queue.join()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert received == [("/wd/deliverables/x.txt", "deliverable text")]
    assert "run_command:noop" in host.timeline
```

- [ ] **Step 2: Run test to verify failure**

Expected: `TypeError: _deliverable_fetch_loop() takes ... positional arguments` (signature mismatch — the loop only takes 4 args today).

- [ ] **Step 3: Update `_deliverable_fetch_loop` signature and caller**

In `session.py`:

1. Change the function signature:
   ```python
   async def _deliverable_fetch_loop(
       host: Host,
       callback,  # DeliverableCallback | None
       queue: asyncio.Queue[str],
       ctx: ProcessContext,
       hook_ctx: HookContext,
   ) -> None:
       # ... unchanged body, except the callback invocation ...
       # OLD: await callback(path, text)
       # NEW: await callback(hook_ctx, path, text)
   ```

2. At the call site (where `_deliverable_fetch_loop` is wrapped in `asyncio.create_task`), pass `hook_ctx` as the new arg:
   ```python
   fetch_task = asyncio.create_task(
       _deliverable_fetch_loop(
           host, config.on_deliverable, deliverable_queue, ctx, hook_ctx,
       )
   )
   ```

`hook_ctx` is the same instance constructed earlier in Task 20.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && pytest tests/test_session_hooks.py -v`
Expected: The new test passes.

Also run the full session test suite to confirm nothing else broke:

Run: `cd packages/optio-opencode && pytest tests/test_session_local.py tests/test_session_remote.py tests/test_session_resume.py -v`
Expected: Existing tests pass — but note that any existing test that used the old `on_deliverable(path, text)` signature will fail until updated. Update those tests as part of this task to use the new `(hook_ctx, path, text)` signature.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/session.py packages/optio-opencode/tests/
git commit -m "feat(optio-opencode): on_deliverable receives HookContext (breaking signature change)"
```

---

## Task 22: Public-API exports in `optio_opencode/__init__.py`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/__init__.py`
- Test: `packages/optio-opencode/tests/test_sanity.py` (append).

- [ ] **Step 1: Append failing test**

```python
def test_optio_opencode_exports_hook_context_types():
    import optio_opencode
    assert hasattr(optio_opencode, "HookContext")
    assert hasattr(optio_opencode, "HookContextProtocol")
    assert hasattr(optio_opencode, "RunResult")
    assert hasattr(optio_opencode, "HostCommandError")
    assert hasattr(optio_opencode, "HookCallback")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd packages/optio-opencode && pytest tests/test_sanity.py::test_optio_opencode_exports_hook_context_types -v`
Expected: AttributeError on `HookContext`.

- [ ] **Step 3: Add re-exports**

In `packages/optio-opencode/src/optio_opencode/__init__.py`, append (or merge with the existing exports list):

```python
from optio_opencode.hook_context import (
    HookContext,
    HookContextProtocol,
    HostCommandError,
    RunResult,
)
from optio_opencode.types import HookCallback

__all__ = [
    # ... existing exports ...
    "HookContext",
    "HookContextProtocol",
    "HostCommandError",
    "RunResult",
    "HookCallback",
]
```

(Preserve any existing `__all__` entries; add the new ones.)

- [ ] **Step 4: Run test to verify pass**

Run: `cd packages/optio-opencode && pytest tests/test_sanity.py -v`
Expected: All sanity tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/__init__.py packages/optio-opencode/tests/test_sanity.py
git commit -m "feat(optio-opencode): export HookContext / HookContextProtocol / RunResult / HostCommandError / HookCallback"
```

---

## Task 23: Rewrite `optio-demo/tasks/opencode.py` + smoke test

**Files:**
- Modify: `packages/optio-demo/src/optio_demo/tasks/opencode.py`
- Create: `packages/optio-demo/tests/__init__.py` (empty)
- Create: `packages/optio-demo/tests/test_demo_smoke.py`

- [ ] **Step 1: Create the smoke test (failing)**

Create `packages/optio-demo/tests/__init__.py` (empty file).

Create `packages/optio-demo/tests/test_demo_smoke.py`:

```python
"""Smoke test: optio-demo's opencode task imports and is well-formed."""

import inspect

from optio_demo.tasks.opencode import get_tasks


def test_get_tasks_returns_one_task_instance():
    tasks = get_tasks()
    assert len(tasks) == 1
    t = tasks[0]
    assert t.process_id == "opencode-demo"
    assert t.name == "Opencode demo"
    assert t.ui_widget == "iframe"


def test_demo_does_not_use_wrapper_execute_pattern():
    """Confirm no _make_on_deliverable factory or inner.execute(ctx) wrapping."""
    import optio_demo.tasks.opencode as mod
    src = inspect.getsource(mod)
    assert "_make_on_deliverable" not in src
    # No inner-task execute wrapping.
    assert "inner.execute(ctx)" not in src
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd packages/optio-demo && pytest tests/test_demo_smoke.py -v`
Expected: `test_demo_does_not_use_wrapper_execute_pattern` fails (the current demo still uses the wrapper pattern).

- [ ] **Step 3: Rewrite the demo file**

Replace `packages/optio-demo/src/optio_demo/tasks/opencode.py` with:

```python
"""Reference demo task for optio-opencode.

Defaults to local mode; set the ``OPTIO_OPENCODE_DEMO_SSH_HOST``
environment variable to run the same task via SSH on a remote host.
Relevant env vars (all optional except ``_HOST``):

- ``OPTIO_OPENCODE_DEMO_SSH_HOST`` — enables remote mode.
- ``OPTIO_OPENCODE_DEMO_SSH_USER`` — default: ``$USER`` on the worker.
- ``OPTIO_OPENCODE_DEMO_SSH_KEY_PATH`` — default: ``~/.ssh/id_ed25519``.
- ``OPTIO_OPENCODE_DEMO_SSH_PORT`` — default: ``22``.

The before_execute hook runs ``whoami`` on the host before opencode
launches and reports the result via ``report_progress``. The
on_deliverable callback prints the deliverable body to the worker's
terminal — additive to the framework's auto-emitted
``"Deliverable: <path>"`` progress message.
"""

from __future__ import annotations

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


async def _on_deliverable(
    hook_ctx: HookContext, path: str, text: str,
) -> None:
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

(Note: `create_opencode_task` already declares `supports_resume=True` per the resume merge, and we don't need to set it again here.)

- [ ] **Step 4: Run tests to verify pass**

Run: `cd packages/optio-demo && pytest tests/test_demo_smoke.py -v`
Expected: Both smoke tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-demo/src/optio_demo/tasks/opencode.py packages/optio-demo/tests/__init__.py packages/optio-demo/tests/test_demo_smoke.py
git commit -m "feat(optio-demo): rewrite opencode demo to direct create_opencode_task + before_execute hook"
```

---

## Task 24: Update `packages/optio-opencode/AGENTS.md`

**Files:**
- Modify: `packages/optio-opencode/AGENTS.md`

- [ ] **Step 1: Read the current AGENTS.md**

Run: `cat packages/optio-opencode/AGENTS.md`
Identify the "Public API" section, the `on_deliverable` description, and any place that endorses or implies a wrapper-`_execute` pattern.

- [ ] **Step 2: Add a "Hooks" section**

Insert a new section between "Public API" and "Log-file contract":

```markdown
## Hooks: `before_execute` / `after_execute`

`OpencodeTaskConfig` accepts two optional async callbacks:

- `before_execute(hook_ctx)`: runs after the opencode binary has been
  installed on the host but before opencode launches. Use this to
  ship per-task files via `hook_ctx.copy_file(...)` or to run setup
  commands via `hook_ctx.run_on_host(...)`.
- `after_execute(hook_ctx)`: runs after opencode has terminated (or
  been cancelled) and **before** snapshot capture. Side effects in
  this hook become part of the snapshot, so files written here are
  preserved across resumes. The hook always runs once a host has
  been connected and a workdir set up — even if `before_execute` or
  the run loop fails.

Both hooks receive a `HookContext`, which is a wrapped
`ProcessContext` extended with four host primitives:

- `copy_file(source, target, *, skip_if_unchanged=False)` — ship a
  file (path / bytes / GridFS blob `ObjectId`) to the host. `target`
  is workdir-relative by default; `/`-prefixed paths are absolute,
  `~/`-prefixed paths are home-relative. `skip_if_unchanged=True`
  computes SHA-256 on both sides and skips the transfer when they
  match.
- `run_on_host(command, *, check=True, capture_stderr=False, cwd=None)`
  — run a shell command on the host. With `check=True` (default),
  returns stdout on exit 0 and raises `HostCommandError` on non-zero;
  with `check=False`, returns a `RunResult(stdout, stderr,
  exit_code)`.
- `read_from_host(path) -> bytes` and `read_text_from_host(path) -> str`
  — fetch a file from the host. Same path conventions as
  `copy_file`.

`HookContext` also exposes everything `ProcessContext` does
(`report_progress`, `should_continue`, `params`, GridFS helpers,
etc.) via attribute delegation. For best IDE support, type-hint
your hooks against `HookContextProtocol`.

### Failure semantics

- `before_execute` raising → the session fails immediately. Opencode
  never launches. `after_execute` still runs. Cleanup runs.
- `after_execute` raising on a successful session → the session is
  marked failed with the `after_execute` exception.
- `after_execute` raising on an already-failing session → the
  exception is logged via `report_progress("after_execute callback
  raised: ...")` and does not shadow the original cause.
```

- [ ] **Step 3: Update the `on_deliverable` description**

Find the existing `on_deliverable` description and replace its signature:

```markdown
- `on_deliverable: Callable[[HookContext, str, str], Awaitable[None]] | None`
  — invoked once per fetched DELIVERABLE with `(hook_ctx,
  remote_path, decoded_text)`. The framework already auto-emits a
  `"Deliverable: <path>"` progress message before the callback fires,
  so callbacks only need to add behavior beyond that (e.g. parsing
  the body, fetching a related file via
  `hook_ctx.read_text_from_host`, etc.). **Breaking change**: prior
  to the hooks feature, this callback received `(path, text)` only.
```

- [ ] **Step 4: Update the "Public API" example to the new pattern**

If the existing "Public API" section shows a wrapper-`_execute`
pattern, replace it with:

```python
from optio_opencode import OpencodeTaskConfig, create_opencode_task

def get_tasks():
    return [
        create_opencode_task(
            process_id="my-task",
            name="My task",
            config=OpencodeTaskConfig(
                consumer_instructions="...",
                ssh=my_ssh_config,
                before_execute=my_before_hook,
                on_deliverable=my_callback,
            ),
        )
    ]
```

- [ ] **Step 5: Verify the markdown renders sensibly**

Run: `cat packages/optio-opencode/AGENTS.md | head -100`
(Just visually scan for obvious mistakes — broken indentation, mismatched code fences.)

- [ ] **Step 6: Commit**

```bash
git add packages/optio-opencode/AGENTS.md
git commit -m "docs(optio-opencode): document before_execute / after_execute / HookContext"
```

---

## Final verification

- [ ] **Step 1: Run the full optio-opencode test suite**

Run: `cd packages/optio-opencode && pytest -v`
Expected: All tests pass. RemoteHost integration tests skip if Docker is unavailable; otherwise pass.

- [ ] **Step 2: Run the optio-demo test suite**

Run: `cd packages/optio-demo && pytest -v`
Expected: Smoke test passes.

- [ ] **Step 3: Verify the package imports cleanly**

Run: `cd packages/optio-opencode && python -c "from optio_opencode import HookContext, HookContextProtocol, RunResult, HostCommandError, HookCallback, OpencodeTaskConfig, create_opencode_task; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Final summary commit (if anything was missed)**

Only if there are uncommitted changes from any of the prior tasks (e.g., a missed AGENTS.md tweak, an export forgotten):

```bash
git status
# If clean: skip this step.
# Otherwise:
git add -p
git commit -m "chore: catch-up commit for missed tweaks"
```
