# optio-host download_file Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `HookContext.download_file(url, target)` method that downloads a URL to a file on the same host the parent task runs on, as a cooperatively-cancellable child process with curl-driven progress.

**Architecture:** Public factory `create_download_task` lives in a new module `optio_host/download.py`; produces a `TaskInstance` whose execute drives `curl --trace-ascii -` (via `host.launch_subprocess` when a `Host` is given, or `asyncio.create_subprocess_exec` when `host=None`) and parses byte-progress on stdout while ring-buffering stderr for the failure path. `HookContext.download_file` resolves the target, generates a stable child id from the parent's `_child_counter`, builds the task and runs it via `ctx.run_child`. No optio-core changes.

**Tech Stack:** Python 3.12+, asyncio, pytest, motor (not needed for these tests), curl (system), stdbuf (system, optional).

**Spec:** `docs/2026-05-12-optio-host-download-design.md`.

---

## File Structure

| File | Purpose | Action |
|------|---------|--------|
| `packages/optio-host/src/optio_host/download.py` | `DownloadFailed` exception, `create_download_task` factory, `_execute` closure body, internal helpers (trace parser, command builder, cleanup). | CREATE |
| `packages/optio-host/src/optio_host/context.py` | Add `download_file` method to `HookContext` (next to `copy_file`) and a corresponding signature in `HookContextProtocol`. | MODIFY |
| `packages/optio-host/src/optio_host/__init__.py` | Re-export `create_download_task` and `DownloadFailed`. | MODIFY |
| `packages/optio-host/AGENTS.md` | Mention `download.py` under L0 layer; list `HookContext.download_file`. | MODIFY |
| `packages/optio-host/tests/test_download.py` | All unit tests (routing + real-curl integration via stdlib HTTP server). | CREATE |

The download module is a single file. It's small enough (parser, command builder, two execute branches, one factory, one exception, ~250 LOC est.) that further splitting is premature.

---

### Task 1: Scaffold `DownloadFailed` exception (TDD)

**Files:**
- Create: `packages/optio-host/src/optio_host/download.py`
- Create: `packages/optio-host/tests/test_download.py`

- [ ] **Step 1: Write failing test**

Create `packages/optio-host/tests/test_download.py`:

```python
"""Tests for optio_host.download — the URL → file task factory and HookContext.download_file."""

import pytest


def test_download_failed_fields_and_str():
    from optio_host.download import DownloadFailed
    err = DownloadFailed(
        url="https://example/foo.bin",
        target="/tmp/foo.bin",
        exit_code=22,
        stderr_tail="curl: (22) The requested URL returned error: 404\n",
    )
    assert err.url == "https://example/foo.bin"
    assert err.target == "/tmp/foo.bin"
    assert err.exit_code == 22
    assert "curl" in err.stderr_tail
    s = str(err)
    assert "https://example/foo.bin" in s
    assert "22" in s
    assert "404" in s
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/csillag/deai/optio/packages/optio-host
python -m pytest tests/test_download.py::test_download_failed_fields_and_str -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'optio_host.download'`.

- [ ] **Step 3: Write minimal implementation**

Create `packages/optio-host/src/optio_host/download.py`:

```python
"""Download a URL to a file on a host, as an optio child task.

Public surface:
  - DownloadFailed: exception raised by the child's execute on curl failure.
  - create_download_task: factory returning a TaskInstance.

See docs/2026-05-12-optio-host-download-design.md for the full design.
"""

from __future__ import annotations


class DownloadFailed(Exception):
    """Raised when a download child task's curl invocation exits non-zero.

    Carries enough structured detail to diagnose:
      - url: the URL that was being downloaded
      - target: the absolute path curl was writing to (on the host)
      - exit_code: curl's exit code (see curl(1) EXIT CODES)
      - stderr_tail: up to ~1 KB of curl's stderr (the most recent bytes)

    Note: when this exception propagates out of a child task body, the
    optio-core executor converts it to ``str(self)`` in ``status.error``
    and the parent's ``run_child`` re-raises as a plain ``RuntimeError``.
    See /tmp/optio-child-failure-problem.md for the cross-cutting fix shape.
    """

    def __init__(self, *, url: str, target: str, exit_code: int, stderr_tail: str) -> None:
        self.url = url
        self.target = target
        self.exit_code = exit_code
        self.stderr_tail = stderr_tail
        # First 200 chars of stderr_tail keep the message bounded.
        super().__init__(
            f"download failed (exit {exit_code}): {url} -> {target}\n"
            f"stderr: {stderr_tail[:200]}"
        )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_download.py::test_download_failed_fields_and_str -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-host/src/optio_host/download.py packages/optio-host/tests/test_download.py
git commit -m "feat(optio-host): add DownloadFailed exception"
```

---

### Task 2: Factory signature returns TaskInstance (TDD)

**Files:**
- Modify: `packages/optio-host/src/optio_host/download.py`
- Modify: `packages/optio-host/tests/test_download.py`

- [ ] **Step 1: Append failing tests**

Append to `packages/optio-host/tests/test_download.py`:

```python
def test_create_download_task_returns_taskinstance_with_fields():
    from optio_core.models import TaskInstance
    from optio_host.download import create_download_task

    t = create_download_task(
        process_id="p.download-0",
        name="download foo.bin",
        url="https://example.com/foo.bin",
        target="/tmp/foo.bin",
        host=None,
        description="grab the binary",
    )

    assert isinstance(t, TaskInstance)
    assert t.process_id == "p.download-0"
    assert t.name == "download foo.bin"
    assert t.description == "grab the binary"
    assert t.cancellable is True
    assert t.supports_resume is False
    # auto_cancel_children is True by default on TaskInstance.
    assert t.auto_cancel_children is True
    # ui_widget left None (no special UI).
    assert t.ui_widget is None
    # execute is callable, signature `async def _execute(ctx)`.
    assert callable(t.execute)


def test_create_download_task_defaults_description_and_cleanup_flag():
    from optio_host.download import create_download_task

    t = create_download_task(
        process_id="p.download-0",
        name="download foo.bin",
        url="https://example.com/foo.bin",
        target="/tmp/foo.bin",
    )
    assert t.description is None
```
- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_download.py -v
```

Expected: 2 new tests FAIL with `AttributeError: module 'optio_host.download' has no attribute 'create_download_task'`.

- [ ] **Step 3: Implement factory stub**

Append to `packages/optio-host/src/optio_host/download.py`:

```python
from typing import Any

