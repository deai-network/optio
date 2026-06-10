# Conversation-Mode FS Isolation (claustrum) — Implementation Plan

> **For agentic workers:** small TDD feature. Factor a shared wrap helper, apply it on the conversation launch, flip the demo tasks, one unit test.

**Goal:** Wrap the conversation (headless stream-json) claude launch with claustrum, identical to the iframe path. **Spec:** `docs/2026-06-10-conversation-mode-fs-isolation-design.md`.

---

## Task 1: Failing test — conversation launch is claustrum-wrapped when fs_isolation is on

**File:** `packages/optio-claudecode/tests/test_conversation_session.py`

- [ ] **Step 1: Add the test.** It runs a conversation task with `fs_isolation=True`, stubs claustrum provisioning (no real build/network), and captures the command handed to `launch_subprocess`, asserting the claustrum prefix is present (and absent when off).

```python
@pytest.mark.asyncio
async def test_conversation_launch_is_claustrum_wrapped(
    shim_install_dir, claude_cache_dir, task_root, mongo_db, monkeypatch,
):
    """fs_isolation=True must wrap the headless conversation launch with
    claustrum (mirrors the iframe path)."""
    from optio_claudecode import host_actions
    from optio_host.host import LocalHost

    # Stub provisioning so no real claustrum is cloned/built.
    async def _fake_install(hook_ctx, *, install_dir=None):
        return "/fake/bin/claustrum"
    monkeypatch.setattr(host_actions, "ensure_claustrum_installed", _fake_install)
    async def _no_newer():
        return None
    monkeypatch.setattr(host_actions, "claustrum_newer_tag", _no_newer)

    # Capture the launched command, then abort the launch (we only assert the cmd).
    captured: dict = {}
    orig = LocalHost.launch_subprocess
    async def _capture(self, cmd, **kw):
        captured["cmd"] = cmd
        raise RuntimeError("captured-launch")
    monkeypatch.setattr(LocalHost, "launch_subprocess", _capture)

    optio = await _make_optio(mongo_db, "ccconvfs")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-fs",
            name="Conversation fs-isolation",
            config=_conversation_config(
                shim_install_dir, claude_cache_dir,
                fs_isolation=True, delivery_type="t",
            ),
        )
        await optio.adhoc_define(task)
        try:
            await optio.launch_and_await_result("cc-conv-fs", session_id=None, timeout=60)
        except Exception:
            pass  # the capture-abort fails the launch; we only need the cmd
        await _wait_terminal(optio, "cc-conv-fs")

        cmd = captured.get("cmd", "")
        assert "/fake/bin/claustrum --best-effort --abi-min 1 " in cmd, cmd
        # claustrum separator precedes the real claude argv
        assert cmd.index("/fake/bin/claustrum") < cmd.index("--input-format")
        assert " -- " in cmd
    finally:
        monkeypatch.setattr(LocalHost, "launch_subprocess", orig)
        await optio.shutdown(grace_seconds=1.0)
```

- [ ] **Step 2: Run — expect RED**

Run: `.venv/bin/python -m pytest "packages/optio-claudecode/tests/test_conversation_session.py::test_conversation_launch_is_claustrum_wrapped" -q`
Expected: FAIL — the captured cmd has no claustrum prefix (conversation launch is not yet wrapped).

## Task 2: Factor the shared wrap helper and apply it on both paths

**File:** `packages/optio-claudecode/src/optio_claudecode/session.py`

- [ ] **Step 1: Add the module-level helper** (near the other module helpers):

```python
async def _build_claustrum_wrap(host, config, claustrum_path):
    """claustrum argv prefix for an fs-isolated launch, or None when fs_isolation
    is off. Shared by the iframe and conversation launch paths."""
    if not config.fs_isolation:
        return None
    from . import fs_allowlist
    cache_dir = await host_actions._resolve_cache_dir(host, config.claude_install_dir)
    grants = fs_allowlist.build_grant_flags(
        workdir=host.workdir,
        claude_cache_dir=cache_dir,
        extra_allowed_dirs=config.extra_allowed_dirs,
    )
    return [claustrum_path, "--best-effort", "--abi-min", "1", *grants, "--"]
```

- [ ] **Step 2: Iframe path** — replace the inline `claustrum_wrap = None; if config.fs_isolation: ...` block in `_claudecode_body` with:

```python
        claustrum_wrap = await _build_claustrum_wrap(host, config, claustrum_path)
```

- [ ] **Step 3: Conversation path** — in `_conversation_body`, after `argv = host_actions.build_conversation_argv(...)` and before `cmd = " ".join(shlex.quote(a) for a in argv)`, insert:

```python
        wrap = await _build_claustrum_wrap(host, config, claustrum_path)
        if wrap:
            argv = [*wrap, *argv]
```

- [ ] **Step 4: Run — expect GREEN**

Run: `.venv/bin/python -m pytest packages/optio-claudecode/tests/test_conversation_session.py packages/optio-claudecode/tests/test_claustrum_wrap.py -q`
Expected: all pass (the new test + the iframe wrap tests — the iframe behavior is unchanged).

## Task 3: Flip the demo conversation tasks to isolated

**File:** `packages/optio-demo/src/optio_demo/tasks/claudecode.py`

- [ ] **Step 1:** For the two `mode="conversation"` tasks, replace the `fs_isolation=False` + TODO comment with `fs_isolation=True` and add `delivery_type="system-notices"`.

- [ ] **Step 2: Run the demo + full claudecode suites**

Run: `.venv/bin/python -m pytest packages/optio-claudecode/tests -q && .venv/bin/python -m pytest packages/optio-demo/tests -q`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/session.py packages/optio-claudecode/tests/test_conversation_session.py packages/optio-demo/src/optio_demo/tasks/claudecode.py
git commit -m "feat(claudecode): claustrum fs-isolation for conversation-mode launch"
```
