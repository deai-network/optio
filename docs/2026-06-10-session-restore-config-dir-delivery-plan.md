# Session-Restore Config-Dir Delivery — Implementation Plan

> **For agentic workers:** small TDD fix. One test, one production line.

**Goal:** On the `session_restore_from` restore path, rekey the restored `.claude.json` `projects` to the new workdir (trust), mirroring the optio-resume path — so a restored conversation isn't blocked by the folder-trust prompt.

**Spec:** `docs/2026-06-10-session-restore-config-dir-delivery-design.md`.

---

## Task 1: Failing test — restore rekeys `.claude.json` projects to the new workdir

**File:** `packages/optio-claudecode/tests/test_session_restore.py`

- [ ] **Step 1: Add a capture hook that also plants a `.claude.json` with foreign projects**, then a test that restores and asserts the rekey. Append:

```python
def _plant_transcript_and_claude_json_hook(foreign_cwd: str):
    """Capture-side before_execute: plant a transcript AND a .claude.json whose
    `projects` is keyed to a FOREIGN workdir, so the restore-side rekey has
    something to fix."""
    async def before(hook_ctx):
        workdir = pathlib.Path(hook_ctx._host.workdir)
        pdir = workdir / "home/.claude/projects" / slugify_workdir(str(workdir))
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "fixture.jsonl").write_text(_FIXTURE)
        cc = workdir / "home/.claude/.claude.json"
        cc.write_text(
            '{"projects":{"' + foreign_cwd + '":{"hasTrustDialogAccepted":true,"k":1}}}'
        )
    return before


def _record_claude_json_hook(records: dict):
    """Restore-side before_execute: capture .claude.json projects after _prepare."""
    async def before(hook_ctx):
        import json
        workdir = pathlib.Path(hook_ctx._host.workdir)
        records["workdir"] = str(workdir)
        cc = workdir / "home/.claude/.claude.json"
        records["claude_json"] = json.loads(cc.read_text()) if cc.exists() else None
    return before


@pytest.mark.asyncio
async def test_restore_rekeys_claude_json_projects_to_new_workdir(
    shim_install_dir, claude_cache_dir, task_root, mongo_db,
):
    """A restored .claude.json carries the ORIGINAL session's projects keys.
    Without a rekey, claude (running in the NEW workdir under CLAUDE_CONFIG_DIR)
    sees the new workdir untrusted -> folder-trust prompt -> bypassPermissions
    can't suppress -> hang. The session_restore_from path must collapse projects
    to the launch workdir with trust, like the optio-resume path does."""
    optio = await _make_optio(mongo_db, "ccsrk")
    try:
        # Capture a blob whose .claude.json is keyed to a FOREIGN workdir.
        saved: list = []
        task = create_claudecode_task(
            process_id="sr-rk-a", name="sr-rk-a",
            config=_flow_config(
                shim_install_dir, claude_cache_dir,
                before_execute=_plant_transcript_and_claude_json_hook("/old/cwd"),
                on_session_saved=lambda b, s: saved.append((b, s)),
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result("sr-rk-a", session_id=None, timeout=60)
        await conv.close()
        await _wait_terminal(optio, "sr-rk-a")
        blob_id, _ = saved[0]

        # Restore into a NEW workdir; capture the planted .claude.json.
        records: dict = {}
        task_b = create_claudecode_task(
            process_id="sr-rk-b", name="sr-rk-b",
            config=_flow_config(
                shim_install_dir, claude_cache_dir,
                session_restore_from=blob_id,
                before_execute=_record_claude_json_hook(records),
            ),
        )
        await optio.adhoc_define(task_b)
        conv2 = await optio.launch_and_await_result("sr-rk-b", session_id=None, timeout=60)
        await conv2.close()
        await _wait_terminal(optio, "sr-rk-b")

        cj = records["claude_json"]
        assert cj is not None, "restored .claude.json missing"
        # Rekeyed to exactly the new workdir, with trust; foreign key gone.
        assert list(cj["projects"].keys()) == [records["workdir"]], cj["projects"]
        assert cj["projects"][records["workdir"]]["hasTrustDialogAccepted"] is True
        assert "/old/cwd" not in cj["projects"]
    finally:
        await optio.shutdown(grace_seconds=1.0)
```

- [ ] **Step 2: Run — expect RED**

Run: `.venv/bin/python -m pytest packages/optio-claudecode/tests/test_session_restore.py::test_restore_rekeys_claude_json_projects_to_new_workdir -q`
Expected: FAIL — projects still keyed to `/old/cwd` (no rekey on the session_restore_from path).

## Task 2: Fix — call the rekey on the `session_restore_from` path

**File:** `packages/optio-claudecode/src/optio_claudecode/session.py`

- [ ] **Step 1:** In `_prepare`, in the `elif config.session_restore_from is not None:` block, add `await _rekey_claude_json_projects(host)` immediately after `await _extract_home_claude(host, plain)` and before `pass_continue = await _has_transcript(host)`.

(`_rekey_claude_json_projects` is already imported in `session.py`.)

- [ ] **Step 2: Run — expect GREEN**

Run: `.venv/bin/python -m pytest packages/optio-claudecode/tests/test_session_restore.py -q`
Expected: all pass, including the new test.

- [ ] **Step 3: Full claudecode suite**

Run: `.venv/bin/python -m pytest packages/optio-claudecode/tests -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/session.py packages/optio-claudecode/tests/test_session_restore.py
git commit -m "fix(claudecode): rekey .claude.json projects on session_restore_from path"
```