from optio_core.models import TaskInstance

# Forward import of Host only for typing; avoid a circular reference by
# keeping it under TYPE_CHECKING.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from optio_host.host import Host


def create_download_task(
    process_id: str,
    name: str,
    *,
    url: str,
    target: str,
    host: "Host | None" = None,
    description: str | None = None,
    cleanup_on_fail: bool = True,
) -> TaskInstance:
    """Build a TaskInstance that downloads ``url`` to ``target`` on ``host``.

    Args:
      process_id: child process_id for the resulting TaskInstance.
      name: child process name (typically ``"download <basename>"``).
      url: http(s) URL to download.
      target: absolute path the response body is written to. When ``host`` is
        given, this is an absolute path on that host. When ``host`` is None,
        this is an absolute path on the local filesystem.
      host: when provided, curl runs via ``host.launch_subprocess``. When
        None, curl runs locally via ``asyncio.create_subprocess_exec``.
      description: optional description shown in the UI for the child.
      cleanup_on_fail: when True (default), best-effort delete the target
        file if curl exits non-zero or the child is cancelled. Errors during
        cleanup are swallowed.
    """

    async def _execute(ctx: Any) -> None:
        # Implementation lands in later tasks. Raise NotImplementedError so
        # an accidental early-binding doesn't silently no-op.
        raise NotImplementedError("download _execute body not implemented yet")

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        cancellable=True,
        supports_resume=False,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_download.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-host/src/optio_host/download.py packages/optio-host/tests/test_download.py
git commit -m "feat(optio-host): add create_download_task factory (stub execute)"
```

---

### Task 3: `HookContext.download_file` routing (TDD)

**Files:**
- Modify: `packages/optio-host/src/optio_host/context.py`
- Modify: `packages/optio-host/tests/test_download.py`

- [ ] **Step 1: Append failing routing tests**

Append to `packages/optio-host/tests/test_download.py`:

```python
import asyncio


class _RoutingFakeCtx:
    """Fake ProcessContext for testing HookContext.download_file routing only.

    Does NOT execute the captured child task. We just want to inspect the
    arguments that download_file passes to run_child.
    """

    def __init__(self, *, process_id="p", workdir_for_resolve=None):
        self.process_id = process_id
        self._child_counter = {"next": 0}
        self.run_child_calls = []  # list of (execute, process_id, name, description)

    async def run_child(self, execute, process_id, name, *, description=None, **kw):
        self.run_child_calls.append((execute, process_id, name, description))
        # Increment counter the way the real ProcessContext.run_child does
        # (via Executor.execute_child calling _next_child_order on the ctx).
        self._child_counter["next"] += 1
        return "done"


class _RoutingFakeHost:
    def __init__(self, *, workdir="/wd", host_home="/home/u"):
        self.workdir = workdir
        self._host_home = host_home

    async def resolve_host_home(self):
        return self._host_home


async def test_download_file_routes_through_run_child_with_generated_id_and_name():
    from optio_host.context import HookContext

    ctx = _RoutingFakeCtx(process_id="root.parent")
    host = _RoutingFakeHost(workdir="/wd")
    h = HookContext(ctx, host)

    await h.download_file(
        "https://example/foo.bin",
        "downloads/foo.bin",
    )

    assert len(ctx.run_child_calls) == 1
    execute, pid, name, description = ctx.run_child_calls[0]
    assert pid == "root.parent.download-0"
    assert name == "download foo.bin"
    assert description is None


async def test_download_file_second_call_increments_counter():
    from optio_host.context import HookContext

    ctx = _RoutingFakeCtx(process_id="root.parent")
    host = _RoutingFakeHost(workdir="/wd")
    h = HookContext(ctx, host)

    await h.download_file("https://example/a.bin", "a.bin")
    await h.download_file("https://example/b.bin", "b.bin")

    assert ctx.run_child_calls[0][1] == "root.parent.download-0"
    assert ctx.run_child_calls[1][1] == "root.parent.download-1"


async def test_download_file_passes_description_through():
    from optio_host.context import HookContext

    ctx = _RoutingFakeCtx()
    host = _RoutingFakeHost()
    h = HookContext(ctx, host)

    await h.download_file(
        "https://example/foo.bin", "foo.bin",
        description="grab it",
    )
    assert ctx.run_child_calls[0][3] == "grab it"


async def test_download_file_resolves_workdir_relative_target_to_absolute():
    """The factory should receive an already-resolved absolute target path."""
    from optio_host.context import HookContext
    from optio_host.download import create_download_task

    captured = {}
    real_create = create_download_task

    def spy_create(*args, **kwargs):
        captured.update(kwargs)
        return real_create(*args, **kwargs)

    import optio_host.context as ctx_mod
    monkey_orig = getattr(ctx_mod, "create_download_task", None)
    ctx_mod.create_download_task = spy_create
    try:
        ctx = _RoutingFakeCtx()
        host = _RoutingFakeHost(workdir="/wd")
        h = HookContext(ctx, host)
        await h.download_file("https://example/foo.bin", "sub/foo.bin")
    finally:
        if monkey_orig is None:
            del ctx_mod.create_download_task
        else:
            ctx_mod.create_download_task = monkey_orig

    assert captured["target"] == "/wd/sub/foo.bin"
    assert captured["url"] == "https://example/foo.bin"
    assert captured["host"] is host


async def test_download_file_rejects_workdir_escape_without_spawning():
    from optio_host.context import HookContext

    ctx = _RoutingFakeCtx()
    host = _RoutingFakeHost()
    h = HookContext(ctx, host)

    with pytest.raises(ValueError):
        await h.download_file("https://example/foo", "../escape")
    assert ctx.run_child_calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_download.py -v
