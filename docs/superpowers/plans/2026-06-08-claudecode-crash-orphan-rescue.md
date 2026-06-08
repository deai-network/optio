# Claudecode Crash-Orphan Rescue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a claudecode resume detect a crash-surviving tmux/ttyd/claude orphan, harvest its live state into a fresh snapshot, and kill it — all before the destructive workdir wipe — so unsaved work is recovered instead of bulldozed.

**Architecture:** A new caller-side startup bracket in `run_claudecode_session` runs before the protocol driver (hence before `setup_workdir`'s wipe). It probes the deterministic per-task tmux socket; on a hit it writes a durable marker, kills the orphan tree via an extracted-and-reused teardown helper, captures the now-static workdir via the existing `_capture_snapshot`, then clears the marker. The unchanged resume path (latest-snapshot-wins) restores the fresh capture.

**Tech Stack:** Python 3, asyncio, pytest / pytest-asyncio, tmux, ttyd, MongoDB/GridFS (snapshots). Package: `packages/optio-claudecode` in `~/deai/optio`. Driver lives in `packages/optio-agents`.

**Spec:** `docs/superpowers/specs/2026-06-08-claudecode-crash-orphan-rescue-design.md` (base revision `dd4906385b72ed3544a0738f64ff4c64e3cbe6b6`, branch `csillag/claudecode-crash-rescue`).

**Working directory for all commands:** `~/deai/optio`. Run tests with the repo's configured runner, e.g. `python -m pytest packages/optio-claudecode/tests/...`.

---

## File Structure

- **Modify** `packages/optio-claudecode/src/optio_claudecode/host_actions.py`
  - Add `_kill_ttyd_by_socket(host, socket_path)` — anchored host-side pkill reaping a detached orphan ttyd by the socket path in its cmdline.
  - Add `teardown_session_tree(host, *, tmux_path, tmux_socket, tmux_session, claude_path, ttyd_handle=None, aggressive)` — the 4-step kill sequence, extracted from the session `finally` block, with a handle-or-orphan ttyd branch.
  - Reword two pasta comments (accuracy cleanup).
- **Modify** `packages/optio-claudecode/src/optio_claudecode/session.py`
  - Replace the inline `finally` kill block (steps 1-4, ~lines 361-395) with a single `teardown_session_tree(...)` call (`ttyd_handle=launched_handle`).
  - Add `_rescue_orphan_if_present(ctx, host, config)` and call it in `run_claudecode_session` after `host.connect()`, gated on resume, before `run_log_protocol_session`.
- **Create** `packages/optio-claudecode/tests/test_kill_ttyd_by_socket.py`
- **Create** `packages/optio-claudecode/tests/test_teardown_session_tree.py`
- **Create** `packages/optio-claudecode/tests/test_rescue_orphan.py`

Test style follows the existing lightweight stub-`_Host` pattern in `tests/test_await_claude_gone.py`: a stub whose `run_command` records command strings and returns a canned result object; helpers are verified by asserting the emitted commands.

---

## Task 1: `_kill_ttyd_by_socket` helper

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py`
- Test: `packages/optio-claudecode/tests/test_kill_ttyd_by_socket.py`

- [ ] **Step 1: Write the failing test**

Create `packages/optio-claudecode/tests/test_kill_ttyd_by_socket.py`:

```python
import pytest

import optio_claudecode.host_actions as H


class _Result:
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.stderr = ""
        self.exit_code = 0


class _Host:
    def __init__(self):
        self.commands = []

    async def run_command(self, cmd, **kwargs):
        self.commands.append(cmd)
        return _Result()


@pytest.mark.asyncio
async def test_kill_ttyd_by_socket_anchored_pkill():
    host = _Host()
    await H._kill_ttyd_by_socket(host, "/tmp/optio-cc-deadbeef0badcafe.sock")
    assert len(host.commands) == 1
    cmd = host.commands[0]
    # Targets ttyd processes by the socket path they carry in their cmdline.
    assert "pkill" in cmd
    assert "/tmp/optio-cc-deadbeef0badcafe.sock" in cmd
    # Anchored so the rescue's own command line is not matched (mirrors the
    # [c]laude self-match guard used for claude).
    assert "optio-cc-deadbeef0badcafe.sock" in cmd
    # Best-effort: never fails the caller when nothing matches.
    assert "|| true" in cmd


@pytest.mark.asyncio
async def test_kill_ttyd_by_socket_does_not_self_match():
    # The emitted pattern must contain a bracket-escape so pkill -f does not
    # match its own argv. We assert the socket digest is bracket-split.
    host = _Host()
    await H._kill_ttyd_by_socket(host, "/tmp/optio-cc-abc123.sock")
    cmd = host.commands[0]
    assert "[" in cmd and "]" in cmd
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/optio-claudecode/tests/test_kill_ttyd_by_socket.py -v`
Expected: FAIL — `AttributeError: module 'optio_claudecode.host_actions' has no attribute '_kill_ttyd_by_socket'`.

- [ ] **Step 3: Write minimal implementation**

In `host_actions.py`, near `kill_claude_processes` / `_claude_pgrep_pattern`, add:

```python
def _socket_pkill_pattern(socket_path: str) -> str:
    """Anchored pkill -f pattern matching processes that carry ``socket_path``
    in their cmdline (the orphan ttyd + its attach wrapper), bracket-escaped on
    the first char so pkill's own argv does not self-match — same trick as
    ``_claude_pgrep_pattern``'s ``[c]laude``."""
    if not socket_path:
        return socket_path
    return "[" + socket_path[0] + "]" + socket_path[1:]


async def _kill_ttyd_by_socket(host: "Host", socket_path: str) -> None:
    """Reap a detached orphan ttyd that has no tracked launch handle.

    Normal teardown kills ttyd via ``terminate_subprocess(launched_handle)``.
    A crash orphan's ttyd is re-parented to init with no handle, so it is
    reaped host-side by an anchored ``pkill -f`` on the private socket path it
    carries in its cmdline. Best-effort: pkill exits non-zero when nothing
    matches."""
    pattern = _socket_pkill_pattern(socket_path)
    await host.run_command(f"pkill -KILL -f {shlex.quote(pattern)} || true")
```

(`shlex` is already imported in `host_actions.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest packages/optio-claudecode/tests/test_kill_ttyd_by_socket.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
cd ~/deai/optio
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py \
        packages/optio-claudecode/tests/test_kill_ttyd_by_socket.py
git commit -m "feat(claudecode): _kill_ttyd_by_socket — reap a handle-less orphan ttyd"
```

---

## Task 2: `teardown_session_tree` extracted helper

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py`
- Test: `packages/optio-claudecode/tests/test_teardown_session_tree.py`

- [ ] **Step 1: Write the failing test**

Create `packages/optio-claudecode/tests/test_teardown_session_tree.py`:

```python
import pytest

import optio_claudecode.host_actions as H


@pytest.fixture
def calls(monkeypatch):
    rec = []

    async def _kill_ttyd(host, socket):
        rec.append(("ttyd_socket", socket))

    async def _kill_session(host, tmux_path, socket, session):
        rec.append(("kill_session", session))

    async def _kill_claude(host, claude_path, **kw):
        rec.append(("kill_claude", claude_path))

    async def _await_gone(host, claude_path, **kw):
        rec.append(("await_gone", claude_path))
        return True

    monkeypatch.setattr(H, "_kill_ttyd_by_socket", _kill_ttyd)
    monkeypatch.setattr(H, "_kill_tmux_session", _kill_session)
    monkeypatch.setattr(H, "kill_claude_processes", _kill_claude)
    monkeypatch.setattr(H, "await_claude_gone", _await_gone)
    return rec


class _FakeHandle:
    pass


class _Host:
    def __init__(self):
        self.terminated = []

    async def terminate_subprocess(self, handle, *, aggressive):
        self.terminated.append((handle, aggressive))


@pytest.mark.asyncio
async def test_orphan_branch_uses_kill_ttyd_by_socket(calls):
    host = _Host()
    await H.teardown_session_tree(
        host, tmux_path="tmux", tmux_socket="/tmp/s.sock",
        tmux_session="optio", claude_path="/w/home/.local/bin/claude",
        ttyd_handle=None, aggressive=True,
    )
    # All four steps, in order; orphan ttyd path; no terminate_subprocess.
    assert [c[0] for c in calls] == [
        "ttyd_socket", "kill_session", "kill_claude", "await_gone",
    ]
    assert host.terminated == []


@pytest.mark.asyncio
async def test_handle_branch_uses_terminate_subprocess(calls):
    host = _Host()
    handle = _FakeHandle()
    await H.teardown_session_tree(
        host, tmux_path="tmux", tmux_socket="/tmp/s.sock",
        tmux_session="optio", claude_path="/w/home/.local/bin/claude",
        ttyd_handle=handle, aggressive=False,
    )
    assert host.terminated == [(handle, False)]
    # ttyd-by-socket NOT used when a handle is present.
    assert [c[0] for c in calls] == ["kill_session", "kill_claude", "await_gone"]


@pytest.mark.asyncio
async def test_steps_are_best_effort(calls, monkeypatch):
    # A failure in one step does not abort the rest.
    async def _boom(host, socket):
        raise RuntimeError("ttyd kill blew up")

    monkeypatch.setattr(H, "_kill_ttyd_by_socket", _boom)
    host = _Host()
    # Should not raise.
    await H.teardown_session_tree(
        host, tmux_path="tmux", tmux_socket="/tmp/s.sock",
        tmux_session="optio", claude_path="/w/home/.local/bin/claude",
        ttyd_handle=None, aggressive=True,
    )
    # The remaining three steps still ran.
    assert [c[0] for c in calls] == ["kill_session", "kill_claude", "await_gone"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/optio-claudecode/tests/test_teardown_session_tree.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'teardown_session_tree'`.

- [ ] **Step 3: Write minimal implementation**

In `host_actions.py`, after `await_claude_gone`, add:

```python
async def teardown_session_tree(
    host: "Host",
    *,
    tmux_path: str,
    tmux_socket: str,
    tmux_session: str,
    claude_path: str,
    ttyd_handle: "ProcessHandle | None" = None,
    aggressive: bool,
) -> None:
    """Kill a full claudecode session tree (ttyd + tmux + claude), reused by
    both normal teardown and crash-orphan rescue.

    Four best-effort steps, each isolated so one failure does not abort the
    rest:
      1. ttyd — via the tracked launch handle (normal teardown) or, when no
         handle exists (a crash orphan re-parented to init), an anchored
         host-side pkill on the socket path.
      2. ``kill-session`` — SIGHUPs the tmux pane.
      3. ``kill_claude_processes`` — claude ignores the pane SIGHUP (and may
         run under a pasta netns wrapper), so it is killed explicitly via an
         anchored host-side pkill on its argv[0]; this reaches it whether or
         not pasta wraps it (pasta isolates the network namespace, not PID).
      4. ``await_claude_gone`` — waits for quiescence so a subsequent capture
         tar does not race a dying claude."""
    if ttyd_handle is not None:
        try:
            await host.terminate_subprocess(ttyd_handle, aggressive=aggressive)
        except Exception:
            _LOG.exception("terminate_subprocess (ttyd) failed")
    else:
        try:
            await _kill_ttyd_by_socket(host, tmux_socket)
        except Exception:
            _LOG.exception("orphan ttyd reap failed (socket=%s)", tmux_socket)

    try:
        await _kill_tmux_session(host, tmux_path, tmux_socket, tmux_session)
    except Exception:
        _LOG.exception("tmux session teardown failed")

    try:
        await kill_claude_processes(host, claude_path)
    except Exception:
        _LOG.exception("kill_claude_processes failed")

    try:
        await await_claude_gone(host, claude_path)
    except Exception:
        _LOG.exception("await_claude_gone failed; proceeding")
```

Confirm `ProcessHandle` is importable in `host_actions.py`; if it is not already imported, add it to the existing typing imports from the host module (same source `session.py` imports `ProcessHandle` from). If unavailable, type the param as `object | None`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest packages/optio-claudecode/tests/test_teardown_session_tree.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
cd ~/deai/optio
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py \
        packages/optio-claudecode/tests/test_teardown_session_tree.py
git commit -m "feat(claudecode): extract teardown_session_tree (handle + orphan ttyd branches)"
```

---

## Task 3: Refactor the session `finally` to reuse `teardown_session_tree`

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py` (the `finally` block, ~lines 361-395)

This is a pure refactor: the teardown behavior must not change. Existing teardown/cancel tests are the guard.

- [ ] **Step 1: Run the existing teardown tests to establish a green baseline**

Run: `python -m pytest packages/optio-claudecode/tests/test_session_hooks.py packages/optio-claudecode/tests/test_tmux_persistence.py packages/optio-claudecode/tests/test_await_claude_gone.py -v`
Expected: PASS (record the count).

- [ ] **Step 2: Replace the inline kill block**

In `session.py`, replace the inline steps in the `finally` (the `terminate_subprocess` call, the `_kill_tmux_session` block, and the `kill_claude_processes` + `await_claude_gone` block — roughly lines 361-395) with a single guarded call. Keep the surrounding `_trace` lines and the `cred_watch_task`/lease/save-back logic that follows untouched.

```python
        if (
            launched_handle is not None
            and tmux_path is not None
            and tmux_socket is not None
            and tmux_session is not None
            and claude_path
        ):
            _trace("finally: teardown_session_tree START aggressive=%s", cancelled)
            try:
                await host_actions.teardown_session_tree(
                    host,
                    tmux_path=tmux_path,
                    tmux_socket=tmux_socket,
                    tmux_session=tmux_session,
                    claude_path=claude_path,
                    ttyd_handle=launched_handle,
                    aggressive=cancelled,
                )
            except Exception:
                _LOG.exception("teardown_session_tree failed")
            _trace("finally: teardown_session_tree DONE")
```

Note on a behavior-preserving subtlety: the old code called `terminate_subprocess` whenever `launched_handle is not None` even if tmux fields were None, then `kill_claude_processes` only when `launched_handle and claude_path`. In practice `launched_handle`, `tmux_*`, and `claude_path` are all set together at a successful launch (handle is assigned strictly after launch; tmux fields and claude_path are set in the same body). The combined guard above therefore covers the same real states. If a reviewer wants byte-identical guards, split the ttyd kill out — but the combined guard is correct given the launch ordering and is preferred for clarity.

- [ ] **Step 3: Run the teardown tests again — must still pass**

Run: `python -m pytest packages/optio-claudecode/tests/test_session_hooks.py packages/optio-claudecode/tests/test_tmux_persistence.py packages/optio-claudecode/tests/test_await_claude_gone.py -v`
Expected: PASS, same count as Step 1.

- [ ] **Step 4: Run the full claudecode suite to catch regressions**

Run: `python -m pytest packages/optio-claudecode/tests/ -q`
Expected: PASS (pre-existing skips/xfails unchanged).

- [ ] **Step 5: Commit**

```bash
cd ~/deai/optio
git add packages/optio-claudecode/src/optio_claudecode/session.py
git commit -m "refactor(claudecode): teardown finally reuses teardown_session_tree"
```

---

## Task 4: `_rescue_orphan_if_present` bracket + wiring

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py`
- Test: `packages/optio-claudecode/tests/test_rescue_orphan.py`

The rescue derives everything pre-driver and is a no-op unless an orphan (live session) or a leftover marker is found.

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-claudecode/tests/test_rescue_orphan.py`:

```python
import pytest

import optio_claudecode.session as S


class _Result:
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.stderr = ""
        self.exit_code = 0


class _Host:
    """Stub host. ``existing`` is a set of marker paths reported present."""

    def __init__(self, workdir, existing=None):
        self.workdir = workdir
        self.existing = set(existing or ())
        self.written = []
        self.removed = []
        self.commands = []

    async def run_command(self, cmd, **kwargs):
        self.commands.append(cmd)
        # Emulate `test -e <path> && echo YES || true` marker probes.
        if cmd.startswith("test -e "):
            path = cmd.split("test -e ", 1)[1].split(" ", 1)[0].strip("'\"")
            return _Result("YES\n" if path in self.existing else "")
        if cmd.startswith("rm -f "):
            path = cmd.split("rm -f ", 1)[1].strip().strip("'\"")
            self.removed.append(path)
            self.existing.discard(path)
        return _Result()

    async def write_text(self, rel, text):
        self.written.append(rel)
        self.existing.add(f"{self.workdir.rstrip('/')}/{rel}")


class _Config:
    workdir_exclude = None
    session_blob_encrypt = None


class _Ctx:
    process_id = "pid-1"
    resume = True


@pytest.fixture
def patched(monkeypatch):
    rec = {"alive": False, "teardown": [], "capture": [], "fail_capture": False}

    async def _alive(host, tmux_path, socket, session):
        return rec["alive"]

    async def _require_tmux(host):
        return "tmux"

    def _socket(host):
        return "/tmp/optio-cc-deadbeef.sock"

    async def _teardown(host, **kw):
        rec["teardown"].append(kw)

    async def _capture(ctx, host, **kw):
        rec["capture"].append(kw)
        if rec["fail_capture"]:
            raise RuntimeError("capture failed")

    monkeypatch.setattr(S.host_actions, "tmux_session_alive", _alive)
    monkeypatch.setattr(S.host_actions, "_require_tmux", _require_tmux)
    monkeypatch.setattr(S.host_actions, "_tmux_socket_path", _socket)
    monkeypatch.setattr(S.host_actions, "teardown_session_tree", _teardown)
    monkeypatch.setattr(S, "_capture_snapshot", _capture)
    return rec


@pytest.mark.asyncio
async def test_noop_when_no_session_and_no_marker(patched, tmp_path):
    patched["alive"] = False
    host = _Host(str(tmp_path))
    await S._rescue_orphan_if_present(_Ctx(), host, _Config())
    assert patched["teardown"] == []
    assert patched["capture"] == []
    assert host.written == []


@pytest.mark.asyncio
async def test_triggers_on_live_session_kill_before_capture(patched, tmp_path):
    patched["alive"] = True
    host = _Host(str(tmp_path))
    await S._rescue_orphan_if_present(_Ctx(), host, _Config())
    # Marker written, orphan killed, then captured — kill strictly before capture.
    assert host.written == [".optio-rescue-pending"]
    assert len(patched["teardown"]) == 1
    assert patched["teardown"][0]["ttyd_handle"] is None
    assert len(patched["capture"]) == 1
    # end_state marks the rescue for forensics.
    assert patched["capture"][0]["end_state"] == "rescued"
    # Marker excluded from the snapshot so a restored workdir does not loop.
    assert ".optio-rescue-pending" in (patched["capture"][0]["workdir_exclude"] or [])
    # Marker cleared after capture success.
    marker = f"{str(tmp_path).rstrip('/')}/.optio-rescue-pending"
    assert marker in host.removed


@pytest.mark.asyncio
async def test_triggers_on_marker_even_without_session(patched, tmp_path):
    patched["alive"] = False
    marker = f"{str(tmp_path).rstrip('/')}/.optio-rescue-pending"
    host = _Host(str(tmp_path), existing={marker})
    await S._rescue_orphan_if_present(_Ctx(), host, _Config())
    # Detect-by-marker path still rescues (mid-rescue retry).
    assert len(patched["teardown"]) == 1
    assert len(patched["capture"]) == 1


@pytest.mark.asyncio
async def test_capture_failure_persists_marker_and_reraises(patched, tmp_path):
    patched["alive"] = True
    patched["fail_capture"] = True
    host = _Host(str(tmp_path))
    with pytest.raises(RuntimeError):
        await S._rescue_orphan_if_present(_Ctx(), host, _Config())
    marker = f"{str(tmp_path).rstrip('/')}/.optio-rescue-pending"
    # Marker NOT removed — a retried resume re-enters rescue.
    assert marker not in host.removed


@pytest.mark.asyncio
async def test_skipped_when_not_resuming(patched, tmp_path):
    patched["alive"] = True

    class _FreshCtx:
        process_id = "pid-1"
        resume = False

    host = _Host(str(tmp_path))
    await S._rescue_orphan_if_present(_FreshCtx(), host, _Config())
    assert patched["teardown"] == []
    assert patched["capture"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest packages/optio-claudecode/tests/test_rescue_orphan.py -v`
Expected: FAIL — `AttributeError: module 'optio_claudecode.session' has no attribute '_rescue_orphan_if_present'`.

- [ ] **Step 3: Implement `_rescue_orphan_if_present`**

In `session.py`, add (module level, near `_capture_snapshot`):

```python
_RESCUE_MARKER = ".optio-rescue-pending"


def _claude_bin_path(host: "Host") -> str:
    """Deterministic launch path of claude inside the isolated HOME."""
    return f"{host.workdir.rstrip('/')}/home/.local/bin/claude"


async def _marker_present(host: "Host", marker_path: str) -> bool:
    r = await host.run_command(
        f"test -e {shlex.quote(marker_path)} && echo YES || true"
    )
    return "YES" in r.stdout


async def _rescue_orphan_if_present(
    ctx: ProcessContext, host: Host, config: ClaudeCodeTaskConfig,
) -> None:
    """Before the driver wipes the workdir, recover a crash-surviving orphan.

    A non-graceful host death (disk-full, OOM, power loss) leaves the
    tmux/ttyd/claude sub-tree running, re-parented to init, with unsaved state
    on disk — but no snapshot. This bracket, run before
    ``run_log_protocol_session`` (hence before ``setup_workdir``), detects that
    orphan on the deterministic per-task socket, kills it, and captures its
    live state into a fresh snapshot that the unchanged resume path then
    restores. No-op unless an orphan (or a leftover rescue marker) is found.

    Kill-before-capture is deliberate: a dead, static workdir prevents a live
    claude from repopulating ``home/.claude`` into the plaintext workdir blob
    after the expunge, and yields a race-free tar. See spec decisions D3/D4."""
    if not config.supports_resume:
        return
    if not bool(getattr(ctx, "resume", False)):
        return

    socket = host_actions._tmux_socket_path(host)
    session = "optio"
    marker_path = f"{host.workdir.rstrip('/')}/{_RESCUE_MARKER}"

    tmux_path = await host_actions._require_tmux(host)
    alive = await host_actions.tmux_session_alive(
        host, tmux_path, socket, session,
    )
    if not alive and not await _marker_present(host, marker_path):
        return  # normal resume; nothing to rescue

    _LOG.warning(
        "crash-orphan rescue: live=%s socket=%s — capturing live state before wipe",
        alive, socket,
    )

    # 1. Durable marker (retry guard: kill removes the has-session signal).
    await host.write_text(_RESCUE_MARKER, "")

    # 2. Kill the orphan tree (handle-less: orphan ttyd reaped by socket).
    claude_path = _claude_bin_path(host)
    await host_actions.teardown_session_tree(
        host,
        tmux_path=tmux_path,
        tmux_socket=socket,
        tmux_session=session,
        claude_path=claude_path,
        ttyd_handle=None,
        aggressive=True,
    )

    # 3. Capture the now-static workdir — identical artifacts to a normal
    #    teardown capture. Exclude the marker so a restored workdir cannot
    #    re-trigger rescue in a loop.
    exclude = [*(config.workdir_exclude or []), _RESCUE_MARKER]
    await _capture_snapshot(
        ctx, host,
        end_state="rescued",
        workdir_exclude=exclude,
        session_blob_encrypt=config.session_blob_encrypt,
    )

    # 4. Capture durable — clear the marker.
    await host.run_command(f"rm -f {shlex.quote(marker_path)}")
    _LOG.warning("crash-orphan rescue: fresh snapshot captured; orphan killed")
```

Ensure `shlex` is imported in `session.py` (it is — used by `_archive_home_claude`). Ensure `ClaudeCodeTaskConfig` and `host_actions` are already imported at module scope (they are).

- [ ] **Step 4: Run the rescue tests to verify they pass**

Run: `python -m pytest packages/optio-claudecode/tests/test_rescue_orphan.py -v`
Expected: PASS (all five).

- [ ] **Step 5: Wire the bracket into `run_claudecode_session`**

In `session.py`, in `run_claudecode_session`, immediately after `await host.connect()` and before the `try:` that wraps `run_log_protocol_session`, add:

```python
    # Crash-orphan rescue: if a non-graceful host death left this task's
    # tmux/ttyd/claude tree running with unsaved state, harvest it into a fresh
    # snapshot and kill it BEFORE the driver wipes the workdir. No-op otherwise.
    await _rescue_orphan_if_present(ctx, config=config, host=host)
```

(If a capture failure raises here, the resume aborts with the marker left in place — the intended retry-safe behavior.)

- [ ] **Step 6: Run the full claudecode suite**

Run: `python -m pytest packages/optio-claudecode/tests/ -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd ~/deai/optio
git add packages/optio-claudecode/src/optio_claudecode/session.py \
        packages/optio-claudecode/tests/test_rescue_orphan.py
git commit -m "feat(claudecode): rescue crash-surviving orphan on resume before wipe"
```

---

## Task 5: pasta comment-accuracy cleanup

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py` (the comment near the old kill block, if it survived the Task 3 refactor)
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py:~684` (`kill_claude_processes` docstring)

No behavior change; documentation accuracy only.

- [ ] **Step 1: Reword the host_actions docstring**

In `host_actions.py`, in `kill_claude_processes`, change the line that states claude "runs under pasta" unconditionally to reflect that pasta is conditional and the kill is pasta-agnostic. Replace the sentence beginning "Teardown SIGKILLs ttyd ... but claude runs under pasta ..." with:

```python
    """Kill the per-task claude via an anchored host-side ``pkill``.

    claude ignores the tmux pane SIGHUP, and MAY run under a pasta netns
    wrapper (only when ``OPTIO_CLAUDECODE_NETNS`` is set AND the host is
    local). The anchored pkill on claude's argv[0] reaches it regardless of
    whether pasta wraps it, because pasta isolates the network namespace, not
    PID. Best-effort: pkill exits non-zero when nothing matches."""
```

(Preserve the existing pattern/command lines below the docstring unchanged.)

- [ ] **Step 2: Reword any surviving session.py comment**

If the Task 3 refactor removed the `# claude runs under pasta in its own process group ...` comment (it was attached to the inline block), nothing to do here. If a similar comment remains anywhere in `session.py`, reword "claude runs under pasta" to "claude may run under pasta (OPTIO_CLAUDECODE_NETNS + local mode); the anchored host-side pkill reaches it regardless".

- [ ] **Step 3: Verify no unconditional pasta claim remains**

Run: `grep -rn "runs under pasta" packages/optio-claudecode/src/`
Expected: no matches (all reworded to "may run under pasta" / pasta-agnostic phrasing).

- [ ] **Step 4: Run the suite (sanity — comments only)**

Run: `python -m pytest packages/optio-claudecode/tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/deai/optio
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py \
        packages/optio-claudecode/src/optio_claudecode/session.py
git commit -m "docs(claudecode): pasta is conditional; kill is pasta-agnostic"
```

---

## Task 6: Integration test — orphan survival → resume → rescue

**Files:**
- Test: `packages/optio-claudecode/tests/test_rescue_orphan.py` (add an integration-style test using the shim binaries + `LocalHost`)

This exercises the real `teardown_session_tree` + `_capture_snapshot` against a real local tmux/ttyd shim tree, asserting the orphan is gone and a fresh `"rescued"` snapshot exists. It uses the existing `shim_install_dir` / `mongo_db` / `ctx_and_captures` fixtures (see `conftest.py`) the same way `test_tmux_persistence.py` and `test_session_local.py` do.

- [ ] **Step 1: Study an existing end-to-end test for the launch/snapshot harness**

Read `packages/optio-claudecode/tests/test_tmux_persistence.py` and `packages/optio-claudecode/tests/test_session_resume.py` to copy their exact fixture wiring (how they build a `LocalHost` with the shims, launch a detached session, and assert on snapshots in `mongo_db`). Reuse that scaffolding verbatim — do not invent a new harness.

- [ ] **Step 2: Write the integration test**

Add to `test_rescue_orphan.py` (adapt fixture names to match what Step 1 found; the assertions below are the contract):

```python
@pytest.mark.asyncio
async def test_rescue_end_to_end_kills_orphan_and_snapshots(
    shim_install_dir, mongo_db, tmp_workdir, ctx_and_captures,
):
    """Launch a real (shim) detached session, abandon it so it becomes an
    orphan, then run _rescue_orphan_if_present and assert: the tmux session is
    gone, no claude shim remains, and a fresh 'rescued' snapshot was inserted."""
    import optio_claudecode.host_actions as H
    from optio_claudecode.snapshots import load_latest_snapshot
    from optio_host.host import LocalHost

    # (a) Build a LocalHost at tmp_workdir with home/.claude/.credentials.json
    #     present (so the capture credentials-guard passes), and a claude shim
    #     installed at home/.local/bin/claude. Copy the scaffolding from the
    #     test identified in Step 1.
    # (b) Launch a detached session via launch_ttyd_with_claude(...) and confirm
    #     tmux_session_alive(...) is True.
    # (c) Simulate the crash: drop all in-process handles WITHOUT calling
    #     teardown (just discard the returned handle). The tmux/ttyd/claude
    #     tree keeps running — that is the orphan.
    # (d) Run the rescue against a fresh ctx whose .resume is True and whose
    #     process_id matches the launch, with supports_resume config True.
    # (e) Assertions:
    ctx = ctx_and_captures.ctx  # adapt to the fixture's actual attribute
    host = LocalHost(taskdir=tmp_workdir)  # adapt: same workdir as the launch

    # ... launch + abandon (steps a-c) ...

    await S._rescue_orphan_if_present(ctx, host, config)  # config: supports_resume=True

    tmux_path = await H._require_tmux(host)
    socket = H._tmux_socket_path(host)
    assert (await H.tmux_session_alive(host, tmux_path, socket, "optio")) is False

    snap = await load_latest_snapshot(
        ctx._db, prefix=ctx._prefix, process_id=ctx.process_id,
    )
    assert snap is not None
    assert snap.get("endState") == "rescued"  # adapt key to snapshots schema

    # Marker cleared on success.
    r = await host.run_command(
        f"test -e {tmp_workdir.rstrip('/')}/.optio-rescue-pending && echo Y || true"
    )
    assert "Y" not in r.stdout
```

If the snapshot document key for the end-state is not `endState`, inspect `insert_snapshot` in `snapshots.py` and use the actual field name. If launching a real shim session proves environment-fragile in CI, mark this single test `@pytest.mark.integration` (or the repo's existing slow/integration marker) so unit CI stays fast, and document the manual run command in the test docstring.

- [ ] **Step 3: Run the integration test**

Run: `python -m pytest packages/optio-claudecode/tests/test_rescue_orphan.py -v`
Expected: PASS, including the end-to-end test (orphan gone, fresh `rescued` snapshot present, marker cleared).

- [ ] **Step 4: Run the full claudecode suite once more**

Run: `python -m pytest packages/optio-claudecode/tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/deai/optio
git add packages/optio-claudecode/tests/test_rescue_orphan.py
git commit -m "test(claudecode): end-to-end crash-orphan rescue (orphan killed, fresh snapshot)"
```

---

## Final verification

- [ ] Run the whole claudecode test suite: `python -m pytest packages/optio-claudecode/tests/ -q` — all green.
- [ ] `grep -rn "runs under pasta" packages/optio-claudecode/src/` — no unconditional claims remain.
- [ ] Confirm both teardown and rescue route through `teardown_session_tree` (no second inline kill path): `grep -rn "kill_claude_processes\|_kill_tmux_session\|terminate_subprocess" packages/optio-claudecode/src/optio_claudecode/session.py` — these should now appear only inside `host_actions`, not inline in `session.py`'s `finally`.
- [ ] Spec coverage recheck: probe (T4), capture-identical-to-normal (T4 uses `_capture_snapshot`), kill-first (T4 ordering test), marker retry-safety (T4 failure test), extracted+reused teardown (T2/T3), handle-less ttyd reap (T1), comment cleanup (T5), end-to-end (T6).

---

## Notes for the implementer

- **Do not** add a `cp -a` staging copy. The capture runs in place against the *dead* orphan workdir on purpose (kill-first makes it static); this was an explicitly rejected earlier design.
- **Do not** couple the kill to pasta. pasta is conditional; the anchored host-side pkill is the whole point and works in both tree shapes.
- **Do not** delete the marker before the capture succeeds — its persistence across a mid-rescue failure is the retry guard.
- The marker MUST be excluded from the captured snapshot (`workdir_exclude`), or a restored workdir re-triggers rescue forever.
- `claude_path` is always `<workdir>/home/.local/bin/claude` regardless of `config.claude_install_dir` (that override is the version cache dir, not the launch symlink).
- Per repo convention (AGENTS.md): no `Co-Authored-By` trailer on commits.
