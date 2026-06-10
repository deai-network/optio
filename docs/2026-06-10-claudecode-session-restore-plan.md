# Claudecode Explicit Session Restore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `ClaudeCodeTaskConfig` gains `session_restore_from` / `session_restore_until` / `on_session_saved` / `model` — plant an explicit (optionally truncated, workdir-rekeyed) Claude session on fresh starts, capture the session to an addressable GridFS blob at teardown, select the model.

**Architecture:** A pure in-memory blob transform (`transcript.py: rebase_session_blob`) does the projects-dir rekey + truncation engine-side; `_prepare` gains an `elif` restore branch for fresh runs; teardown gains an opt-in capture block (factored from `_capture_snapshot`'s session-blob steps); `build_claude_flags` gains `--model`. No behavior change for any existing config.

**Tech Stack:** Python 3.11+, tarfile (in-memory), pytest + pytest-asyncio, MongoDB via Docker, the existing fake-claude stream-json shim harness.

**Spec:** `docs/2026-06-10-claudecode-session-restore-design.md`

**Working directory:** repo root of the `csillag/convo-scripter` worktree. Test commands run from `packages/optio-claudecode`; venv at repo root (`../../.venv/bin/pytest`).

---

### Task 1: Config fields + validation

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/types.py` (fields after `permission_gate`, ~line 130; validation at the end of `__post_init__`, ~line 182)
- Test: `packages/optio-claudecode/tests/test_session_restore.py` (new)

- [ ] **Step 1.1: Write the failing tests**

Create `packages/optio-claudecode/tests/test_session_restore.py`:

```python
"""Explicit session restore: config validation + capture/restore session flows.

Spec: docs/2026-06-10-claudecode-session-restore-design.md
"""
from __future__ import annotations

import pytest
from bson import ObjectId

from optio_claudecode import ClaudeCodeTaskConfig


def _conv(**kw) -> ClaudeCodeTaskConfig:
    base = dict(
        consumer_instructions="x",
        mode="conversation",
        permission_mode="bypassPermissions",
    )
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


def test_restore_fields_default_off_and_valid_combo():
    cfg = _conv(
        session_restore_from=ObjectId(),
        session_restore_until="some-uuid",
        on_session_saved=lambda blob_id, end_state: None,
        model="claude-opus-4-8",
    )
    assert cfg.session_restore_until == "some-uuid"
    plain = _conv()
    assert plain.session_restore_from is None
    assert plain.session_restore_until is None
    assert plain.on_session_saved is None
    assert plain.model is None


def test_restore_until_requires_restore_from():
    with pytest.raises(ValueError, match="session_restore_until"):
        _conv(session_restore_until="some-uuid")


def test_restore_from_requires_conversation_mode():
    with pytest.raises(ValueError, match="conversation"):
        ClaudeCodeTaskConfig(
            consumer_instructions="x",
            mode="iframe",
            session_restore_from=ObjectId(),
        )


def test_restore_from_incompatible_with_auto_start():
    with pytest.raises(ValueError, match="auto_start"):
        _conv(session_restore_from=ObjectId(), auto_start=True)
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run (from `packages/optio-claudecode`): `../../.venv/bin/pytest tests/test_session_restore.py -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'session_restore_from'`.

- [ ] **Step 1.3: Implement fields + validation**

In `packages/optio-claudecode/src/optio_claudecode/types.py`:

Add `from bson import ObjectId` to the imports if not present.

Add fields directly after `permission_gate` (keep the dataclass field ordering —
all new fields have defaults):

```python
    session_restore_from: "ObjectId | None" = None
    session_restore_until: str | None = None
    on_session_saved: "Callable[[ObjectId, str], Awaitable[None] | None] | None" = None
    model: str | None = None
```

Add docstring lines to the class docstring's field list, matching the
neighboring entries' style:

```
    session_restore_from: GridFS blob id of a home/.claude session tar (as
        produced by on_session_saved); planted before launch on FRESH runs
        only (optio-level resume ignores it). Conversation mode only.
    session_restore_until: transcript entry uuid — keep history up to and
        including this entry, drop the rest. Requires session_restore_from.
    on_session_saved: (new_blob_id, end_state) fired at teardown after the
        session blob is stored under a standalone GridFS ref. Presence opts
        in to capture; runs on all end states (done/failed/cancelled).
    model: passed through as `--model <value>`. Not validated.
```

Append to `__post_init__` (after the existing conversation-mode checks):

```python
        if self.session_restore_until is not None and self.session_restore_from is None:
            raise ValueError(
                "session_restore_until requires session_restore_from"
            )
        if self.session_restore_from is not None and self.mode != "conversation":
            raise ValueError(
                "session_restore_from requires mode='conversation'"
            )
        if self.session_restore_from is not None and self.auto_start:
            raise ValueError(
                "session_restore_from is incompatible with auto_start "
                "(a restored conversation is continued by the caller)"
            )
```

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `../../.venv/bin/pytest tests/test_session_restore.py -v`
Expected: 4 PASS.

- [ ] **Step 1.5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/types.py \
        packages/optio-claudecode/tests/test_session_restore.py
git commit -m "feat(claudecode): session-restore config fields + validation"
```

---

### Task 2: Blob transform module

**Files:**
- Create: `packages/optio-claudecode/src/optio_claudecode/transcript.py`
- Test: `packages/optio-claudecode/tests/test_transcript.py` (new)

- [ ] **Step 2.1: Write the failing tests**

Create `packages/optio-claudecode/tests/test_transcript.py`:

```python
"""rebase_session_blob: projects-dir rekey + transcript truncation.

Spec: docs/2026-06-10-claudecode-session-restore-design.md §4
"""
from __future__ import annotations

import io
import json
import tarfile

import pytest

from optio_claudecode.transcript import rebase_session_blob, slugify_workdir


def _entry(uuid: str, parent: str | None = None, typ: str = "user", **extra) -> str:
    e = {
        "uuid": uuid, "parentUuid": parent, "isSidechain": False,
        "sessionId": "sess-1", "type": typ, **extra,
    }
    return json.dumps(e)


def _bookkeeping(leaf: str) -> str:
    return json.dumps({"type": "last-prompt", "leafUuid": leaf, "sessionId": "sess-1"})


# A realistic mini-transcript: bookkeeping, three turns, one sidechain entry.
LINES = [
    _bookkeeping("u3"),
    json.dumps({"type": "mode", "mode": "normal", "sessionId": "sess-1"}),
    _entry("u1", None, "user"),
    _entry("s1", "u1", "assistant", isSidechain=True),
    _entry("u2", "u1", "assistant"),
    _entry("u3", "u2", "user"),
]
TRANSCRIPT = "\n".join(LINES) + "\n"

OLD_SLUG = "-old-workdir"
NEW_WORKDIR = "/data/optio/tasks/conv-7/work.dir"


def _blob(files: dict[str, tuple[bytes, int]]) -> bytes:
    """Build a tar.gz blob: name -> (content, mtime)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, (content, mtime) in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(content)
            ti.mtime = mtime
            t.addfile(ti, io.BytesIO(content))
    return buf.getvalue()


def _default_blob() -> bytes:
    return _blob({
        "home/.claude/.credentials.json": (b'{"k":"v"}', 100),
        f"home/.claude/projects/{OLD_SLUG}/aaa.jsonl": (TRANSCRIPT.encode(), 200),
    })


def _read(blob: bytes) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as t:
        for m in t.getmembers():
            if m.isfile():
                out[m.name] = t.extractfile(m).read()
    return out


def test_slugify_workdir():
    assert slugify_workdir("/home/user/deai/optio") == "-home-user-deai-optio"
    assert slugify_workdir("/a/.claude/wt/") == "-a--claude-wt"


def test_rekey_only_moves_transcript_and_keeps_content():
    result = _read(rebase_session_blob(_default_blob(), new_workdir=NEW_WORKDIR))
    new_name = f"home/.claude/projects/{slugify_workdir(NEW_WORKDIR)}/aaa.jsonl"
    assert new_name in result
    assert result[new_name].decode() == TRANSCRIPT
    assert result["home/.claude/.credentials.json"] == b'{"k":"v"}'
    assert not any(OLD_SLUG in n for n in result)


def test_truncate_mid_file_keeps_prefix():
    result = _read(rebase_session_blob(
        _default_blob(), new_workdir=NEW_WORKDIR, until_uuid="u2",
    ))
    new_name = f"home/.claude/projects/{slugify_workdir(NEW_WORKDIR)}/aaa.jsonl"
    text = result[new_name].decode()
    assert '"u2"' in text and '"u3"' not in text.replace('"leafUuid": "u3"', "")
    # u3's entry line is gone entirely:
    assert _entry("u3", "u2", "user") not in text


def test_truncate_rewrites_dangling_leafuuid():
    result = _read(rebase_session_blob(
        _default_blob(), new_workdir=NEW_WORKDIR, until_uuid="u2",
    ))
    new_name = f"home/.claude/projects/{slugify_workdir(NEW_WORKDIR)}/aaa.jsonl"
    lines = result[new_name].decode().splitlines()
    lp = json.loads(lines[0])
    assert lp["type"] == "last-prompt"
    assert lp["leafUuid"] == "u2"  # was u3, now dropped → rewritten to boundary


def test_truncate_at_last_entry_is_noop_cut():
    result = _read(rebase_session_blob(
        _default_blob(), new_workdir=NEW_WORKDIR, until_uuid="u3",
    ))
    new_name = f"home/.claude/projects/{slugify_workdir(NEW_WORKDIR)}/aaa.jsonl"
    assert result[new_name].decode() == TRANSCRIPT  # nothing dropped, leaf intact


def test_unknown_uuid_raises():
    with pytest.raises(ValueError, match="not found"):
        rebase_session_blob(
            _default_blob(), new_workdir=NEW_WORKDIR, until_uuid="nope",
        )


def test_no_transcript_raises():
    blob = _blob({"home/.claude/.credentials.json": (b"{}", 100)})
    with pytest.raises(ValueError, match="no transcript"):
        rebase_session_blob(blob, new_workdir=NEW_WORKDIR)


def test_newest_of_several_transcripts_is_truncated():
    older = "\n".join([_entry("o1")]) + "\n"
    blob = _blob({
        f"home/.claude/projects/{OLD_SLUG}/old.jsonl": (older.encode(), 100),
        f"home/.claude/projects/{OLD_SLUG}/new.jsonl": (TRANSCRIPT.encode(), 200),
    })
    result = _read(rebase_session_blob(
        blob, new_workdir=NEW_WORKDIR, until_uuid="u2",
    ))
    slug = slugify_workdir(NEW_WORKDIR)
    assert result[f"home/.claude/projects/{slug}/old.jsonl"].decode() == older
    assert '"u3"' not in result[f"home/.claude/projects/{slug}/new.jsonl"].decode()
```

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `../../.venv/bin/pytest tests/test_transcript.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optio_claudecode.transcript'`.

- [ ] **Step 2.3: Implement the module**

Create `packages/optio-claudecode/src/optio_claudecode/transcript.py`:

```python
"""Session-blob transform: projects-dir rekey + optional transcript truncation.

Spec: docs/2026-06-10-claudecode-session-restore-design.md §4

Pure in-memory tar.gz → tar.gz functions; no host or Mongo dependency.
"""
from __future__ import annotations

import io
import json
import tarfile

_PROJECTS_PREFIX = "home/.claude/projects/"


def slugify_workdir(workdir: str) -> str:
    """Claude Code's projects-dir name for a session cwd.

    Empirical rule ('/' and '.' both map to '-'), confirmed on two
    interactive transcript samples; headless confirmation is
    live-verification item §7.4 of the spec.
    """
    return workdir.rstrip("/").replace("/", "-").replace(".", "-")


def _norm(name: str) -> str:
    return name[2:] if name.startswith("./") else name


def rebase_session_blob(
    plain_tar: bytes, *, new_workdir: str, until_uuid: str | None = None,
) -> bytes:
    """Rekey a home/.claude session blob to ``new_workdir``'s projects slug
    and optionally truncate its newest transcript after ``until_uuid``.

    Raises ValueError when the blob has no transcript, or when
    ``until_uuid`` is not found in the newest transcript.
    """
    new_slug = slugify_workdir(new_workdir)
    src = tarfile.open(fileobj=io.BytesIO(plain_tar), mode="r:*")
    members = src.getmembers()

    transcripts = [
        m for m in members
        if m.isfile()
        and _norm(m.name).startswith(_PROJECTS_PREFIX)
        and _norm(m.name).endswith(".jsonl")
    ]
    if not transcripts:
        raise ValueError("session blob contains no transcript (*.jsonl)")
    target = max(transcripts, key=lambda m: m.mtime)

    new_payloads: dict[str, bytes] = {}
    if until_uuid is not None:
        new_payloads[target.name] = _truncate(
            src.extractfile(target).read(), until_uuid, _norm(target.name),
        )

    def rekeyed(name: str) -> str:
        norm = _norm(name)
        if not norm.startswith(_PROJECTS_PREFIX):
            return norm
        rest = norm[len(_PROJECTS_PREFIX):]
        parts = rest.split("/", 1)
        parts[0] = new_slug
        return _PROJECTS_PREFIX + "/".join(parts)

    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as dst:
        for m in members:
            m2 = tarfile.TarInfo(rekeyed(m.name))
            m2.mtime = m.mtime
            m2.mode = m.mode
            m2.type = m.type
            m2.linkname = m.linkname
            m2.uid, m2.gid = m.uid, m.gid
            m2.uname, m2.gname = m.uname, m.gname
            if m.isfile():
                payload = new_payloads.get(m.name)
                if payload is None:
                    payload = src.extractfile(m).read()
                m2.size = len(payload)
                dst.addfile(m2, io.BytesIO(payload))
            else:
                dst.addfile(m2)
    return out.getvalue()


def _truncate(raw: bytes, until_uuid: str, display_name: str) -> bytes:
    """Prefix-cut a transcript after the line bearing ``until_uuid``, then
    repair kept ``leafUuid`` pointers that reference dropped entries."""
    lines = raw.decode("utf-8", errors="replace").splitlines()
    kept: list[str] = []
    kept_uuids: set[str] = set()
    found = False
    for line in lines:
        kept.append(line)
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        u = entry.get("uuid")
        if u:
            kept_uuids.add(u)
        if u == until_uuid:
            found = True
            break
    if not found:
        raise ValueError(
            f"session_restore_until uuid {until_uuid!r} not found in "
            f"newest transcript {display_name!r}"
        )
    repaired: list[str] = []
    for line in kept:
        try:
            entry = json.loads(line)
        except ValueError:
            repaired.append(line)
            continue
        leaf = entry.get("leafUuid")
        if leaf and leaf not in kept_uuids:
            entry["leafUuid"] = until_uuid
            repaired.append(json.dumps(entry, separators=(",", ":")))
        else:
            repaired.append(line)
    return ("\n".join(repaired) + "\n").encode()
```

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `../../.venv/bin/pytest tests/test_transcript.py -v`
Expected: 8 PASS.

Note on `test_truncate_rewrites_dangling_leafuuid`: the repair runs only on the
truncation path — the boundary uuid set is what defines "dangling". A rekey-only
call leaves the transcript byte-identical by design (asserted by
`test_rekey_only_moves_transcript_and_keeps_content`).

- [ ] **Step 2.5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/transcript.py \
        packages/optio-claudecode/tests/test_transcript.py
git commit -m "feat(claudecode): rebase_session_blob — projects rekey + truncation"
```

---

### Task 3: `--model` flag

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py:810-834` (`build_claude_flags`)
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py:208-213` and `session.py:320-325` (both call sites pass `model=config.model`)
- Test: `packages/optio-claudecode/tests/test_host_actions.py` (append)

- [ ] **Step 3.1: Write the failing test**

Append to `packages/optio-claudecode/tests/test_host_actions.py` (match the file's
existing import of `build_claude_flags`; add it to the import if absent):

```python
def test_build_claude_flags_model():
    from optio_claudecode.host_actions import build_claude_flags
    flags = build_claude_flags(
        permission_mode=None, allowed_tools=None, disallowed_tools=None,
        model="claude-opus-4-8",
    )
    assert flags == ["--model", "claude-opus-4-8"]
    assert build_claude_flags(
        permission_mode=None, allowed_tools=None, disallowed_tools=None,
    ) == []
```

- [ ] **Step 3.2: Run test to verify it fails**

Run: `../../.venv/bin/pytest tests/test_host_actions.py::test_build_claude_flags_model -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'model'`.

- [ ] **Step 3.3: Implement**

In `build_claude_flags` (host_actions.py): add keyword param `model: str | None = None`
after `resuming`, document it in the docstring ("``model`` emits `--model <value>`;
not validated — vendor strings change"), and append before the `return`:

```python
    if model:
        out += ["--model", model]
```

In `session.py`, both `build_claude_flags(...)` call sites (iframe body ~line 208,
conversation body ~line 320) gain `model=config.model,` after the
`disallowed_tools=` argument. (`model` is mode-independent per the spec.)

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `../../.venv/bin/pytest tests/test_host_actions.py -v`
Expected: all PASS (existing + new).

- [ ] **Step 3.5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py \
        packages/optio-claudecode/src/optio_claudecode/session.py \
        packages/optio-claudecode/tests/test_host_actions.py
git commit -m "feat(claudecode): model config field -> --model flag"
```

---

### Task 4: Capture-to-ref at teardown (`on_session_saved`)

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py` — factor `_store_session_blob` out of `_capture_snapshot` (~lines 849-871); insert the capture block in the teardown `finally` immediately BEFORE the `config.supports_resume` snapshot block (~line 544); add `import sys` to the module imports.
- Test: `packages/optio-claudecode/tests/test_session_restore.py` (append)

**Why the ordering matters:** `_capture_snapshot` defensively wipes `home/.claude`
(step 4) before taring the workdir — the explicit capture must run first.

- [ ] **Step 4.1: Write the failing tests**

Append to `packages/optio-claudecode/tests/test_session_restore.py` (the flow tests
mirror `tests/test_conversation_session.py`'s harness):

```python
import asyncio
import pathlib
import time as _time

from bson import ObjectId as _OID  # noqa: F401  (clarity in asserts)

from optio_core.lifecycle import Optio

from optio_claudecode import create_claudecode_task
from optio_claudecode.transcript import slugify_workdir

_TERMINAL = {"done", "failed", "cancelled"}

# A minimal valid transcript planted by before_execute on capture-side sessions.
_FIXTURE_LINES = [
    '{"type":"last-prompt","leafUuid":"u3","sessionId":"s"}',
    '{"uuid":"u1","parentUuid":null,"isSidechain":false,"sessionId":"s","type":"user"}',
    '{"uuid":"u2","parentUuid":"u1","isSidechain":false,"sessionId":"s","type":"assistant"}',
    '{"uuid":"u3","parentUuid":"u2","isSidechain":false,"sessionId":"s","type":"user"}',
]
_FIXTURE = "\n".join(_FIXTURE_LINES) + "\n"


async def _make_optio(mongo_db, prefix: str) -> Optio:
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix)
    return optio


async def _wait_terminal(optio: Optio, process_id: str, timeout: float = 30.0) -> dict:
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc["status"]["state"] in _TERMINAL:
            return proc
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} did not reach terminal state in {timeout}s")


def _flow_config(shim_install_dir, claude_cache_dir, **kw):
    base = dict(
        consumer_instructions="Converse with the test.",
        mode="conversation",
        permission_mode="bypassPermissions",
        claude_install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        supports_resume=False,
    )
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


def _plant_transcript_hook(records: dict | None = None):
    """before_execute hook: write a fixture transcript into home/.claude
    so capture has something real to save (the fake claude writes none)."""
    async def before(hook_ctx):
        workdir = pathlib.Path(hook_ctx._host.workdir)
        pdir = workdir / "home/.claude/projects" / slugify_workdir(str(workdir))
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "fixture.jsonl").write_text(_FIXTURE)
        if records is not None:
            records["workdir"] = str(workdir)
    return before


async def _run_capture_session(
    optio, shim_install_dir, claude_cache_dir, pid: str, *, cancel: bool = False,
):
    """Run one conversation session that plants a transcript and captures it.
    Returns the (blob_id, end_state) the callback received."""
    saved: list[tuple] = []
    task = create_claudecode_task(
        process_id=pid, name=pid,
        config=_flow_config(
            shim_install_dir, claude_cache_dir,
            before_execute=_plant_transcript_hook(),
            on_session_saved=lambda blob_id, end_state: saved.append(
                (blob_id, end_state)
            ),
        ),
    )
    await optio.adhoc_define(task)
    conv = await optio.launch_and_await_result(pid, session_id=None, timeout=60)
    if cancel:
        await optio.cancel(pid)
    else:
        await conv.close()
    await _wait_terminal(optio, pid)
    assert len(saved) == 1, f"on_session_saved fired {len(saved)} times"
    return saved[0]


@pytest.mark.asyncio
async def test_capture_on_close_fires_callback_done(
    shim_install_dir, claude_cache_dir, task_root, mongo_db,
):
    optio = await _make_optio(mongo_db, "ccsr1")
    try:
        blob_id, end_state = await _run_capture_session(
            optio, shim_install_dir, claude_cache_dir, "sr-cap-1",
        )
        assert isinstance(blob_id, ObjectId)
        assert end_state == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_capture_on_cancel_fires_callback_cancelled(
    shim_install_dir, claude_cache_dir, task_root, mongo_db,
):
    optio = await _make_optio(mongo_db, "ccsr2")
    try:
        blob_id, end_state = await _run_capture_session(
            optio, shim_install_dir, claude_cache_dir, "sr-cap-2", cancel=True,
        )
        assert isinstance(blob_id, ObjectId)
        assert end_state == "cancelled"
    finally:
        await optio.shutdown(grace_seconds=1.0)
```

- [ ] **Step 4.2: Run tests to verify they fail**

Run: `../../.venv/bin/pytest tests/test_session_restore.py -v -k capture`
Expected: both FAIL — callback never fires (`AssertionError: on_session_saved fired 0 times`).

- [ ] **Step 4.3: Implement**

In `session.py`:

1. Add `import sys` to the module imports.

2. Factor the session-blob storage out of `_capture_snapshot`. Add this helper
above `_capture_snapshot` (~line 823); then replace `_capture_snapshot`'s steps
1-3 (lines 849-871: `_archive_home_claude` through the short-write check) with a
single call `session_blob_id = await _store_session_blob(ctx, host,
session_blob_encrypt=session_blob_encrypt)`:

```python
async def _store_session_blob(
    ctx: ProcessContext,
    host: Host,
    *,
    session_blob_encrypt: "Callable[[bytes], bytes] | None",
):
    """Tar home/.claude, encrypt, store as a standalone GridFS blob.

    Shared by snapshot capture and the explicit on_session_saved capture.
    Returns the GridFS file id.
    """
    session_bytes = await _archive_home_claude(host)
    encrypt = session_blob_encrypt or (lambda b: b)
    payload = encrypt(session_bytes)
    expected_len = len(payload)
    async with ctx.store_blob("session") as swriter:
        await swriter.write(payload)
        session_blob_id = swriter.file_id
        written = getattr(swriter, "_position", None)
        if written is not None and written != expected_len:
            raise RuntimeError(
                f"session blob short-write: expected {expected_len} bytes, "
                f"GridIn._position is {written}"
            )
    return session_blob_id
```

3. Insert the capture block in the teardown `finally`, immediately BEFORE the
`if config.supports_resume and launched_handle is not None:` snapshot block
(~line 544):

```python
        # Explicit session capture (on_session_saved): runs BEFORE snapshot
        # capture, whose workdir-tar step defensively wipes home/.claude.
        # Same reached-live gate as snapshots; unlike snapshots there is no
        # credentials guard — the caller owns blob semantics and lifecycle.
        if config.on_session_saved is not None and launched_handle is not None:
            _trace("finally: session blob capture START")
            try:
                _end_state = (
                    "cancelled" if cancelled
                    else "failed" if sys.exc_info()[0] is not None
                    else "done"
                )
                _session_blob_id = await _store_session_blob(
                    ctx, host,
                    session_blob_encrypt=config.session_blob_encrypt,
                )
                await _call_maybe_async(
                    config.on_session_saved, _session_blob_id, _end_state,
                )
                _trace(
                    "finally: session blob capture DONE id=%s state=%s",
                    _session_blob_id, _end_state,
                )
            except Exception:
                _LOG.exception(
                    "session blob capture failed; callback not fired, "
                    "teardown continues",
                )
```

(`sys.exc_info()` inside a `finally` during exception unwinding returns the
in-flight exception — that is the "failed" detection. `cancelled` is the
existing teardown flag.)

- [ ] **Step 4.4: Run tests to verify they pass**

Run: `../../.venv/bin/pytest tests/test_session_restore.py tests/test_conversation_session.py -v`
Expected: all PASS (the conversation-session file guards against regressions in
the shared teardown and `_capture_snapshot` factoring).

- [ ] **Step 4.5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/session.py \
        packages/optio-claudecode/tests/test_session_restore.py
git commit -m "feat(claudecode): on_session_saved capture-to-ref at teardown"
```

---

### Task 5: Restore flow in `_prepare`

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py:144-186` (`_prepare` — add the `elif` restore branch; log notice on skip)
- Test: `packages/optio-claudecode/tests/test_session_restore.py` (append)

- [ ] **Step 5.1: Write the failing tests**

Append to `packages/optio-claudecode/tests/test_session_restore.py`:

```python
def _record_projects_hook(records: dict):
    """before_execute hook for restore-side sessions: snapshot the projects
    tree (it was planted during _prepare, which runs before this hook)."""
    async def before(hook_ctx):
        workdir = pathlib.Path(hook_ctx._host.workdir)
        records["workdir"] = str(workdir)
        proj = workdir / "home/.claude/projects"
        records["files"] = {
            str(p.relative_to(proj)): p.read_text()
            for p in proj.rglob("*.jsonl")
        } if proj.exists() else {}
    return before


async def _launch_restore_session(
    optio, shim_install_dir, claude_cache_dir, pid: str, blob_id, *,
    until: str | None = None, records: dict,
):
    task = create_claudecode_task(
        process_id=pid, name=pid,
        config=_flow_config(
            shim_install_dir, claude_cache_dir,
            session_restore_from=blob_id,
            session_restore_until=until,
            before_execute=_record_projects_hook(records),
        ),
    )
    await optio.adhoc_define(task)
    return await optio.launch_and_await_result(pid, session_id=None, timeout=60)


@pytest.mark.asyncio
async def test_restore_round_trip_rekeys_to_new_workdir(
    shim_install_dir, claude_cache_dir, task_root, mongo_db,
):
    optio = await _make_optio(mongo_db, "ccsr3")
    try:
        blob_id, _ = await _run_capture_session(
            optio, shim_install_dir, claude_cache_dir, "sr-rt-a",
        )
        records: dict = {}
        conv = await _launch_restore_session(
            optio, shim_install_dir, claude_cache_dir, "sr-rt-b", blob_id,
            records=records,
        )
        # Transcript landed under the NEW workdir's slug, content intact.
        slug = slugify_workdir(records["workdir"])
        assert list(records["files"]) == [f"{slug}/fixture.jsonl"]
        assert records["files"][f"{slug}/fixture.jsonl"] == _FIXTURE
        # Silent kickoff: our send is the fake's FIRST turn.
        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        await conv.send("ping")
        assert await asyncio.wait_for(msgs.get(), 10) == "reply-1"
        await conv.close()
        proc = await _wait_terminal(optio, "sr-rt-b")
        assert proc["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_restore_with_truncation(
    shim_install_dir, claude_cache_dir, task_root, mongo_db,
):
    optio = await _make_optio(mongo_db, "ccsr4")
    try:
        blob_id, _ = await _run_capture_session(
            optio, shim_install_dir, claude_cache_dir, "sr-tr-a",
        )
        records: dict = {}
        conv = await _launch_restore_session(
            optio, shim_install_dir, claude_cache_dir, "sr-tr-b", blob_id,
            until="u2", records=records,
        )
        slug = slugify_workdir(records["workdir"])
        text = records["files"][f"{slug}/fixture.jsonl"]
        assert '"u2"' in text
        assert '"uuid":"u3"' not in text  # u3 entry dropped
        await conv.close()
        await _wait_terminal(optio, "sr-tr-b")
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_restore_blob_without_transcript_fails(
    shim_install_dir, claude_cache_dir, task_root, mongo_db,
):
    from optio_core.exceptions import ResultNotPublished

    optio = await _make_optio(mongo_db, "ccsr5")
    try:
        # Capture session WITHOUT planting a transcript → blob has no *.jsonl.
        saved: list[tuple] = []
        task = create_claudecode_task(
            process_id="sr-nt-a", name="sr-nt-a",
            config=_flow_config(
                shim_install_dir, claude_cache_dir,
                on_session_saved=lambda b, s: saved.append((b, s)),
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "sr-nt-a", session_id=None, timeout=60,
        )
        await conv.close()
        await _wait_terminal(optio, "sr-nt-a")
        blob_id = saved[0][0]

        records: dict = {}
        task_b = create_claudecode_task(
            process_id="sr-nt-b", name="sr-nt-b",
            config=_flow_config(
                shim_install_dir, claude_cache_dir,
                session_restore_from=blob_id,
                before_execute=_record_projects_hook(records),
            ),
        )
        await optio.adhoc_define(task_b)
        with pytest.raises(ResultNotPublished):
            await optio.launch_and_await_result(
                "sr-nt-b", session_id=None, timeout=60,
            )
        proc = await optio.get_process("sr-nt-b")
        assert proc["status"]["state"] == "failed"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_restore_directives_skipped_on_optio_resume(
    shim_install_dir, claude_cache_dir, task_root, mongo_db, caplog,
):
    """supports_resume task with restore directives: fresh run applies them;
    an optio-level resume skips them with a logged notice."""
    optio = await _make_optio(mongo_db, "ccsr6")
    try:
        blob_id, _ = await _run_capture_session(
            optio, shim_install_dir, claude_cache_dir, "sr-skip-src",
        )
        records: dict = {}
        task = create_claudecode_task(
            process_id="sr-skip", name="sr-skip",
            config=_flow_config(
                shim_install_dir, claude_cache_dir,
                supports_resume=True,
                session_restore_from=blob_id,
                before_execute=_record_projects_hook(records),
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "sr-skip", session_id=None, timeout=60,
        )
        await conv.close()
        await _wait_terminal(optio, "sr-skip")

        import logging
        with caplog.at_level(logging.INFO, logger="optio_claudecode.session"):
            conv2 = await optio.launch_and_await_result(
                "sr-skip", resume=True, session_id=None, timeout=60,
            )
            await conv2.close()
            proc = await _wait_terminal(optio, "sr-skip")
        assert proc["status"]["state"] == "done"
        assert "session_restore_from is skipped" in caplog.text
    finally:
        await optio.shutdown(grace_seconds=1.0)
```

- [ ] **Step 5.2: Run tests to verify they fail**

Run: `../../.venv/bin/pytest tests/test_session_restore.py -v -k restore`
Expected: the four new tests FAIL — no restore happens, so `records["files"]` is
empty / blob restore asserts fail. (`test_restore_blob_without_transcript_fails`
fails because the task succeeds instead of failing.)

- [ ] **Step 5.3: Implement**

In `session.py`:

1. Add the import near the other intra-package imports:
   `from optio_claudecode.transcript import rebase_session_blob`.

2. In `_prepare`, extend the resume block and add the `elif` branch. The current
code is:

```python
        resuming = snapshot is not None
        if resuming:
            if config.on_seed_saved is not None:
```

Replace with (the new lines are the `session_restore_from` notice and the whole
`elif` arm; the existing `if resuming:` body is unchanged):

```python
        resuming = snapshot is not None
        if resuming:
            if config.session_restore_from is not None:
                _LOG.info(
                    "optio resume in progress; session_restore_from is skipped "
                    "(restore directives apply to fresh runs only)",
                )
            if config.on_seed_saved is not None:
```

and after the END of the `if resuming:` block (after the `_LOG.warning(...)`
for the no-transcript D3 case, line ~186), add:

```python
        elif config.session_restore_from is not None:
            # Explicit session restore (fresh runs only): fetch, decrypt,
            # rekey to this workdir (and truncate when requested), plant.
            payload = await _read_blob_bytes(ctx, config.session_restore_from)
            decrypt = config.session_blob_decrypt or (lambda b: b)
            plain = decrypt(payload)
            plain = rebase_session_blob(
                plain,
                new_workdir=host.workdir,
                until_uuid=config.session_restore_until,
            )
            await _extract_home_claude(host, plain)
            pass_continue = await _has_transcript(host)
            if not pass_continue:
                raise RuntimeError(
                    "session_restore_from: restored blob contains no transcript"
                )
```

No kickoff change is needed: with `session_restore_from`, `auto_start` is
excluded by validation and `resuming` is False, so the conversation body's
kickoff conditional (`session.py:349-352`) already sends nothing, while
`pass_continue=True` routes `--continue` into the argv via the existing
`build_claude_flags(resuming=pass_continue)` call.

- [ ] **Step 5.4: Run tests to verify they pass**

Run: `../../.venv/bin/pytest tests/test_session_restore.py -v`
Expected: all PASS (4 config + 2 capture + 4 restore).

- [ ] **Step 5.5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/session.py \
        packages/optio-claudecode/tests/test_session_restore.py
git commit -m "feat(claudecode): session_restore_from/_until restore flow"
```

---

### Task 6: Full-suite regression gate

**Files:** none (verification only)

- [ ] **Step 6.1: Run the full optio-claudecode suite**

Run (from `packages/optio-claudecode`): `../../.venv/bin/pytest -q`
Expected: all pass. Known pre-existing flake to NOT chase:
`optio-core`'s `test_cancel_shared_deadline_across_subtree` (different package);
if anything claudecode-side fails, investigate — likeliest candidates are
`test_conversation_session.py` (shared teardown changes) and snapshot-related
tests (`_capture_snapshot` factoring).

- [ ] **Step 6.2: Run the optio-core suite (cross-package guard)**

Run (from `packages/optio-core`): `../../.venv/bin/pytest -q`
Expected: 391 passed (the D5 baseline). A failure of
`test_cancel_propagation.py::test_cancel_shared_deadline_across_subtree` is a
known pre-existing flake — re-run once before investigating.

- [ ] **Step 6.3: Commit (only if fixes were needed)**

If both suites are green with no changes, there is nothing to commit.