```

Expected: 5 new tests FAIL — most with `AttributeError: 'HookContext' object has no attribute 'download_file'`.

- [ ] **Step 3: Implement `HookContext.download_file`**

Modify `packages/optio-host/src/optio_host/context.py`. Insert this method just after the existing `copy_file` method body (before `_read_blob_bytes`):

```python
    async def download_file(
        self,
        url: str,
        target: str,
        *,
        description: str | None = None,
        cleanup_on_fail: bool = True,
    ) -> None:
        """Download ``url`` to ``target`` on the host as a child task.

        Spawns a child process under the current task that runs curl on the
        same host the parent runs on. Reports a single "Downloading <name>"
        message followed by numeric progress percent updates on the child's
        ProcessContext.

        ``target`` is resolved by the same rules as ``copy_file``:
            absolute path | ``~`` / ``~/...`` home-relative | workdir-relative.
        On a workdir-escape attempt this raises ``ValueError`` without
        spawning a child.

        Returns None on success. Raises ``RuntimeError`` if the child fails
        (the underlying ``DownloadFailed`` is lost at the run_child
        boundary; see the design doc). Parent-task cancellation propagates
        to the child automatically (``auto_cancel_children=True`` default).
        """
        # Lazy import: download module imports TaskInstance from optio_core
        # and we want context.py's import graph to stay small.
        from optio_host.download import create_download_task

        host_home = await self._host.resolve_host_home()
        abs_target = _resolve_target_path(target, self._host.workdir, host_home)
        basename = os.path.basename(abs_target) or abs_target

        # Peek (do not increment) the current child counter; run_child below
        # will perform the actual increment as it allocates the order index.
        n = self._ctx._child_counter.get("next", 0)
        child_process_id = f"{self._ctx.process_id}.download-{n}"
        child_name = f"download {basename}"

        task = create_download_task(
            process_id=child_process_id,
            name=child_name,
            url=url,
            target=abs_target,
            host=self._host,
            description=description,
            cleanup_on_fail=cleanup_on_fail,
        )
        await self._ctx.run_child(
            task.execute, task.process_id, task.name,
            description=task.description,
        )
```

Also add `create_download_task` as a *module-level* attribute reference in `context.py` so the monkeypatch in `test_download_file_resolves_workdir_relative_target_to_absolute` can intercept it. Replace the lazy import inside `download_file` with the following pattern at the top of `context.py` (after existing imports):

```python
# Imported lazily inside download_file at call time. We rebind here as a
# module attribute so tests can monkeypatch it.
def _import_create_download_task():
    from optio_host.download import create_download_task
    return create_download_task

# Module attribute pointing at the factory; tests can monkeypatch this.
create_download_task = None  # populated on first use
```

And inside `download_file`, replace the import with:

```python
        global create_download_task
        if create_download_task is None:
            create_download_task = _import_create_download_task()
        task = create_download_task(
            process_id=child_process_id,
            ...
        )
```

This indirection is solely for testability; without it monkeypatching `optio_host.context.create_download_task` would not intercept the call.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_download.py -v
```

Expected: all tests PASS (the 3 existing + 5 new).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-host/src/optio_host/context.py packages/optio-host/tests/test_download.py
git commit -m "feat(optio-host): add HookContext.download_file routing"
```

---

### Task 4: Add `download_file` to `HookContextProtocol` (TDD)

**Files:**
- Modify: `packages/optio-host/src/optio_host/context.py`
- Modify: `packages/optio-host/tests/test_download.py`

- [ ] **Step 1: Append failing test**

Append to `packages/optio-host/tests/test_download.py`:

```python
def test_download_file_appears_on_hook_context_protocol():
    from optio_host.context import HookContextProtocol
    methods = {m for m in dir(HookContextProtocol) if not m.startswith("_")}
    assert "download_file" in methods
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_download.py::test_download_file_appears_on_hook_context_protocol -v
```

Expected: FAIL.

- [ ] **Step 3: Add the signature**

Modify `packages/optio-host/src/optio_host/context.py` — inside `class HookContextProtocol`, after `copy_file` signature, add:

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

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_download.py::test_download_file_appears_on_hook_context_protocol -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-host/src/optio_host/context.py packages/optio-host/tests/test_download.py
git commit -m "feat(optio-host): expose download_file in HookContextProtocol"
```

---

### Task 5: Re-export from package top-level (TDD)

**Files:**
- Modify: `packages/optio-host/src/optio_host/__init__.py`
- Modify: `packages/optio-host/tests/test_download.py`

- [ ] **Step 1: Append failing test**

```python
def test_create_download_task_and_downloadfailed_exported_from_optio_host():
    import optio_host
    assert hasattr(optio_host, "create_download_task")
    assert hasattr(optio_host, "DownloadFailed")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_download.py::test_create_download_task_and_downloadfailed_exported_from_optio_host -v
```

Expected: FAIL.

- [ ] **Step 3: Add exports**

Read `packages/optio-host/src/optio_host/__init__.py` first, then add (after existing exports):

```python
from optio_host.download import DownloadFailed, create_download_task

__all__ = list(globals().get("__all__", [])) + ["DownloadFailed", "create_download_task"]
```

(If `__init__.py` has an explicit `__all__` list already, append the two names to it directly instead of the runtime mutation above.)

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_download.py::test_create_download_task_and_downloadfailed_exported_from_optio_host -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-host/src/optio_host/__init__.py packages/optio-host/tests/test_download.py
git commit -m "feat(optio-host): re-export create_download_task and DownloadFailed"
```

---

### Task 6: Trace parser unit — internal helper (TDD)

The `_execute` closure has a parser-shaped piece of logic that we want to test by behavior (real curl). But we also extract a single pure helper for the per-line parsing decision — that one we can unit-test in isolation without curl.

**Files:**
- Modify: `packages/optio-host/src/optio_host/download.py`
- Modify: `packages/optio-host/tests/test_download.py`

- [ ] **Step 1: Append failing tests**

```python
def test_parse_trace_line_content_length():
    from optio_host.download import _parse_trace_line
    out = _parse_trace_line(b"0000: content-length: 1048576\r\n")
    assert out == ("length", 1048576)


def test_parse_trace_line_recv_data():
    from optio_host.download import _parse_trace_line
    out = _parse_trace_line(b"<= Recv data, 16384 bytes (0x4000)\n")
    assert out == ("recv", 16384)


def test_parse_trace_line_recv_data_lowercased():
    from optio_host.download import _parse_trace_line
    # curl emits "Recv" (capital R); parser must normalize.
    out = _parse_trace_line(b"<= recv data, 4096 bytes (0x1000)\n")
    assert out == ("recv", 4096)


def test_parse_trace_line_unrelated_returns_none():
    from optio_host.download import _parse_trace_line
    assert _parse_trace_line(b"== Info: Trying 1.2.3.4...\n") is None
    assert _parse_trace_line(b"=> Send header, 123 bytes (0x7b)\n") is None
    assert _parse_trace_line(b"") is None
    assert _parse_trace_line(b"0000: GET /foo HTTP/1.1\n") is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_download.py -v
```

Expected: 4 new tests FAIL.

- [ ] **Step 3: Implement `_parse_trace_line`**

Append to `packages/optio-host/src/optio_host/download.py`:

```python
def _parse_trace_line(raw: bytes) -> tuple[str, int] | None:
    """Parse a single curl ``--trace-ascii -`` output line.

    Returns:
      ('length', N) on a content-length header line.
      ('recv', N) on a "<= Recv data, N bytes" line.
      None for any other line.

    The matcher is lowercase + prefix-based to tolerate header-name case
    variation and curl version drift on incidental fields.
    """
    if not raw:
        return None
    line = raw.decode("utf-8", errors="replace").strip().lower()
    # "0000: content-length: 1048576"
    cl_prefix = "0000: content-length:"
    if line.startswith(cl_prefix):
        value = line[len(cl_prefix):].strip()
        try:
            return ("length", int(value))
        except ValueError:
            return None
    # "<= recv data, 16384 bytes (0x4000)"
    if line.startswith("<= recv data,"):
        # Tokens: ['<=', 'recv', 'data,', 'N', 'bytes', ...]
        parts = line.split()
        if len(parts) >= 4:
            try:
                return ("recv", int(parts[3]))
            except ValueError:
                return None
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_download.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-host/src/optio_host/download.py packages/optio-host/tests/test_download.py
git commit -m "feat(optio-host): add _parse_trace_line helper"
```

---

### Task 7: Command builder helper (TDD)

Encapsulate the curl command construction so we can test it without running curl.

**Files:**
- Modify: `packages/optio-host/src/optio_host/download.py`
- Modify: `packages/optio-host/tests/test_download.py`

- [ ] **Step 1: Append failing tests**

```python
def test_build_curl_cmd_includes_required_flags():
    from optio_host.download import _build_curl_cmd
    cmd = _build_curl_cmd(url="https://example/foo bin", target="/tmp/out file")
    # Required flags, in any order:
    assert " --trace-ascii - " in cmd or cmd.endswith(" --trace-ascii -")
    assert " -s " in cmd or cmd.endswith(" -s")
    assert " -f " in cmd or cmd.endswith(" -f")
    assert " -L " in cmd or cmd.endswith(" -L")
    # URL and target both shell-quoted (single quotes around path with space).
    assert "'/tmp/out file'" in cmd
    assert "'https://example/foo bin'" in cmd
    # stdbuf prefix is present.
    assert cmd.startswith("stdbuf -oL curl ")


def test_build_curl_cmd_omits_stdbuf_when_unavailable(monkeypatch):
    """If shutil.which('stdbuf') returns None, command builder omits the prefix."""
    import shutil
    from optio_host import download as dl_mod
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "stdbuf" else "/usr/bin/" + name)
    cmd = dl_mod._build_curl_cmd(url="https://example/foo", target="/tmp/out")
    assert cmd.startswith("curl ")
    assert "stdbuf" not in cmd
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_download.py -v
```

Expected: 2 new tests FAIL.

- [ ] **Step 3: Implement `_build_curl_cmd`**

Append to `packages/optio-host/src/optio_host/download.py`:

```python
import shlex
import shutil


def _build_curl_cmd(*, url: str, target: str) -> str:
    """Build the shell command string that runs curl with progress trace.

    Output stream layout when run:
      - stdout: ``--trace-ascii -`` protocol trace lines.
      - stderr: curl's own error messages (used for ``DownloadFailed.stderr_tail``).
      - the response body is written to ``target`` directly (``-o``).

    ``stdbuf -oL`` is prefixed when available so trace lines flush
    promptly; when absent, parsing still works (just chunkier).
    """
    parts = [
        "curl",
        "--trace-ascii", "-",
        "-s",
        "-f",
        "-L",
        "-o", shlex.quote(target),
        shlex.quote(url),
    ]
    cmd = " ".join(parts)
    if shutil.which("stdbuf"):
        cmd = "stdbuf -oL " + cmd
    return cmd
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_download.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-host/src/optio_host/download.py packages/optio-host/tests/test_download.py
git commit -m "feat(optio-host): add _build_curl_cmd helper"
```

---

### Task 8: Implement `_execute` body — no-host branch (real curl, TDD)

**Files:**
- Modify: `packages/optio-host/src/optio_host/download.py`
- Modify: `packages/optio-host/tests/test_download.py`

This task wires the `_execute` closure for the simpler branch (host=None) and exercises it with a real local HTTP server and real curl.

- [ ] **Step 1: Add the test fixture and append the happy-path test**

Append to `packages/optio-host/tests/test_download.py`:

```python
import hashlib
import os
import socketserver
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


@pytest.fixture
def http_server(tmp_path):
    """Serve ``tmp_path/served/`` over a thread-backed HTTP server.

    Yields (base_url, served_dir). Caller writes files into served_dir, then
    fetches them via ``f"{base_url}/<filename>"``.
    """
    served = tmp_path / "served"
    served.mkdir()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(served), **kwargs)
        def log_message(self, format, *args):
            pass  # silence test output

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", served
    finally:
        httpd.shutdown()
        thread.join(timeout=2)


class _RecordingCtx:
    """Fake ProcessContext for execute-level tests. Records report_progress."""

    def __init__(self):
        import asyncio
        self.process_id = "p"
        self.cancellation_flag = asyncio.Event()
        self.progress = []  # list of (percent, message)

    def report_progress(self, percent, message=None):
        self.progress.append((percent, message))

    def should_continue(self) -> bool:
        return not self.cancellation_flag.is_set()


async def test_download_execute_no_host_happy_path(http_server, tmp_path):
    base_url, served = http_server
    # 4 MB random blob, deterministic via seed.
    blob = os.urandom(4 * 1024 * 1024)
    (served / "blob.bin").write_bytes(blob)
    expected_sha = hashlib.sha256(blob).hexdigest()

    from optio_host.download import create_download_task

    target = tmp_path / "out.bin"
    task = create_download_task(
        process_id="p.download-0",
        name="download blob.bin",
        url=f"{base_url}/blob.bin",
        target=str(target),
        host=None,
    )
    ctx = _RecordingCtx()
    await task.execute(ctx)

    # First message must be "Downloading blob.bin" with percent=None.
    assert ctx.progress[0] == (None, "Downloading blob.bin")
    # All subsequent entries must carry a non-None percent.
    numeric = [p for p, m in ctx.progress[1:] if p is not None]
    assert numeric, "expected at least one numeric progress report"
    # Monotonic non-decreasing.
    for a, b in zip(numeric, numeric[1:]):
        assert a <= b, f"percent went backwards: {a} -> {b}"
    # Ends at 100 (allow tiny FP slack).
    assert numeric[-1] >= 99.0
    # File on disk matches.
    assert target.exists()
    assert hashlib.sha256(target.read_bytes()).hexdigest() == expected_sha
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_download.py::test_download_execute_no_host_happy_path -v
```

Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement the no-host execute branch**

In `packages/optio-host/src/optio_host/download.py`, replace the placeholder `_execute` inside `create_download_task` with a real implementation. The new module body should contain (at module scope) the helpers below, and `_execute` becomes a thin closure that calls them.

Append/modify in `packages/optio-host/src/optio_host/download.py`:

```python
import asyncio
import os
from collections import deque


_STDERR_TAIL_CAP = 1024


async def _run_no_host(
    cmd: str,
) -> tuple[asyncio.subprocess.Process, asyncio.StreamReader, asyncio.StreamReader]:
    """Spawn ``cmd`` locally via sh -c, return (proc, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "sh", "-c", cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None and proc.stderr is not None
    return proc, proc.stdout, proc.stderr


async def _drain_stdout_trace(
    stream,  # asyncio.StreamReader or async iterator yielding bytes
    *,
    on_length,        # Callable[[int], None]
    on_recv,          # Callable[[int], None]
    should_continue,  # Callable[[], bool]
) -> None:
    """Read ``stream`` line by line, call callbacks for trace events.

    Stops early when ``should_continue()`` returns False.
    """
    while True:
        if not should_continue():
            return
        line = await _readline(stream)
        if not line:
            return  # EOF
        parsed = _parse_trace_line(line)
        if parsed is None:
            continue
        kind, value = parsed
        if kind == "length":
            on_length(value)
        elif kind == "recv":
            on_recv(value)


async def _readline(stream) -> bytes:
    """Read one newline-terminated chunk from an asyncio StreamReader OR an
    async iterator of bytes (the latter must yield line-sized chunks)."""
    if hasattr(stream, "readline"):
        return await stream.readline()
    # Fallback for iterators (ProcessHandle.stdout): accumulate to newline.
    buf = bytearray()
    try:
        async for chunk in stream:
            buf.extend(chunk)
            if b"\n" in chunk:
                break
    except StopAsyncIteration:
        pass
    return bytes(buf)


async def _drain_stderr_tail(stream, tail: deque) -> None:
    """Read stream chunks into ``tail`` (a deque of bytes), bounded by cap."""
    while True:
        if hasattr(stream, "read"):
            chunk = await stream.read(4096)
            if not chunk:
                return
        else:
            try:
                chunk = await stream.__anext__()
            except StopAsyncIteration:
                return
        tail.append(chunk)
        # Trim from the left until total <= cap.
        while sum(len(c) for c in tail) > _STDERR_TAIL_CAP and len(tail) > 1:
            tail.popleft()


async def _maybe_remove(host, target: str) -> None:
    """Best-effort cleanup of the target file. Errors swallowed."""
    try:
        if host is None:
            try:
                os.remove(target)
            except FileNotFoundError:
                pass
        else:
            await host.remove_file(target)
    except Exception:
        pass  # cleanup is best-effort
```

Then rewrite `create_download_task`'s inner `_execute` to use these helpers (no-host branch):

```python
def create_download_task(
    process_id: str,
    name: str,
    *,
    url: str,
    target: str,
    host: "Host | None" = None,
    description: str | None = None,
    cleanup_on_fail: bool = True,
) -> TaskInstance:
    async def _execute(ctx: Any) -> None:
        basename = os.path.basename(target) or target
        ctx.report_progress(None, f"Downloading {basename}")

        cmd = _build_curl_cmd(url=url, target=target)
        total = {"value": 0}
        received = {"value": 0}
        stderr_tail: deque = deque()

        def _on_length(n: int) -> None:
            total["value"] = n

        def _on_recv(n: int) -> None:
            received["value"] += n
            if total["value"] > 0:
                pct = min(100.0, received["value"] * 100.0 / total["value"])
                ctx.report_progress(pct, None)

        cancelled = False

        if host is None:
            proc, stdout_s, stderr_s = await _run_no_host(cmd)

            async def _stdout_task():
                await _drain_stdout_trace(
                    stdout_s,
                    on_length=_on_length,
                    on_recv=_on_recv,
                    should_continue=ctx.should_continue,
                )

            async def _stderr_task():
                await _drain_stderr_tail(stderr_s, stderr_tail)

            stdout_t = asyncio.create_task(_stdout_task())
            stderr_t = asyncio.create_task(_stderr_task())

            # Cancel watcher: poll ctx.should_continue while curl runs.
            async def _cancel_watcher():
                nonlocal cancelled
                while True:
                    if proc.returncode is not None:
                        return
                    if not ctx.should_continue():
                        cancelled = True
                        proc.terminate()
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            proc.kill()
                        return
                    await asyncio.sleep(0.05)

            watcher_t = asyncio.create_task(_cancel_watcher())
            exit_code = await proc.wait()
            await asyncio.gather(stdout_t, stderr_t, watcher_t, return_exceptions=True)
        else:
            # Host branch implemented in a later task.
            raise NotImplementedError("host branch not yet implemented")

        if cancelled:
            if cleanup_on_fail:
                await _maybe_remove(host, target)
            return  # framework writes 'cancelled' from cancel_flag
        if exit_code != 0:
            if cleanup_on_fail:
                await _maybe_remove(host, target)
            stderr_text = b"".join(stderr_tail).decode("utf-8", errors="replace")
            raise DownloadFailed(
                url=url, target=target,
                exit_code=exit_code, stderr_tail=stderr_text,
            )
        # success: nothing to do

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        cancellable=True,
        supports_resume=False,
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_download.py::test_download_execute_no_host_happy_path -v
```

Expected: PASS. (Requires `curl` on PATH; CI workers normally have it. If missing, install via `apt-get install -y curl` or document the requirement.)

- [ ] **Step 5: Commit**

```bash
git add packages/optio-host/src/optio_host/download.py packages/optio-host/tests/test_download.py
git commit -m "feat(optio-host): implement download _execute (no-host branch)"
```

---

### Task 9: 404 → DownloadFailed + cleanup (TDD)

**Files:**
- Modify: `packages/optio-host/tests/test_download.py`

The no-host branch already implements failure mapping. We just need to assert it.

- [ ] **Step 1: Append failing test**

```python
async def test_download_execute_no_host_404_raises_and_cleans_up(http_server, tmp_path):
    base_url, _ = http_server  # served dir is empty; any URL is 404

    from optio_host.download import DownloadFailed, create_download_task

    target = tmp_path / "out.bin"
    task = create_download_task(
        process_id="p.download-0",
        name="download nope.bin",
        url=f"{base_url}/nope.bin",
        target=str(target),
        host=None,
    )
    ctx = _RecordingCtx()
    with pytest.raises(DownloadFailed) as ei:
        await task.execute(ctx)
    assert ei.value.exit_code == 22
    assert ei.value.url == f"{base_url}/nope.bin"
    assert ei.value.target == str(target)
    assert ei.value.stderr_tail  # non-empty
    # Cleanup happened by default.
    assert not target.exists()


async def test_download_execute_no_host_404_no_cleanup_does_not_call_remove(
    http_server, tmp_path, monkeypatch,
):
    base_url, _ = http_server
    from optio_host import download as dl_mod
    from optio_host.download import DownloadFailed, create_download_task

    called = {"n": 0}

    async def spy_remove(host, target):
        called["n"] += 1

    monkeypatch.setattr(dl_mod, "_maybe_remove", spy_remove)

    target = tmp_path / "out.bin"
    task = create_download_task(
        process_id="p.download-0",
        name="download nope.bin",
        url=f"{base_url}/nope.bin",
        target=str(target),
        host=None,
        cleanup_on_fail=False,
    )
    ctx = _RecordingCtx()
    with pytest.raises(DownloadFailed):
        await task.execute(ctx)
    assert called["n"] == 0
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
python -m pytest tests/test_download.py::test_download_execute_no_host_404_raises_and_cleans_up tests/test_download.py::test_download_execute_no_host_404_no_cleanup_does_not_call_remove -v
```

Expected: both PASS (Task 8 implementation already covers this; if it doesn't, fix Task 8 before continuing).

- [ ] **Step 3: Commit**

```bash
git add packages/optio-host/tests/test_download.py
git commit -m "test(optio-host): download failure + cleanup-on-fail behavior"
```

---

### Task 10: Cancel mid-stream test (TDD)

**Files:**
- Modify: `packages/optio-host/tests/test_download.py`

- [ ] **Step 1: Append fixture and failing test**

```python
import time as _time
from http.server import BaseHTTPRequestHandler


@pytest.fixture
def slow_http_server(tmp_path):
    """Serve a 50MB blob throttled to ~64KB per chunk with 50ms sleeps."""
    served = tmp_path / "slow_served"
    served.mkdir()
    blob = os.urandom(50 * 1024 * 1024)
    (served / "big.bin").write_bytes(blob)

    class SlowHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.lstrip("/")
            file_path = served / path
            if not file_path.is_file():
                self.send_error(404)
                return
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            chunk = 64 * 1024
            for i in range(0, len(data), chunk):
                try:
                    self.wfile.write(data[i:i + chunk])
                    self.wfile.flush()
                except BrokenPipeError:
                    return
                _time.sleep(0.05)
        def log_message(self, format, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        thread.join(timeout=2)


async def test_download_execute_no_host_cancel_mid_stream(slow_http_server, tmp_path):
    from optio_host.download import create_download_task

    target = tmp_path / "out.bin"
    task = create_download_task(
        process_id="p.download-0",
        name="download big.bin",
        url=f"{slow_http_server}/big.bin",
        target=str(target),
        host=None,
    )
    ctx = _RecordingCtx()

    async def _run():
        await task.execute(ctx)

    run_t = asyncio.create_task(_run())
    # Wait until we see at least one numeric progress report, then cancel.
    for _ in range(200):
        if any(p is not None and p > 0 for p, _m in ctx.progress):
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("did not see any numeric progress before cancelling")

    start = _time.monotonic()
    ctx.cancellation_flag.set()
    await asyncio.wait_for(run_t, timeout=10.0)
    elapsed = _time.monotonic() - start

    # Curl should have been terminated promptly (well under 10s).
    assert elapsed < 8.0, f"cancel took too long: {elapsed:.1f}s"
    # No exception was raised (cancel branch returns normally).
    # Target absent (cleanup_on_fail True by default).
    assert not target.exists()
```

- [ ] **Step 2: Run test**

```bash
python -m pytest tests/test_download.py::test_download_execute_no_host_cancel_mid_stream -v
```

Expected: PASS (if Task 8 implementation correctly terminates the subprocess on cancel; if FAIL, fix the cancel watcher loop in `create_download_task`).

- [ ] **Step 3: Commit**

```bash
git add packages/optio-host/tests/test_download.py
git commit -m "test(optio-host): cancel mid-stream terminates curl promptly"
```

---

### Task 11: Implement `_execute` body — host branch (real curl via LocalHost, TDD)

**Files:**
- Modify: `packages/optio-host/src/optio_host/download.py`
- Modify: `packages/optio-host/tests/test_download.py`

- [ ] **Step 1: Append failing test using `LocalHost`**

```python
async def test_download_execute_host_happy_path_via_localhost(http_server, tmp_path):
    base_url, served = http_server
    blob = os.urandom(2 * 1024 * 1024)
    (served / "blob.bin").write_bytes(blob)
    expected_sha = hashlib.sha256(blob).hexdigest()

    from optio_host.host import LocalHost
    from optio_host.download import create_download_task

    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    host = LocalHost(taskdir=str(taskdir))
    await host.setup_workdir()  # creates taskdir/workdir
    # target = absolute path inside workdir
    target_abs = os.path.join(host.workdir, "out.bin")

    task = create_download_task(
        process_id="p.download-0",
        name="download blob.bin",
        url=f"{base_url}/blob.bin",
        target=target_abs,
        host=host,
    )
    ctx = _RecordingCtx()
    await task.execute(ctx)

    assert ctx.progress[0] == (None, "Downloading out.bin")
    numeric = [p for p, m in ctx.progress[1:] if p is not None]
    assert numeric
    for a, b in zip(numeric, numeric[1:]):
        assert a <= b
    assert numeric[-1] >= 99.0
    assert os.path.exists(target_abs)
    assert hashlib.sha256(Path(target_abs).read_bytes()).hexdigest() == expected_sha


async def test_download_execute_host_404_raises_and_cleans_up(http_server, tmp_path):
    base_url, _ = http_server
    from optio_host.host import LocalHost
    from optio_host.download import DownloadFailed, create_download_task

    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    host = LocalHost(taskdir=str(taskdir))
    await host.setup_workdir()
    target_abs = os.path.join(host.workdir, "nope.bin")

    task = create_download_task(
        process_id="p.download-0",
        name="download nope.bin",
        url=f"{base_url}/nope.bin",
        target=target_abs,
        host=host,
    )
    ctx = _RecordingCtx()
    with pytest.raises(DownloadFailed) as ei:
        await task.execute(ctx)
    assert ei.value.exit_code == 22
    assert not os.path.exists(target_abs)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_download.py::test_download_execute_host_happy_path_via_localhost tests/test_download.py::test_download_execute_host_404_raises_and_cleans_up -v
```

Expected: FAIL with `NotImplementedError("host branch not yet implemented")`.

- [ ] **Step 3: Replace the host-branch placeholder with a real implementation**

In `packages/optio-host/src/optio_host/download.py`, modify the `if host is None: ... else: raise NotImplementedError(...)` block inside `_execute`. Replace the `else` body with the following:

```python
        else:
            handle = await host.launch_subprocess(
                cmd, cwd=host.workdir, merge_stderr=False,
            )

            async def _stdout_task():
                await _drain_stdout_trace(
                    handle.stdout,
                    on_length=_on_length,
                    on_recv=_on_recv,
                    should_continue=ctx.should_continue,
                )

            async def _stderr_task():
                if handle.stderr is None:
                    return
                await _drain_stderr_tail(handle.stderr, stderr_tail)

            stdout_t = asyncio.create_task(_stdout_task())
            stderr_t = asyncio.create_task(_stderr_task())

            async def _cancel_watcher():
                nonlocal cancelled
                while True:
                    # ProcessHandle has no .returncode helper; rely on stream
                    # iterators completing as the cancel signal.
                    if not ctx.should_continue():
                        cancelled = True
                        await host.terminate_subprocess(handle, aggressive=False)
                        return
                    if stdout_t.done() and stderr_t.done():
                        return
                    await asyncio.sleep(0.05)

            watcher_t = asyncio.create_task(_cancel_watcher())
            # Wait for both streams to finish, then for the watcher to exit.
            await asyncio.gather(stdout_t, stderr_t, return_exceptions=True)
            watcher_t.cancel()
            # Pull the actual exit code from the underlying process.
            exit_code = await _host_proc_wait(handle)
```

Then append `_host_proc_wait` to `packages/optio-host/src/optio_host/download.py`:

```python
async def _host_proc_wait(handle) -> int:
    """Wait for the subprocess behind ``handle`` and return its exit code.

    Handles both LocalHost (asyncio.subprocess.Process) and RemoteHost
    (asyncssh.SSHClientProcess); both expose ``.wait()`` returning an int
    or ``.returncode``/``.exit_status`` after the streams close.
    """
    pid_like = handle.pid_like
    if hasattr(pid_like, "wait") and asyncio.iscoroutinefunction(pid_like.wait):
        return await pid_like.wait()
    # asyncssh: exit_status is populated once the channel closes.
    if hasattr(pid_like, "exit_status"):
        # Some asyncssh versions provide a coroutine `wait_closed`.
        if hasattr(pid_like, "wait_closed") and asyncio.iscoroutinefunction(pid_like.wait_closed):
            await pid_like.wait_closed()
        return int(pid_like.exit_status) if pid_like.exit_status is not None else -1
    raise RuntimeError(f"unable to determine exit code for {pid_like!r}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_download.py::test_download_execute_host_happy_path_via_localhost tests/test_download.py::test_download_execute_host_404_raises_and_cleans_up -v
```

Expected: both PASS.

- [ ] **Step 5: Run the full test file**

```bash
python -m pytest tests/test_download.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-host/src/optio_host/download.py packages/optio-host/tests/test_download.py
git commit -m "feat(optio-host): implement download _execute (host branch via launch_subprocess)"
```

---

### Task 12: Verify cancel works on host branch (TDD)

**Files:**
- Modify: `packages/optio-host/tests/test_download.py`

- [ ] **Step 1: Append failing test**

```python
async def test_download_execute_host_cancel_mid_stream(slow_http_server, tmp_path):
    from optio_host.host import LocalHost
    from optio_host.download import create_download_task

    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    host = LocalHost(taskdir=str(taskdir))
    await host.setup_workdir()
    target_abs = os.path.join(host.workdir, "out.bin")

    task = create_download_task(
        process_id="p.download-0",
        name="download big.bin",
        url=f"{slow_http_server}/big.bin",
        target=target_abs,
        host=host,
    )
    ctx = _RecordingCtx()

    run_t = asyncio.create_task(task.execute(ctx))
    for _ in range(200):
        if any(p is not None and p > 0 for p, _m in ctx.progress):
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("did not see any numeric progress before cancelling")

    start = _time.monotonic()
    ctx.cancellation_flag.set()
    await asyncio.wait_for(run_t, timeout=10.0)
    elapsed = _time.monotonic() - start

    assert elapsed < 8.0
    assert not os.path.exists(target_abs)
```

- [ ] **Step 2: Run test**

```bash
python -m pytest tests/test_download.py::test_download_execute_host_cancel_mid_stream -v
```

Expected: PASS (Task 11 cancel watcher should handle this; if FAIL, fix the watcher logic).

- [ ] **Step 3: Commit**

```bash
git add packages/optio-host/tests/test_download.py
git commit -m "test(optio-host): host-branch cancel terminates subprocess promptly"
```

---

### Task 13: Update `optio-host/AGENTS.md`

**Files:**
- Modify: `packages/optio-host/AGENTS.md`

- [ ] **Step 1: Read the current file**

```bash
cat /home/csillag/deai/optio/packages/optio-host/AGENTS.md
```

- [ ] **Step 2: Edit `packages/optio-host/AGENTS.md`**

Under the "Layers" section, append to the L0 description:

```
- `optio_host.download` — `create_download_task` factory + `DownloadFailed`
  exception. Drives `curl --trace-ascii -` via either `Host.launch_subprocess`
  (when a host is supplied) or `asyncio.create_subprocess_exec` (when not),
  parses byte-progress, supports cooperative cancel.
```

Under (or after) the existing layer documentation, add a "HookContext methods" sub-section if one does not exist, and list:

```
### HookContext methods (consumer surface)

- `await hook_ctx.copy_file(source, target, *, skip_if_unchanged=False)`
- `await hook_ctx.run_on_host(command, *, check=True, capture_stderr=False, cwd=None)`
- `await hook_ctx.read_from_host(path, *, silent=False)`
- `await hook_ctx.read_text_from_host(path, *, silent=False)`
- `await hook_ctx.download_file(url, target, *, description=None, cleanup_on_fail=True)`
  — Spawns a child task that downloads `url` to `target` on the same host.
  Reports one initial "Downloading <basename>" message then numeric percent
  updates. Workdir-relative / `~`-relative / absolute target paths supported
  (same rules as `copy_file`). Failure raises `RuntimeError` at the
  `run_child` boundary; the underlying `optio_host.DownloadFailed` lives in
  `status.error`. Parent cancel auto-propagates.
```

- [ ] **Step 3: Commit**

```bash
git add packages/optio-host/AGENTS.md
git commit -m "docs(optio-host): document download.py + HookContext.download_file"
```

---

### Task 14: Final full-suite verification

- [ ] **Step 1: Run the full `optio-host` test suite**

```bash
cd /home/csillag/deai/optio/packages/optio-host
python -m pytest tests/ -v
```

Expected: all tests PASS, including pre-existing context, host, archive, paths, and protocol tests.

- [ ] **Step 2: Run the full repo test suite (sanity)**

```bash
cd /home/csillag/deai/optio
# If a top-level Makefile target exists, prefer it. Otherwise, run pytest in
# each Python package that has tests.
ls packages/*/tests/ 2>/dev/null
# For each Python package with a tests/ directory:
for pkg in optio-core optio-host optio-opencode; do
  echo "=== $pkg ==="
  (cd packages/$pkg && python -m pytest tests/ -x -q) || break
done
```

Expected: all packages green. If something breaks unrelated to download (regression in protocol or context tests), STOP and inspect — do not commit a fix without analyzing.

- [ ] **Step 3: Manual smoke (optional)**

If a real opencode task body is available, manually invoke `await hook_ctx.download_file("https://example.com/some.bin", "downloads/some.bin")` and observe progress in the optio dashboard.

- [ ] **Step 4: Final commit if any cleanup needed**

If Step 2 surfaces no issues, no extra commit is needed.

---

## Self-Review

**Spec coverage:**

| Spec section | Plan task(s) |
|---|---|
| `optio_host/download.py` new module | Tasks 1, 2, 6, 7, 8, 11 |
| `DownloadFailed` exception | Task 1 |
| `create_download_task` factory + signature | Task 2 |
| Trace parsing (`_parse_trace_line`) | Task 6 |
| curl command builder (`_build_curl_cmd`) | Task 7 |
| `_execute` no-host branch | Task 8 |
| `_execute` host branch | Task 11 |
| Failure mapping → `DownloadFailed` | Task 9 |
| Cleanup-on-fail | Tasks 9 (no-host), 11 (host) |
| Cancel mid-stream behavior | Tasks 10, 12 |
| `HookContext.download_file` integration | Tasks 3, 4 |
| `HookContextProtocol` signature | Task 4 |
| Re-export from `optio_host` | Task 5 |
| AGENTS.md updates | Task 13 |
| End-to-end sanity | Task 14 |

**Placeholder scan:** None of the tasks contain "TBD", "TODO", "implement later", or unspecified code. Every code-step block contains the actual code.

**Type consistency:**
- `DownloadFailed` constructor uses keyword-only `url`, `target`, `exit_code`, `stderr_tail` consistently across Tasks 1, 8, 9, 11.
- `create_download_task` signature consistent across Tasks 2, 3, 7, 8, 11.
- `_parse_trace_line` return type `tuple[str, int] | None` consistent in Task 6 implementation and downstream callsites (`on_length`/`on_recv` dispatch in Task 8).
- `_RecordingCtx` API (`process_id`, `cancellation_flag`, `progress`, `report_progress`, `should_continue`) used identically across Tasks 8, 9, 10, 11, 12.

**Known limitation surfaced once:** child-failure-type-loss is documented in `DownloadFailed`'s docstring (Task 1) and in `HookContext.download_file`'s docstring (Task 3); points to `/tmp/optio-child-failure-problem.md`.

**System dependencies:** curl, optionally stdbuf, both already required by existing opencode install path. No new dependency footprint.

---

## Open items not in scope of this plan

- Cross-task fix for the run_child failure-type-loss (separate effort; problem brief at `/tmp/optio-child-failure-problem.md`).
- Optio-demo end-to-end exercise (user has a specific consumer in mind; will be planned separately).
- Optional curl features (resume `-C -`, custom headers, timeouts, TLS bypass) — future work.
