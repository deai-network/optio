# optio-claudecode Resume Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add resume support to `optio-claudecode` so a terminated task can be relaunched by `processId`, restoring claude's conversation, credentials, settings, and workdir — mirroring `optio-opencode`'s resume surface.

**Architecture:** A snapshot is two GridFS blobs — an encryptable `session` tar.gz of `<workdir>/home/.claude/` plus a plaintext `workdir` tar.gz of everything else. Capture runs in `run_claudecode_session`'s `finally`; resume restores (plaintext first, encrypted `home/.claude` on top) before the protocol driver starts and appends `--continue` to claude's argv. The implementation is a direct port of `optio-opencode`'s snapshot/resume machinery, minus the `sessionId` field (claude finds its own latest session via `--continue`) and plus HOME-subtree archiving instead of `opencode export/import`.

**Tech Stack:** Python 3, `asyncio`, `motor` (async MongoDB), GridFS, `pytest`/`pytest-asyncio`, MongoDB-via-Docker for the session tests.

**Source spec:** `docs/2026-05-29-optio-claudecode-resume-design.md`

**Reference implementation (port these):**
- `packages/optio-opencode/src/optio_opencode/snapshots.py`
- `packages/optio-opencode/src/optio_opencode/session.py`
- `packages/optio-opencode/src/optio_opencode/prompt.py`
- `packages/optio-opencode/src/optio_opencode/types.py`
- `packages/optio-opencode/tests/{test_snapshots,test_session_resume,test_session_blob_hooks,test_prompt}.py`

**Environment note (from project memory):** Use a `.venv` inside the worktree, never `pip install -e` against the global Python. Run pytest from that venv. MongoDB comes from Docker (or `mongodb-memory-server`); there is no local `mongod`. Session/snapshot tests need `MONGO_URL` reachable (defaults to `mongodb://localhost:27017`).

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `packages/optio-claudecode/src/optio_claudecode/snapshots.py` | Mongo `{prefix}_claudecode_session_snapshots` helpers (`insert_snapshot`, `load_latest_snapshot`, `prune_snapshots`, `ensure_indexes`). No `sessionId`. | Create |
| `packages/optio-claudecode/src/optio_claudecode/types.py` | Add `supports_resume` (default `True`), `workdir_exclude`, `session_blob_encrypt`, `session_blob_decrypt`, `on_resume_refresh`; asymmetric-crypto validation. | Modify |
| `packages/optio-claudecode/src/optio_claudecode/prompt.py` | Render the claudecode resume section (opencode's text + one `home/.claude/` bullet); gate on `supports_resume`. | Modify |
| `packages/optio-claudecode/src/optio_claudecode/host_actions.py` | `build_claude_flags` gains `resuming: bool = False` → appends `--continue`. | Modify |
| `packages/optio-claudecode/src/optio_claudecode/session.py` | Resume decision + restore before the protocol session; fresh-vs-resume body branch; snapshot capture in `finally`; resume helpers; `create_claudecode_task` propagates `config.supports_resume`. | Modify |
| `packages/optio-claudecode/tests/fake_claude.py` | Add `long_then_signaled` and `idempotent_done` scenarios; record received argv so tests can assert `--continue`. | Modify |
| `packages/optio-claudecode/tests/test_snapshots.py` | Port of opencode's, schema diff (no `sessionId`). | Create |
| `packages/optio-claudecode/tests/test_session_blob_hooks.py` | Config validation + capture-through-encrypt + source check of resume decrypt wiring. | Create |
| `packages/optio-claudecode/tests/test_resume_prompt.py` | `_render_resume_section` text under default/custom excludes + `home/.claude/` clause. | Create |
| `packages/optio-claudecode/tests/test_session_resume.py` | End-to-end local round-trip (capture → resume) with identity crypto. | Create |
| `packages/optio-claudecode/tests/test_session_resume_decrypt_failure.py` | Corrupt session blob → resume raises, no silent fresh-start. | Create |
| `packages/optio-claudecode/tests/test_on_resume_refresh.py` | Hook fires only on resume; rewrites AGENTS.md; `REFRESHED:AGENTS.md` in resume.log. | Create |
| `packages/optio-claudecode/tests/test_types.py` | Add assertions for the new config defaults. | Modify |
| `packages/optio-demo/src/optio_demo/tasks/claudecode.py` | `supports_resume=True` explicit; crypto hooks left `None`. | Modify |
| `packages/optio-claudecode/AGENTS.md` | Update the `supports_resume=False` doc line. | Modify |

---

## Task 1: Snapshot Mongo helpers

Direct port of `optio_opencode/snapshots.py` with the collection suffix changed and the `session_id` parameter/field removed.

**Files:**
- Create: `packages/optio-claudecode/src/optio_claudecode/snapshots.py`
- Test: `packages/optio-claudecode/tests/test_snapshots.py`

- [ ] **Step 1: Write the failing test**

Create `packages/optio-claudecode/tests/test_snapshots.py`:

```python
"""Tests for the per-task claudecode session snapshot collection."""

import asyncio
import os
import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from optio_claudecode.snapshots import (
    SESSION_SNAPSHOT_COLLECTION_SUFFIX,
    SNAPSHOT_RETENTION,
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_cc_test_snapshots_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def test_collection_suffix_is_claudecode_specific():
    assert SESSION_SNAPSHOT_COLLECTION_SUFFIX == "_claudecode_session_snapshots"


async def test_insert_and_load_latest(mongo_db):
    pid = "proc_a"
    for i in range(3):
        await insert_snapshot(
            mongo_db, prefix="opt", process_id=pid,
            end_state="done",
            session_blob_id=ObjectId(), workdir_blob_id=ObjectId(),
            deliverables_emitted=[],
        )
        await asyncio.sleep(0.001)

    latest = await load_latest_snapshot(mongo_db, prefix="opt", process_id=pid)
    assert latest is not None
    assert latest["endState"] == "done"
    assert "sessionId" not in latest


async def test_load_latest_none_when_empty(mongo_db):
    latest = await load_latest_snapshot(mongo_db, prefix="opt", process_id="nope")
    assert latest is None


async def test_prune_keeps_last_five_and_returns_deleted_ids(mongo_db):
    pid = "proc_b"
    blob_ids_by_cap: list[dict] = []
    for i in range(6):
        snap = await insert_snapshot(
            mongo_db, prefix="opt", process_id=pid,
            end_state="done",
            session_blob_id=ObjectId(), workdir_blob_id=ObjectId(),
            deliverables_emitted=[],
        )
        await asyncio.sleep(0.001)
        blob_ids_by_cap.append({
            "session": snap["sessionBlobId"],
            "workdir": snap["workdirBlobId"],
        })

    pruned = await prune_snapshots(mongo_db, prefix="opt", process_id=pid)
    assert len(pruned) == 1
    assert pruned[0]["sessionBlobId"] == blob_ids_by_cap[0]["session"]
    assert pruned[0]["workdirBlobId"] == blob_ids_by_cap[0]["workdir"]

    coll = mongo_db[f"opt{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"]
    count = await coll.count_documents({"processId": pid})
    assert count == SNAPSHOT_RETENTION


async def test_prune_noop_when_within_retention(mongo_db):
    pid = "proc_c"
    for i in range(SNAPSHOT_RETENTION):
        await insert_snapshot(
            mongo_db, prefix="opt", process_id=pid,
            end_state="done",
            session_blob_id=ObjectId(), workdir_blob_id=ObjectId(),
            deliverables_emitted=[],
        )
        await asyncio.sleep(0.001)
    pruned = await prune_snapshots(mongo_db, prefix="opt", process_id=pid)
    assert pruned == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest packages/optio-claudecode/tests/test_snapshots.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optio_claudecode.snapshots'`

- [ ] **Step 3: Write minimal implementation**

Create `packages/optio-claudecode/src/optio_claudecode/snapshots.py`:

```python
"""MongoDB `{prefix}_claudecode_session_snapshots` collection helpers.

One document per terminal run per process_id. Layout:

    {
      _id:             ObjectId,
      processId:       str,
      capturedAt:      datetime,
      endState:        str,          # "done" | "failed" | "cancelled"
      sessionBlobId:   ObjectId,     # GridFS — encrypted tar.gz of home/.claude
      workdirBlobId:   ObjectId,     # GridFS — plaintext tar.gz of workdir minus home/.claude
      deliverablesEmitted: list,     # audit metadata only; not replayed
    }

No `sessionId`: claude resolves its own most-recent session via `--continue`,
so optio does not record or replay a session UUID. This is the only schema
divergence from optio-opencode's parallel module.

Retention: keep the latest `SNAPSHOT_RETENTION` per processId. Older rows
are deleted by `prune_snapshots` and their GridFS blobs are expected to be
deleted by the caller using the ids returned.
"""

from __future__ import annotations

from datetime import datetime, timezone
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase


SESSION_SNAPSHOT_COLLECTION_SUFFIX = "_claudecode_session_snapshots"
SNAPSHOT_RETENTION = 5


def _collection(db: AsyncIOMotorDatabase, prefix: str):
    return db[f"{prefix}{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"]


async def ensure_indexes(db: AsyncIOMotorDatabase, prefix: str) -> None:
    """Idempotent index creation — called lazily by insert_snapshot."""
    await _collection(db, prefix).create_index(
        [("processId", 1), ("capturedAt", -1)],
        name="by_processId_capturedAt_desc",
    )


async def insert_snapshot(
    db: AsyncIOMotorDatabase,
    *,
    prefix: str,
    process_id: str,
    end_state: str,
    session_blob_id: ObjectId,
    workdir_blob_id: ObjectId,
    deliverables_emitted: list,
) -> dict:
    await ensure_indexes(db, prefix)
    doc = {
        "processId": process_id,
        "capturedAt": datetime.now(timezone.utc),
        "endState": end_state,
        "sessionBlobId": session_blob_id,
        "workdirBlobId": workdir_blob_id,
        "deliverablesEmitted": deliverables_emitted,
    }
    result = await _collection(db, prefix).insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def load_latest_snapshot(
    db: AsyncIOMotorDatabase, *, prefix: str, process_id: str,
) -> dict | None:
    return await _collection(db, prefix).find_one(
        {"processId": process_id}, sort=[("capturedAt", -1)],
    )


async def prune_snapshots(
    db: AsyncIOMotorDatabase, *, prefix: str, process_id: str,
) -> list[dict]:
    """Keep the latest SNAPSHOT_RETENTION; delete the rest.

    Returns a list of `{sessionBlobId, workdirBlobId}` dicts for the
    deleted snapshots so the caller can remove the corresponding GridFS
    blobs.
    """
    coll = _collection(db, prefix)
    all_docs = await coll.find(
        {"processId": process_id},
        projection={"sessionBlobId": 1, "workdirBlobId": 1, "capturedAt": 1},
        sort=[("capturedAt", -1)],
    ).to_list(None)
    stale = all_docs[SNAPSHOT_RETENTION:]
    if not stale:
        return []
    stale_ids = [d["_id"] for d in stale]
    await coll.delete_many({"_id": {"$in": stale_ids}})
    return [
        {"sessionBlobId": d["sessionBlobId"], "workdirBlobId": d["workdirBlobId"]}
        for d in stale
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest packages/optio-claudecode/tests/test_snapshots.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/snapshots.py \
        packages/optio-claudecode/tests/test_snapshots.py
git commit -m "feat(optio-claudecode): snapshot collection helpers for resume"
```

---

## Task 2: Config surface additions

Add the four resume fields and the asymmetric-crypto validation to `ClaudeCodeTaskConfig`, mirroring `OpencodeTaskConfig`.

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/types.py`
- Test: `packages/optio-claudecode/tests/test_session_blob_hooks.py`

- [ ] **Step 1: Write the failing test**

Create `packages/optio-claudecode/tests/test_session_blob_hooks.py` (config-validation portion only for now; the capture/source checks are added in Task 5 Step where noted — but write the full file here so it exists; the capture test references `session._capture_snapshot` which lands in Task 5):

```python
"""Tests for the optional session_blob_encrypt / session_blob_decrypt hooks."""

import pytest

from optio_claudecode.types import ClaudeCodeTaskConfig


def test_both_hooks_none_is_valid():
    cfg = ClaudeCodeTaskConfig(consumer_instructions="x")
    assert cfg.session_blob_encrypt is None
    assert cfg.session_blob_decrypt is None


def test_both_hooks_set_is_valid():
    cfg = ClaudeCodeTaskConfig(
        consumer_instructions="x",
        session_blob_encrypt=lambda b: b,
        session_blob_decrypt=lambda b: b,
    )
    assert cfg.session_blob_encrypt is not None
    assert cfg.session_blob_decrypt is not None


def test_only_encrypt_set_raises():
    with pytest.raises(ValueError) as exc:
        ClaudeCodeTaskConfig(
            consumer_instructions="x",
            session_blob_encrypt=lambda b: b,
        )
    assert "session_blob_encrypt" in str(exc.value)
    assert "session_blob_decrypt" in str(exc.value)


def test_only_decrypt_set_raises():
    with pytest.raises(ValueError) as exc:
        ClaudeCodeTaskConfig(
            consumer_instructions="x",
            session_blob_decrypt=lambda b: b,
        )
    assert "session_blob_encrypt" in str(exc.value)
    assert "session_blob_decrypt" in str(exc.value)


def test_supports_resume_defaults_true():
    cfg = ClaudeCodeTaskConfig(consumer_instructions="x")
    assert cfg.supports_resume is True


def test_workdir_exclude_defaults_none():
    cfg = ClaudeCodeTaskConfig(consumer_instructions="x")
    assert cfg.workdir_exclude is None


def test_on_resume_refresh_defaults_none():
    cfg = ClaudeCodeTaskConfig(consumer_instructions="x")
    assert cfg.on_resume_refresh is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest packages/optio-claudecode/tests/test_session_blob_hooks.py -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'session_blob_encrypt'` (and missing-attr failures).

- [ ] **Step 3: Write minimal implementation**

Edit `packages/optio-claudecode/src/optio_claudecode/types.py`. Change the imports line:

```python
from dataclasses import dataclass
from typing import Any, Callable, Literal
```

Add the fields to `ClaudeCodeTaskConfig` immediately after the existing `on_deliverable` field (before `__post_init__`):

```python
    on_deliverable: DeliverableCallback | None = None

    # --- resume surface (mirrors OpencodeTaskConfig) --------------------
    supports_resume: bool = True
    workdir_exclude: list[str] | None = None
    # Optional pair of synchronous bytes->bytes transforms wrapping the
    # home/.claude session tar at GridFS write/read. Both set → encrypted
    # at rest; both None (default) → plaintext. Setting only one is a
    # config error (asymmetric usage is always a mistake).
    session_blob_encrypt: Callable[[bytes], bytes] | None = None
    session_blob_decrypt: Callable[[bytes], bytes] | None = None
    # Optional hook fired on resume only (never on fresh start). Receives
    # the original config; returns a (possibly mutated) config. The harness
    # re-renders AGENTS.md from the returned config and writes it back only
    # when it differs from the file on disk, tagging the next resume.log
    # line with `REFRESHED:AGENTS.md`. None (default) → no refresh.
    on_resume_refresh: "Callable[[ClaudeCodeTaskConfig], ClaudeCodeTaskConfig] | None" = None
```

In `__post_init__`, append the asymmetric-crypto check after the existing validation:

```python
    def __post_init__(self) -> None:
        if self.permission_mode is not None and self.permission_mode not in _VALID_PERMISSION_MODES:
            raise ValueError(
                f"ClaudeCodeTaskConfig.permission_mode={self.permission_mode!r} "
                f"is not one of {sorted(_VALID_PERMISSION_MODES)}"
            )
        for field_name in ("claude_install_dir", "ttyd_install_dir"):
            val = getattr(self, field_name)
            if val is not None and not val.startswith("/") and not val.startswith("~"):
                raise ValueError(
                    f"ClaudeCodeTaskConfig.{field_name}={val!r} must be an "
                    f"absolute path (start with '/' or '~')."
                )
        e = self.session_blob_encrypt is not None
        d = self.session_blob_decrypt is not None
        if e != d:
            raise ValueError(
                "ClaudeCodeTaskConfig: session_blob_encrypt and "
                "session_blob_decrypt must be set together (both callables) "
                "or both left as None; one without the other is a config error."
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest packages/optio-claudecode/tests/test_session_blob_hooks.py -v`
Expected: PASS for the 7 config tests defined so far. (The capture-through-encrypt test is added in Task 5.)

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/types.py \
        packages/optio-claudecode/tests/test_session_blob_hooks.py
git commit -m "feat(optio-claudecode): resume config fields + crypto-pair validation"
```

---

## Task 3: Resume prompt section

Render the claudecode resume section: byte-identical to opencode's text plus one added bullet stating `home/.claude/` is preserved.

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/prompt.py`
- Test: `packages/optio-claudecode/tests/test_resume_prompt.py`

- [ ] **Step 1: Write the failing test**

Create `packages/optio-claudecode/tests/test_resume_prompt.py`:

```python
"""Tests for the claudecode resume prompt section."""

from optio_claudecode.prompt import _render_resume_section, compose_agents_md


def test_render_mentions_resume_log():
    out = _render_resume_section(None)
    assert "## Resumes" in out
    assert "resume.log" in out


def test_render_mentions_home_claude_preserved():
    """Claudecode-specific bullet: home/.claude/ survives resumes."""
    out = _render_resume_section(None)
    assert "home/.claude/" in out


def test_render_default_excludes_listed():
    from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES
    out = _render_resume_section(None)
    for pattern in DEFAULT_WORKDIR_EXCLUDES:
        assert f"`{pattern}`" in out


def test_render_custom_excludes_listed_and_defaults_absent():
    from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES
    out = _render_resume_section(["custom_a", "custom_b"])
    assert "`custom_a`" in out
    assert "`custom_b`" in out
    for pattern in DEFAULT_WORKDIR_EXCLUDES:
        assert f"`{pattern}`" not in out


def test_render_empty_excludes_says_no_paths_excluded():
    out = _render_resume_section([])
    assert "No paths are excluded" in out


def test_compose_includes_resume_section_by_default():
    out = compose_agents_md("hi", workdir_exclude=None, supports_resume=True)
    assert "## Resumes" in out
    assert "home/.claude/" in out


def test_compose_omits_resume_section_when_disabled():
    out = compose_agents_md("hi", workdir_exclude=None, supports_resume=False)
    assert "## Resumes" not in out
    assert "resume.log" not in out


def test_compose_appends_consumer_instructions_verbatim():
    out = compose_agents_md("compute 2+2", workdir_exclude=None, supports_resume=True)
    assert out.endswith("compute 2+2\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest packages/optio-claudecode/tests/test_resume_prompt.py -v`
Expected: FAIL — `ImportError: cannot import name '_render_resume_section'` and `compose_agents_md` rejects the new keyword args.

- [ ] **Step 3: Write minimal implementation**

Replace the whole body of `packages/optio-claudecode/src/optio_claudecode/prompt.py`:

```python
"""AGENTS.md composer for optio-claudecode.

Renders the claudecode resume section and forwards to the shared
``optio_host.agents.compose_agents_md``. The resume text is byte-identical
to optio-opencode's, with one added bullet: ``home/.claude/`` (credentials,
settings, conversation transcript) is preserved across resumes — claudecode
needs this because all sensitive agent-continuity state lives there.
"""

from optio_host.agents import (
    BASE_PROMPT_PRE,
    BASE_PROMPT_POST,
    compose_agents_md as _compose_agents_md_host,
)


__all__ = ["BASE_PROMPT_PRE", "BASE_PROMPT_POST", "compose_agents_md"]


RESUME_SECTION_TEMPLATE = """## Resumes

This harness may pause your session, save your context to a database,
terminate the underlying process, and later rehydrate it. From your
point of view the conversation is fully continuous — you keep your
prior context and will not "notice" the resume.

**A resume can happen at any point, not only at the start.** The host
environment may have changed across a resume — different host,
different running processes, files outside this workdir gone — even
though your context remembers everything as alive and well.

**The workdir (this directory) is preserved across resumes, with two
caveats:**

- {excludes_clause}
- **Anything outside the workdir is not preserved.**

- **Your `home/.claude/` directory — credentials, settings, and the
  conversation transcript — IS preserved across resumes**, so your
  identity and history travel with you even when the underlying process
  and host change.

{outside_clause}

### Detecting a resume: `resume.log`

Each session start (fresh or resumed) appends one line to
`./resume.log`. Line format:

```
<ISO 8601 UTC timestamp>[ REFRESHED:<comma-separated filenames>]
```

The very first line is the original launch timestamp; each subsequent
line is a resume. The optional `REFRESHED:` suffix signals that the
harness rewrote the listed files on that resume (e.g.
`2026-05-28T13:15:42Z REFRESHED:AGENTS.md`) — your in-memory copy of
those files is stale and must be re-read before continuing.

**At the start of every new incoming user message, read
`./resume.log` first.** Compare the latest line to the value you
remembered last time you checked. If a new line has appeared, treat
the situation as a resume:

- Verify any tools, processes, or files you previously gathered
  outside the workdir are still where you left them.
- Re-establish anything that's gone (re-launch a server, re-fetch a
  file, etc.) before continuing.
- **If the latest line carries a `REFRESHED:` suffix, re-read each
  listed file** (e.g. `cat ./AGENTS.md`) — the harness updated it
  since your last context snapshot and the version you remember is
  out of date.
- Then resume the work you were doing.

If a resume slips past unnoticed, a failing tool call is the
next-best signal — re-check `./resume.log` then.
"""


def _render_resume_section(workdir_exclude: list[str] | None) -> str:
    """Render the RESUME_SECTION_TEMPLATE with the effective exclude list."""
    from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES
    effective = workdir_exclude if workdir_exclude is not None else DEFAULT_WORKDIR_EXCLUDES
    if not effective:
        excludes_clause = (
            "**No paths are excluded** — every file in the workdir is preserved."
        )
        outside_clause = (
            "If you need to stash large data, place it outside the workdir "
            "(e.g. `/tmp/`) — but remember it may be missing when you next look."
        )
    else:
        excludes_str = ", ".join(f"`{p}`" for p in effective)
        excludes_clause = (
            f"**Paths matching the snapshot exclude list are NOT preserved**, "
            f"even inside the workdir. The current exclude list is: {excludes_str}."
        )
        outside_clause = (
            "If you need to stash large data, place it outside the workdir "
            "(e.g. `/tmp/`) or inside an excluded subdirectory — but remember "
            "any such location may be missing when you next look."
        )
    return RESUME_SECTION_TEMPLATE.format(
        excludes_clause=excludes_clause,
        outside_clause=outside_clause,
    )


def compose_agents_md(
    consumer_instructions: str,
    *,
    workdir_exclude: list[str] | None = None,
    supports_resume: bool = True,
) -> str:
    """Render <workdir>/AGENTS.md for an optio-claudecode task.

    Renders the claudecode resume section when ``supports_resume`` is
    True and forwards everything else to the shared host composer.
    """
    resume_section = _render_resume_section(workdir_exclude) if supports_resume else None
    return _compose_agents_md_host(
        consumer_instructions, resume_section=resume_section,
    )
```

> Note: the existing v1 caller `compose_agents_md(config.consumer_instructions)` in `session.py` keeps working because `workdir_exclude` and `supports_resume` have defaults. Task 5 changes that call site to pass them explicitly.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest packages/optio-claudecode/tests/test_resume_prompt.py packages/optio-claudecode/tests/test_prompt.py -v`
Expected: PASS (new resume-prompt tests + existing `test_prompt.py` still green).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/prompt.py \
        packages/optio-claudecode/tests/test_resume_prompt.py
git commit -m "feat(optio-claudecode): resume prompt section with home/.claude clause"
```

---

## Task 4: `build_claude_flags` appends `--continue` on resume

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py:359-378`
- Test: `packages/optio-claudecode/tests/test_host_actions.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-claudecode/tests/test_host_actions.py`:

```python
from optio_claudecode.host_actions import build_claude_flags


def test_build_claude_flags_no_continue_by_default():
    flags = build_claude_flags(
        permission_mode=None, allowed_tools=None, disallowed_tools=None,
    )
    assert "--continue" not in flags


def test_build_claude_flags_appends_continue_when_resuming():
    flags = build_claude_flags(
        permission_mode="bypassPermissions",
        allowed_tools=None, disallowed_tools=None,
        resuming=True,
    )
    assert "--continue" in flags
    # permission flags still present and ordered before the resume flag
    assert flags.index("--permission-mode") < flags.index("--continue")


def test_build_claude_flags_continue_is_last():
    flags = build_claude_flags(
        permission_mode=None,
        allowed_tools=["Read"],
        disallowed_tools=None,
        resuming=True,
    )
    assert flags[-1] == "--continue"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest packages/optio-claudecode/tests/test_host_actions.py -k continue -v`
Expected: FAIL — `TypeError: build_claude_flags() got an unexpected keyword argument 'resuming'`.

- [ ] **Step 3: Write minimal implementation**

Edit `build_claude_flags` in `packages/optio-claudecode/src/optio_claudecode/host_actions.py`:

```python
def build_claude_flags(
    *,
    permission_mode: str | None,
    allowed_tools: list[str] | None,
    disallowed_tools: list[str] | None,
    resuming: bool = False,
) -> list[str]:
    """Translate ClaudeCodeTaskConfig permission knobs to an argv list.

    Empty lists are treated as None: no flag is emitted.
    When ``resuming`` is True, ``--continue`` is appended so claude picks
    up the most recent conversation in ``home/.claude/projects/<cwd>/``.
    Validation of ``permission_mode`` values lives in
    ``ClaudeCodeTaskConfig.__post_init__``.
    """
    out: list[str] = []
    if permission_mode is not None:
        out += ["--permission-mode", permission_mode]
    if allowed_tools:
        out += ["--allowed-tools", ",".join(allowed_tools)]
    if disallowed_tools:
        out += ["--disallowed-tools", ",".join(disallowed_tools)]
    if resuming:
        out += ["--continue"]
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest packages/optio-claudecode/tests/test_host_actions.py -v`
Expected: PASS (new flag tests + existing host_actions tests).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py \
        packages/optio-claudecode/tests/test_host_actions.py
git commit -m "feat(optio-claudecode): build_claude_flags --continue on resume"
```

---

## Task 5: Session wiring — capture, resume, fresh/resume branch

This is the core port. It adds resume helpers, hoists the resume decision/restore ahead of the protocol session, branches the body fresh-vs-resume, captures a snapshot in the `finally`, and flips `create_claudecode_task` to propagate `config.supports_resume`.

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py`
- Test (created here): `packages/optio-claudecode/tests/test_session_resume.py`, `packages/optio-claudecode/tests/test_session_resume_decrypt_failure.py`, `packages/optio-claudecode/tests/test_on_resume_refresh.py`
- Test (extended here): `packages/optio-claudecode/tests/test_session_blob_hooks.py`

### 5a — fake_claude argv recording + new scenarios (prerequisite for the e2e tests)

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-claudecode/tests/fake_claude.py`'s scenario behavior expectations by first adding a test in `packages/optio-claudecode/tests/test_sanity.py`:

```python
def test_fake_claude_has_resume_scenarios():
    import importlib.util, pathlib
    p = pathlib.Path(__file__).parent / "fake_claude.py"
    spec = importlib.util.spec_from_file_location("fake_claude_probe", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "long_then_signaled" in mod.SCENARIOS
    assert "idempotent_done" in mod.SCENARIOS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest packages/optio-claudecode/tests/test_sanity.py -k resume_scenarios -v`
Expected: FAIL — assertion error (scenarios not present).

- [ ] **Step 3: Write minimal implementation**

Edit `packages/optio-claudecode/tests/fake_claude.py`. Extend `SCENARIOS` and add the two scenario functions and an argv-recording side effect. Replace the `SCENARIOS` tuple and `main()` dispatch, and add functions:

```python
SCENARIOS = (
    "happy", "deliverable", "error", "long",
    "long_then_signaled", "idempotent_done",
)


def _record_argv(argv: list[str]) -> None:
    """Record the argv claude was launched with, so resume tests can
    assert that ``--continue`` was passed. Written under the isolated
    HOME (``$HOME`` is ``<workdir>/home`` under HOME-isolation) so it
    travels in the session blob, not the plaintext workdir blob."""
    home = os.environ.get("HOME")
    if not home:
        return
    target = Path(home) / ".claude" / "fake_claude_argv.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    import json
    # Append one JSON line per launch so multiple runs are observable.
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(argv) + "\n")
        fh.flush()


def _scenario_long_then_signaled() -> None:
    # Emit a STATUS so the dashboard sees life, then stay alive
    # indefinitely until SIGTERM/SIGKILL from the framework.
    _log("STATUS: 10% long-running, awaiting signal")
    while True:
        time.sleep(0.5)


def _scenario_idempotent_done() -> None:
    # Emits the same DONE line as `happy`; used across two runs to verify
    # the agent's perspective of continuity survives capture+restore.
    time.sleep(0.05)
    _log("STATUS: 10% resumed claude alive")
    time.sleep(0.05)
    _log("DONE: scenario completed")
    time.sleep(30.0)
```

Then in `main()`, after computing `scenario`, record argv and extend the dispatch dict:

```python
    scenario = os.environ.get("FAKE_CLAUDE_SCENARIO", "happy").strip()
    if scenario not in SCENARIOS:
        print(f"unknown FAKE_CLAUDE_SCENARIO={scenario!r}", file=sys.stderr)
        return 2
    _record_argv(sys.argv[1:])
    {
        "happy": _scenario_happy,
        "deliverable": _scenario_deliverable,
        "error": _scenario_error,
        "long": _scenario_long,
        "long_then_signaled": _scenario_long_then_signaled,
        "idempotent_done": _scenario_idempotent_done,
    }[scenario]()
    return 0
```

Add `import json` at module top if not already imported (the helper imports it locally, so this is optional; leave the local import to keep the diff minimal).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest packages/optio-claudecode/tests/test_sanity.py -k resume_scenarios -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/tests/fake_claude.py \
        packages/optio-claudecode/tests/test_sanity.py
git commit -m "test(optio-claudecode): fake_claude resume scenarios + argv recording"
```

### 5b — session.py resume/capture wiring

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-claudecode/tests/test_session_resume.py`:

```python
"""Full-cycle resume test for optio-claudecode against fake_claude.py."""

import asyncio
import json
import os
import pathlib

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_claudecode import ClaudeCodeTaskConfig
from optio_claudecode.session import run_claudecode_session
from optio_claudecode.snapshots import (
    SESSION_SNAPSHOT_COLLECTION_SUFFIX,
    load_latest_snapshot,
)
from optio_host.paths import task_dir


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_cc_test_resume_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


@pytest.fixture
def task_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIO_CLAUDECODE_TASK_ROOT", str(tmp_path))
    return tmp_path


async def _make_ctx(mongo_db, process_id: str, *, resume: bool):
    task = TaskInstance(
        execute=lambda c: None,  # type: ignore[arg-type, return-value]
        process_id=process_id, name=process_id, supports_resume=True,
    )
    proc = await upsert_process(mongo_db, "test", task)
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"status": {"state": "running"}}},
    )
    return ProcessContext(
        process_oid=proc["_id"],
        process_id=process_id,
        root_oid=proc["_id"],
        depth=0,
        params={},
        services={},
        db=mongo_db,
        prefix="test",
        cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
        resume=resume,
    )


def _cfg(shim_install_dir, scenario: str) -> ClaudeCodeTaskConfig:
    return ClaudeCodeTaskConfig(
        consumer_instructions=f"(scenario: {scenario})",
        claude_install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        permission_mode="bypassPermissions",
        supports_resume=True,
        session_blob_encrypt=lambda b: b,
        session_blob_decrypt=lambda b: b,
    )


async def _run_cycle(mongo_db, pid, shim_install_dir, scenario, resume, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", scenario)
    ctx = await _make_ctx(mongo_db, pid, resume=resume)
    await run_claudecode_session(ctx, _cfg(shim_install_dir, scenario))


async def test_terminal_flow_captures_snapshot(mongo_db, task_root, shim_install_dir, monkeypatch):
    pid = "cc_terminal_1"
    await _run_cycle(mongo_db, pid, shim_install_dir, "happy", False, monkeypatch)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    assert snap is not None
    assert snap["endState"] == "done"
    assert "sessionBlobId" in snap and "workdirBlobId" in snap

    proc = await mongo_db["test_processes"].find_one({"processId": pid})
    assert proc["hasSavedState"] is True


async def test_session_blob_excludes_home_claude_from_workdir_blob(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    """The plaintext workdir blob must NOT contain home/.claude (defensive
    rm -rf at capture). The session blob is where home/.claude lives."""
    import io, tarfile
    pid = "cc_split_1"
    await _run_cycle(mongo_db, pid, shim_install_dir, "happy", False, monkeypatch)
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)

    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    wstream = await bucket.open_download_stream(snap["workdirBlobId"])
    workdir_bytes = await wstream.read()
    with tarfile.open(fileobj=io.BytesIO(workdir_bytes), mode="r:gz") as tar:
        names = tar.getnames()
    assert not any("home/.claude" in n for n in names), names

    sstream = await bucket.open_download_stream(snap["sessionBlobId"])
    session_bytes = await sstream.read()
    with tarfile.open(fileobj=io.BytesIO(session_bytes), mode="r:gz") as tar:
        snames = tar.getnames()
    assert any("home/.claude" in n for n in snames), snames


async def test_resume_creates_second_snapshot_and_passes_continue(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    import io, tarfile
    pid = "cc_resume_1"
    await _run_cycle(mongo_db, pid, shim_install_dir, "idempotent_done", False, monkeypatch)
    await _run_cycle(mongo_db, pid, shim_install_dir, "idempotent_done", True, monkeypatch)

    count = await mongo_db[f"test{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"].count_documents(
        {"processId": pid}
    )
    assert count == 2

    # The latest session blob carries home/.claude/fake_claude_argv.json
    # written by fake_claude. The resumed launch line must contain --continue.
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    sstream = await bucket.open_download_stream(snap["sessionBlobId"])
    session_bytes = await sstream.read()
    with tarfile.open(fileobj=io.BytesIO(session_bytes), mode="r:gz") as tar:
        member = next(m for m in tar.getmembers() if m.name.endswith("fake_claude_argv.json"))
        argv_lines = tar.extractfile(member).read().decode("utf-8").splitlines()
    launches = [json.loads(line) for line in argv_lines if line]
    # First launch: no --continue. Second (resume): --continue present.
    assert "--continue" not in launches[0]
    assert any("--continue" in launch for launch in launches[1:])


async def test_resume_with_no_prior_snapshot_falls_back_to_fresh(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    pid = "cc_resume_no_prior"
    await _run_cycle(mongo_db, pid, shim_install_dir, "happy", True, monkeypatch)
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    assert snap is not None  # fresh-start cycle still captures a terminal snapshot
```

Create `packages/optio-claudecode/tests/test_session_resume_decrypt_failure.py`:

```python
"""Resume with a corrupted session blob must fail loud, not fresh-start."""

import asyncio
import os

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_claudecode import ClaudeCodeTaskConfig
from optio_claudecode.session import run_claudecode_session
from optio_claudecode.snapshots import load_latest_snapshot


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_cc_test_decrypt_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


@pytest.fixture
def task_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIO_CLAUDECODE_TASK_ROOT", str(tmp_path))
    return tmp_path


async def _make_ctx(mongo_db, pid, *, resume):
    task = TaskInstance(
        execute=lambda c: None,  # type: ignore[arg-type, return-value]
        process_id=pid, name=pid, supports_resume=True,
    )
    proc = await upsert_process(mongo_db, "test", task)
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"status": {"state": "running"}}},
    )
    return ProcessContext(
        process_oid=proc["_id"], process_id=pid, root_oid=proc["_id"],
        depth=0, params={}, services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
        resume=resume,
    )


def _raise_on_decrypt(_b: bytes) -> bytes:
    raise ValueError("session blob decrypt failed: bad key or tampering")


async def test_decrypt_failure_propagates_and_no_fresh_start(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    pid = "cc_decrypt_fail"
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")

    # First cycle: capture a (plaintext) snapshot.
    ctx1 = await _make_ctx(mongo_db, pid, resume=False)
    cfg1 = ClaudeCodeTaskConfig(
        consumer_instructions="x",
        claude_install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        supports_resume=True,
    )
    await run_claudecode_session(ctx1, cfg1)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    assert snap is not None

    # Resume with a decrypt hook that raises. The session must raise and
    # must NOT silently fall through to a fresh start (which would emit a
    # NEW snapshot). We assert the raise propagates.
    ctx2 = await _make_ctx(mongo_db, pid, resume=True)
    cfg2 = ClaudeCodeTaskConfig(
        consumer_instructions="x",
        claude_install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        supports_resume=True,
        session_blob_encrypt=lambda b: b,
        session_blob_decrypt=_raise_on_decrypt,
    )
    with pytest.raises(Exception) as exc:
        await run_claudecode_session(ctx2, cfg2)
    assert "decrypt" in repr(exc.value).lower()
```

Create `packages/optio-claudecode/tests/test_on_resume_refresh.py`:

```python
"""on_resume_refresh fires only on resume and rewrites AGENTS.md."""

import asyncio
import io
import os
import tarfile

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_claudecode import ClaudeCodeTaskConfig
from optio_claudecode.session import run_claudecode_session
from optio_claudecode.snapshots import load_latest_snapshot


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_cc_test_refresh_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


@pytest.fixture
def task_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIO_CLAUDECODE_TASK_ROOT", str(tmp_path))
    return tmp_path


async def _make_ctx(mongo_db, pid, *, resume):
    task = TaskInstance(
        execute=lambda c: None,  # type: ignore[arg-type, return-value]
        process_id=pid, name=pid, supports_resume=True,
    )
    proc = await upsert_process(mongo_db, "test", task)
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"status": {"state": "running"}}},
    )
    return ProcessContext(
        process_oid=proc["_id"], process_id=pid, root_oid=proc["_id"],
        depth=0, params={}, services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
        resume=resume,
    )


def _bump_instructions(cfg: ClaudeCodeTaskConfig) -> ClaudeCodeTaskConfig:
    import dataclasses
    return dataclasses.replace(
        cfg, consumer_instructions=cfg.consumer_instructions + " [REFRESHED]",
    )


async def test_resume_refresh_tags_resume_log(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    pid = "cc_refresh_1"
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "idempotent_done")

    base = dict(
        claude_install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        supports_resume=True,
    )

    ctx1 = await _make_ctx(mongo_db, pid, resume=False)
    await run_claudecode_session(
        ctx1, ClaudeCodeTaskConfig(consumer_instructions="orig", **base),
    )

    ctx2 = await _make_ctx(mongo_db, pid, resume=True)
    await run_claudecode_session(
        ctx2,
        ClaudeCodeTaskConfig(
            consumer_instructions="orig", on_resume_refresh=_bump_instructions, **base,
        ),
    )

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    stream = await bucket.open_download_stream(snap["workdirBlobId"])
    workdir_bytes = await stream.read()
    with tarfile.open(fileobj=io.BytesIO(workdir_bytes), mode="r:gz") as tar:
        member = tar.getmember("resume.log")
        contents = tar.extractfile(member).read().decode("utf-8")
    lines = [l for l in contents.splitlines() if l]
    assert len(lines) == 2
    assert "REFRESHED:AGENTS.md" in lines[1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest packages/optio-claudecode/tests/test_session_resume.py packages/optio-claudecode/tests/test_session_resume_decrypt_failure.py packages/optio-claudecode/tests/test_on_resume_refresh.py -v`
Expected: FAIL — `run_claudecode_session` does not capture snapshots, `supports_resume` is hardcoded `False` on the task, no resume restore, no `--continue`.

- [ ] **Step 3: Write the implementation**

Rewrite `packages/optio-claudecode/src/optio_claudecode/session.py`. Replace the whole file:

```python
"""State machine for one optio-claudecode session.

Orchestrates a Host (local or remote) through install → (resume restore |
fresh plant) → launch ttyd(claude) → protocol session → snapshot capture.

Most protocol plumbing lives in optio-host. This module does the
claudecode-specific orchestration plus the resume/snapshot brackets,
mirroring optio-opencode's session module. The one structural difference
from opencode: sensitive state is the ``<workdir>/home/.claude/`` subtree
(tarred + optionally encrypted) rather than an exported session DB.
"""

from __future__ import annotations

import logging
import os
import shlex
from datetime import datetime, timezone
from typing import AsyncIterator, Callable

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance

from optio_host.context import HookContext
from optio_host.host import Host, LocalHost, ProcessHandle, RemoteHost
from optio_host.paths import task_dir
from optio_host.protocol.session import _SessionFailed, run_log_protocol_session

from optio_claudecode import host_actions
from optio_claudecode.prompt import compose_agents_md
from optio_claudecode.snapshots import (
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)
from optio_claudecode.types import ClaudeCodeTaskConfig


_LOG = logging.getLogger(__name__)

READY_TIMEOUT_S = 30.0


def _build_host(config: ClaudeCodeTaskConfig, process_id: str) -> Host:
    """Construct the appropriate Host for the given config.

    Extracted so tests monkeypatch ``session._build_host`` to inject a
    fake host (mirrors the opencode pattern).
    """
    taskdir = task_dir(
        ssh=config.ssh, process_id=process_id, consumer_name="optio-claudecode",
    )
    if config.ssh is None:
        os.makedirs(taskdir, exist_ok=True)
        host: Host = LocalHost(taskdir=taskdir)
        os.makedirs(host.workdir, exist_ok=True)
        return host
    return RemoteHost(ssh_config=config.ssh, taskdir=taskdir)


async def run_claudecode_session(
    ctx: ProcessContext, config: ClaudeCodeTaskConfig,
) -> None:
    """Execute function body for one optio-claudecode task instance."""
    host: Host = _build_host(config, ctx.process_id)
    launched_handle: ProcessHandle | None = None
    cancelled = False

    await host.connect()
    await host.setup_workdir()

    hook_ctx_outer = HookContext(ctx, host)
    claude_path = await host_actions.ensure_claude_installed(
        hook_ctx_outer,
        install_if_missing=config.install_if_missing,
        install_dir=config.claude_install_dir,
    )
    ttyd_path = await host_actions.ensure_ttyd_installed(
        hook_ctx_outer,
        install_if_missing=config.install_ttyd_if_missing,
        install_dir=config.ttyd_install_dir,
    )

    # --- resume decision (BEFORE the protocol session starts) -------------
    # Restore must happen before run_log_protocol_session subscribes its
    # tail, so the driver does not replay the previous run's DONE/ERROR
    # out of the restored optio.log.
    resume_requested = bool(getattr(ctx, "resume", False))
    snapshot: dict | None = None
    if resume_requested:
        snapshot = await load_latest_snapshot(
            ctx._db, prefix=ctx._prefix, process_id=ctx.process_id,
        )

    resuming = snapshot is not None
    if resuming:
        # Plaintext workdir first (establishes the tree incl. home/), then
        # decrypt + extract home/.claude on top. Decrypt failure is treated
        # as tampering/key-rotation and propagated — never silent fresh-start.
        await host.restore_workdir(_stream_blob(ctx, snapshot["workdirBlobId"]))
        payload = await _read_blob_bytes(ctx, snapshot["sessionBlobId"])
        decrypt = config.session_blob_decrypt or (lambda b: b)
        plain = decrypt(payload)
        await _extract_home_claude(host, plain)
        await _rotate_optio_log(host)

    async def _claudecode_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle

        refreshed_files: list[str] = []
        if not resuming:
            # Fresh start: protocol driver has created workdir,
            # deliverables/, and an empty optio.log. Plant per-task HOME
            # files and AGENTS.md before launching ttyd.
            await host_actions.plant_home_files(
                host,
                credentials_json=config.credentials_json,
                claude_config=config.claude_config,
            )
            await host.write_text(
                "AGENTS.md",
                compose_agents_md(
                    config.consumer_instructions,
                    workdir_exclude=config.workdir_exclude,
                    supports_resume=config.supports_resume,
                ),
            )
        else:
            # Resume: home/.claude (credentials, settings) was restored from
            # the session blob — do NOT re-plant. Optionally refresh AGENTS.md.
            refreshed_files = await _maybe_refresh_on_resume(host, hook_ctx, config)

        if config.supports_resume:
            await _append_resume_log_entry(host, refreshed=refreshed_files)

        if config.before_execute is not None:
            await config.before_execute(hook_ctx)

        bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
        upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
        ttyd_iface = bind_addr if isinstance(host, LocalHost) else "127.0.0.1"

        claude_flags = host_actions.build_claude_flags(
            permission_mode=config.permission_mode,
            allowed_tools=config.allowed_tools,
            disallowed_tools=config.disallowed_tools,
            resuming=resuming,
        )
        ctx.report_progress(None, "Launching claude (ttyd)…")
        handle, ttyd_port = await host_actions.launch_ttyd_with_claude(
            host,
            ttyd_path=ttyd_path,
            claude_path=claude_path,
            bind_iface=ttyd_iface,
            extra_env=config.env,
            claude_flags=claude_flags,
            ready_timeout_s=READY_TIMEOUT_S,
        )
        launched_handle = handle

        worker_port = await host.establish_tunnel(ttyd_port, bind_addr=bind_addr)
        await ctx.set_widget_upstream(f"http://{upstream_host}:{worker_port}")
        await ctx.set_widget_data({
            "iframeSrc": "{widgetProxyUrl}/",
        })
        ctx.report_progress(None, "claude is live")

        proc = launched_handle.pid_like
        await proc.wait()  # type: ignore[union-attr]

    try:
        await run_log_protocol_session(
            host, ctx,
            body=_claudecode_body,
            on_deliverable=config.on_deliverable,
            after_execute=config.after_execute,
        )
    except _SessionFailed as fail:
        raise RuntimeError(str(fail)) from None
    finally:
        if not ctx.should_continue():
            cancelled = True
        if launched_handle is not None:
            try:
                await host.terminate_subprocess(launched_handle, aggressive=cancelled)
            except Exception:
                _LOG.exception("terminate_subprocess failed")

        if config.supports_resume:
            try:
                await _capture_snapshot(
                    ctx, host,
                    end_state="cancelled" if cancelled else "done",
                    workdir_exclude=config.workdir_exclude,
                    session_blob_encrypt=config.session_blob_encrypt,
                )
            except Exception:
                _LOG.exception(
                    "snapshot capture failed; proceeding with workdir wipe",
                )

        try:
            await host.cleanup_taskdir(aggressive=cancelled)
        except Exception:
            _LOG.exception("cleanup_taskdir failed")
        try:
            await host.disconnect()
        except Exception:
            _LOG.exception("host.disconnect failed")


# --- helpers ---------------------------------------------------------------


async def _stream_blob(ctx: ProcessContext, blob_id) -> "AsyncIterator[bytes]":
    async with ctx.load_blob(blob_id) as reader:
        while True:
            chunk = await reader.read(1 << 20)
            if not chunk:
                break
            yield chunk


async def _read_blob_bytes(ctx: ProcessContext, blob_id) -> bytes:
    out = bytearray()
    async with ctx.load_blob(blob_id) as reader:
        while True:
            chunk = await reader.read(1 << 20)
            if not chunk:
                break
            out.extend(chunk)
    return bytes(out)


async def _archive_home_claude(host: Host) -> bytes:
    """tar.gz the sensitive ``home/.claude`` subtree and fetch it as bytes."""
    workdir = host.workdir.rstrip("/")
    tmpfile = f"{workdir}/.optio-claudecode-session.tar.gz"
    r = await host.run_command(
        f"tar -czf {shlex.quote(tmpfile)} -C {shlex.quote(workdir)} home/.claude"
    )
    if r.exit_code != 0:
        raise RuntimeError(
            f"tar home/.claude failed (exit {r.exit_code}): "
            f"{r.stderr.strip()[:200]}"
        )
    try:
        return await host.fetch_bytes_from_host(tmpfile)
    finally:
        await host.run_command(f"rm -f {shlex.quote(tmpfile)}")


async def _extract_home_claude(host: Host, plain: bytes) -> None:
    """Extract the decrypted ``home/.claude`` tar over the workdir."""
    workdir = host.workdir.rstrip("/")
    tmpfile = f"{workdir}/.optio-claudecode-restore.tar.gz"
    await host.put_file_to_host(plain, tmpfile)
    try:
        r = await host.run_command(
            f"tar -xzf {shlex.quote(tmpfile)} -C {shlex.quote(workdir)}"
        )
        if r.exit_code != 0:
            raise RuntimeError(
                f"tar -x home/.claude failed (exit {r.exit_code}): "
                f"{r.stderr.strip()[:200]}"
            )
    finally:
        await host.run_command(f"rm -f {shlex.quote(tmpfile)}")


async def _capture_snapshot(
    ctx: ProcessContext,
    host: Host,
    *,
    end_state: str,
    workdir_exclude: list[str] | None,
    session_blob_encrypt: "Callable[[bytes], bytes] | None" = None,
) -> None:
    # 1. tar the sensitive subtree into bytes.
    session_bytes = await _archive_home_claude(host)

    # 2. encrypt (or plaintext fallthrough).
    encrypt = session_blob_encrypt or (lambda b: b)
    payload = encrypt(session_bytes)
    expected_len = len(payload)

    # 3. write the session blob.
    async with ctx.store_blob("session") as swriter:
        await swriter.write(payload)
        session_blob_id = swriter.file_id
        written = getattr(swriter, "_position", None)
        if written is not None and written != expected_len:
            raise RuntimeError(
                f"snapshot session blob short-write: expected "
                f"{expected_len} bytes, GridIn._position is {written}"
            )

    # 4. defensive wipe so the workdir tar cannot carry sensitive state.
    workdir = host.workdir.rstrip("/")
    await host.run_command(f"rm -rf {shlex.quote(workdir)}/home/.claude")

    # 5. stream the plaintext workdir tar.
    async with ctx.store_blob("workdir") as wwriter:
        async for chunk in host.archive_workdir(workdir_exclude):
            await wwriter.write(chunk)
        workdir_blob_id = wwriter.file_id

    # 6. insert the snapshot doc.
    await insert_snapshot(
        ctx._db,
        prefix=ctx._prefix,
        process_id=ctx.process_id,
        end_state=end_state,
        session_blob_id=session_blob_id,
        workdir_blob_id=workdir_blob_id,
        deliverables_emitted=[],
    )

    # 7. prune + delete stale blobs.
    pruned = await prune_snapshots(
        ctx._db, prefix=ctx._prefix, process_id=ctx.process_id,
    )
    for p in pruned:
        try:
            await ctx.delete_blob(p["sessionBlobId"])
        except Exception:
            _LOG.exception("delete_blob(session) failed")
        try:
            await ctx.delete_blob(p["workdirBlobId"])
        except Exception:
            _LOG.exception("delete_blob(workdir) failed")

    # 8. surface the Resume affordance in the dashboard.
    await ctx.mark_has_saved_state()


async def _rotate_optio_log(host: Host) -> None:
    """Append the restored optio.log to optio.log.old, then truncate it.

    Copied verbatim from opencode. Preserves historical log content across
    consecutive resumes while ensuring the tail driver only sees fresh
    lines from the resumed run.
    """
    workdir = host.workdir.rstrip("/")
    log_abs = f"{workdir}/optio.log"
    old_abs = f"{workdir}/optio.log.old"
    try:
        current = (await host.fetch_bytes_from_host(log_abs)).decode("utf-8")
    except FileNotFoundError:
        current = ""
    if not current:
        await host.write_text("optio.log", "")
        return
    try:
        existing_old = (await host.fetch_bytes_from_host(old_abs)).decode("utf-8")
    except FileNotFoundError:
        existing_old = ""
    await host.write_text("optio.log.old", existing_old + current)
    await host.write_text("optio.log", "")


async def _append_resume_log_entry(
    host, *, refreshed: list[str] | None = None,
) -> None:
    """Append one line to ``<workdir>/resume.log``.

    Line format: ``<ISO 8601 UTC timestamp>[ REFRESHED:<comma-separated names>]``.
    Caller gates this on config.supports_resume.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = ts
    if refreshed:
        line = f"{ts} REFRESHED:{','.join(refreshed)}"
    target = f"{host.workdir}/resume.log"
    result = await host.run_command(
        f"echo {shlex.quote(line)} >> {shlex.quote(target)}"
    )
    if result.exit_code != 0:
        raise RuntimeError(
            f"failed to append to resume.log: exit {result.exit_code}: "
            f"{result.stderr!r}"
        )


async def _maybe_refresh_on_resume(
    host, hook_ctx, config: ClaudeCodeTaskConfig,
) -> list[str]:
    """Run on_resume_refresh (if any) and rewrite AGENTS.md when changed.

    Returns the list of filenames rewritten (currently at most
    ``["AGENTS.md"]``). A hook that raises is logged and ignored.
    """
    if config.on_resume_refresh is None:
        return []
    try:
        new_config = config.on_resume_refresh(config)
    except Exception:
        _LOG.exception(
            "on_resume_refresh raised; keeping existing AGENTS.md from snapshot",
        )
        return []
    new_agents_md = compose_agents_md(
        new_config.consumer_instructions,
        workdir_exclude=new_config.workdir_exclude,
        supports_resume=new_config.supports_resume,
    )
    try:
        existing = await hook_ctx.read_text_from_host("AGENTS.md", silent=True)
    except FileNotFoundError:
        existing = None
    except Exception:
        _LOG.exception(
            "failed to read existing AGENTS.md on resume; rewriting unconditionally",
        )
        existing = None
    if existing == new_agents_md:
        return []
    await host.write_text("AGENTS.md", new_agents_md)
    return ["AGENTS.md"]


def create_claudecode_task(
    process_id: str,
    name: str,
    config: ClaudeCodeTaskConfig,
    description: str | None = None,
) -> TaskInstance:
    """Return a TaskInstance that runs one optio-claudecode session."""

    async def _execute(ctx: ProcessContext) -> None:
        await run_claudecode_session(ctx, config)

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        ui_widget="iframe",
        supports_resume=config.supports_resume,
    )
```

> Decrypt-failure handling note: in this port the decrypt hook is called outside the try/except in `run_claudecode_session` (the resume block has no surrounding `except`), so any exception it raises propagates straight out of the function — satisfying "fail loud, no fresh-start fallback". This is simpler than opencode's keyword-sniffing branch and is the behavior the spec mandates. The decrypt-failure test asserts the raise propagates.

- [ ] **Step 4: Add the capture-through-encrypt + source-wiring tests to `test_session_blob_hooks.py`**

Append to `packages/optio-claudecode/tests/test_session_blob_hooks.py`:

```python
"""Roundtrip a fake session tar through _capture_snapshot with a
non-identity hook to confirm the encrypt wiring, plus a source check that
the resume body invokes the decrypt hook."""

from unittest.mock import AsyncMock, MagicMock


def _reverse(b: bytes) -> bytes:
    return b[::-1]


@pytest.mark.asyncio
async def test_capture_writes_through_session_blob_encrypt(monkeypatch):
    from optio_claudecode.session import _capture_snapshot
    from optio_claudecode import session as session_mod

    fake_session_tar = b"hello-home-claude-tar"
    monkeypatch.setattr(
        session_mod, "_archive_home_claude",
        AsyncMock(return_value=fake_session_tar),
    )
    monkeypatch.setattr(session_mod, "insert_snapshot", AsyncMock(return_value={}))
    monkeypatch.setattr(session_mod, "prune_snapshots", AsyncMock(return_value=[]))

    captured: dict[str, bytes] = {}

    class _FakeWriter:
        def __init__(self, slot: str):
            self.slot = slot
            self._buf = bytearray()
            self.file_id = "f-" + slot
            self._position = 0
        async def write(self, b: bytes):
            self._buf.extend(b)
            self._position = len(self._buf)
            captured[self.slot] = bytes(self._buf)

    class _FakeBlobCtx:
        def __init__(self, slot: str): self._slot = slot
        async def __aenter__(self):
            self._w = _FakeWriter(self._slot)
            return self._w
        async def __aexit__(self, *exc): return False

    fake_ctx = MagicMock()
    fake_ctx.store_blob = lambda slot: _FakeBlobCtx(slot)
    fake_ctx._db = None
    fake_ctx._prefix = "test"
    fake_ctx.process_id = "pid-x"
    fake_ctx.delete_blob = AsyncMock()
    fake_ctx.mark_has_saved_state = AsyncMock()

    async def _fake_archive(_excl):
        yield b"workdir-bytes"
    fake_host = MagicMock()
    fake_host.workdir = "/tmp/wd"
    fake_host.archive_workdir = _fake_archive
    fake_host.run_command = AsyncMock()

    await _capture_snapshot(
        fake_ctx, fake_host,
        end_state="done",
        workdir_exclude=None,
        session_blob_encrypt=_reverse,
    )

    assert captured["session"] == _reverse(fake_session_tar)


def test_resume_body_invokes_decrypt_hook_in_source():
    import inspect
    from optio_claudecode import session as session_mod
    src = inspect.getsource(session_mod.run_claudecode_session)
    assert "decrypt = config.session_blob_decrypt or (lambda b: b)" in src
    assert "decrypt(payload)" in src
```

- [ ] **Step 5: Run all the resume tests to verify they pass**

Run: `.venv/bin/pytest packages/optio-claudecode/tests/test_session_resume.py packages/optio-claudecode/tests/test_session_resume_decrypt_failure.py packages/optio-claudecode/tests/test_on_resume_refresh.py packages/optio-claudecode/tests/test_session_blob_hooks.py -v`
Expected: PASS. Also re-run the existing session test to confirm no regression: `.venv/bin/pytest packages/optio-claudecode/tests/test_session_local.py -v` → PASS.

> If `test_session_local.py::test_local_happy_path...` now fails because AGENTS.md gained the resume section (it asserts substrings, not exact text — `"Hello from the test."` and `"STATUS:"` are still present), no change is needed. If any assertion checked for the *absence* of resume text, update it; none currently do.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/session.py \
        packages/optio-claudecode/tests/test_session_resume.py \
        packages/optio-claudecode/tests/test_session_resume_decrypt_failure.py \
        packages/optio-claudecode/tests/test_on_resume_refresh.py \
        packages/optio-claudecode/tests/test_session_blob_hooks.py
git commit -m "feat(optio-claudecode): resume restore + snapshot capture wiring"
```

---

## Task 6: Update v1 type test for the new config defaults

The v1 spec flagged that the type test must be updated when `supports_resume` lands. `test_types.py` currently asserts only the v1 defaults; extend it to cover the new fields so the surface is locked.

**Files:**
- Modify: `packages/optio-claudecode/tests/test_types.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-claudecode/tests/test_types.py`:

```python
def test_minimal_config_resume_defaults():
    cfg = ClaudeCodeTaskConfig(consumer_instructions="hi")
    assert cfg.supports_resume is True
    assert cfg.workdir_exclude is None
    assert cfg.session_blob_encrypt is None
    assert cfg.session_blob_decrypt is None
    assert cfg.on_resume_refresh is None
```

- [ ] **Step 2: Run test to verify it passes immediately**

Run: `.venv/bin/pytest packages/optio-claudecode/tests/test_types.py -v`
Expected: PASS — Task 2 already added these fields, so this test documents/locks the surface. (This task is a guard, not a RED→GREEN cycle; it exists because the source spec explicitly requires the v1 type-test update. If it fails, Task 2 was incomplete.)

- [ ] **Step 3: Commit**

```bash
git add packages/optio-claudecode/tests/test_types.py
git commit -m "test(optio-claudecode): lock resume config defaults in type test"
```

---

## Task 7: Demo task exercises the resume path

**Files:**
- Modify: `packages/optio-demo/src/optio_demo/tasks/claudecode.py`

- [ ] **Step 1: Write the failing test**

Append to (or create if absent) `packages/optio-demo/tests/test_claudecode_task.py`. First check whether a demo test module exists:

Run: `ls packages/optio-demo/tests/ 2>/dev/null | grep -i claudecode`

If it exists, append; otherwise create `packages/optio-demo/tests/test_claudecode_task.py`:

```python
"""The claudecode demo task opts into resume explicitly."""

from optio_demo.tasks.claudecode import get_tasks


def test_demo_task_supports_resume():
    tasks = get_tasks()
    assert len(tasks) == 1
    assert tasks[0].supports_resume is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest packages/optio-demo/tests/test_claudecode_task.py -v`
Expected: FAIL — `supports_resume` is `True` only if Task 5 changed `create_claudecode_task` AND the demo config doesn't pass `supports_resume=False`. Since the demo currently passes no `supports_resume`, the new default `True` already makes this pass. To make this a real RED, the demo must first be made explicit. **If this test already passes** (because the default is `True`), skip to Step 3 and treat this as locking intent.

- [ ] **Step 3: Write the implementation**

Edit the `ClaudeCodeTaskConfig(...)` call inside `get_tasks()` in `packages/optio-demo/src/optio_demo/tasks/claudecode.py` to make resume explicit:

```python
            config=ClaudeCodeTaskConfig(
                consumer_instructions=CONSUMER_PROMPT,
                env=_resolve_env(),
                # Demo runs autonomously; skip per-tool prompts so the
                # agent isn't blocked waiting for user clicks inside
                # the iframe.
                permission_mode="bypassPermissions",
                ssh=_resolve_ssh_config(),
                before_execute=_before_execute,
                after_execute=_after_execute,
                on_deliverable=_on_deliverable,
                # Resume support. Crypto hooks left None → plaintext
                # session blob (same shape as the opencode demo). Operators
                # forking the demo for a real deployment supply both hooks
                # pointing at actual crypto. No on_resume_refresh: AGENTS.md
                # is reused verbatim on resume. workdir_exclude left default.
                supports_resume=True,
            ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest packages/optio-demo/tests/test_claudecode_task.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-demo/src/optio_demo/tasks/claudecode.py \
        packages/optio-demo/tests/test_claudecode_task.py
git commit -m "feat(optio-demo): claudecode demo opts into resume (plaintext blob)"
```

---

## Task 8: Update package docs

**Files:**
- Modify: `packages/optio-claudecode/AGENTS.md:37`

- [ ] **Step 1: Update the doc line**

Edit `packages/optio-claudecode/AGENTS.md` line 37. Replace the `supports_resume=False` statement with a description of the resume surface. Find:

```
`TaskInstance` returned has `ui_widget="iframe"` and `supports_resume=False`
```

Replace with:

```
`TaskInstance` returned has `ui_widget="iframe"` and `supports_resume` tracking
the config field (defaults to `True`). Resume snapshots the
`<workdir>/home/.claude/` subtree (encryptable session blob) plus a plaintext
workdir blob; on resume the workdir is restored and `--continue` is appended to
claude's argv. See `docs/2026-05-29-optio-claudecode-resume-design.md`.
```

- [ ] **Step 2: Verify no other doc references the old behavior**

Run: `grep -rn "supports_resume=False\|no resume\|resume support" packages/optio-claudecode/`
Expected: no remaining claim that claudecode lacks resume.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-claudecode/AGENTS.md
git commit -m "docs(optio-claudecode): describe resume support surface"
```

---

## Task 9: Full package test sweep

**Files:** none (verification only)

- [ ] **Step 1: Run the whole claudecode suite**

Run: `.venv/bin/pytest packages/optio-claudecode/ -v`
Expected: all pass. MongoDB-via-Docker must be reachable at `$MONGO_URL`.

- [ ] **Step 2: Run the demo suite**

Run: `.venv/bin/pytest packages/optio-demo/ -v`
Expected: all pass.

- [ ] **Step 3: Confirm no opencode regression (shared host primitives unchanged)**

Run: `.venv/bin/pytest packages/optio-opencode/ -v` (or at minimum `test_session_resume.py`, `test_snapshots.py`).
Expected: all pass — this work touches no opencode or optio-host source.

> Per project memory `optio_api_ws_flake`: if running the full monorepo with `pnpm -r` / preflight, the fastify-widget-proxy WS tests can flake under load; set `OPTIO_SKIP_PREFLIGHT_TESTS=1` for isolated Python runs. Not relevant to these Python-only suites.

- [ ] **Step 4: No commit** — this task only verifies.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|--------------|------|
| Mongo schema (no `sessionId`), index, retention/prune | Task 1 |
| Config surface (`supports_resume`, `workdir_exclude`, crypto pair, `on_resume_refresh`) + validation | Task 2 |
| Prompt additions (resume section + `home/.claude/` bullet, `_render_resume_section`) | Task 3 |
| `--continue` on resume | Task 4 |
| Capture flow (`_archive_home_claude`, defensive `rm -rf`, plaintext workdir tar, encrypt hook, prune, `mark_has_saved_state`) | Task 5 |
| Resume flow (plaintext-first restore, decrypt + `_extract_home_claude`, `_rotate_optio_log`, `--continue`, fresh-fallback when no snapshot) | Task 5 |
| Decrypt-failure = fail loud, no fresh-start | Task 5 (test + propagation) |
| `on_resume_refresh` (fires only on resume, rewrites AGENTS.md, `REFRESHED:` tag) | Task 5 |
| `create_claudecode_task` propagates `supports_resume` | Task 5 |
| Test scenarios `long_then_signaled`, `idempotent_done` | Task 5a |
| Demo task update | Task 7 |
| v1 type-test follow-up | Task 6 |
| Package docs | Task 8 |

**Placeholder scan:** No TBD/TODO. Every code step shows full code.

**Type/name consistency:** `_capture_snapshot(ctx, host, *, end_state, workdir_exclude, session_blob_encrypt)` — same signature in Task 5 source and the Task 5 Step 4 test. `insert_snapshot(... session_blob_id=, workdir_blob_id=, deliverables_emitted=)` — no `session_id`, consistent across snapshots.py, session.py, and tests. `build_claude_flags(..., resuming=False)` — consistent in host_actions.py, session.py call site, and Task 4 tests. `compose_agents_md(consumer, *, workdir_exclude=None, supports_resume=True)` — consistent in prompt.py and all call sites.

**Open risk to watch during execution:** the e2e resume tests assume `ctx.load_blob` / `ctx.store_blob` / `ctx.mark_has_saved_state` / `ctx._db` / `ctx._prefix` exist on `ProcessContext` (they do — confirmed against the opencode tests that use them). If `ProcessContext`'s constructor signature differs from what `_make_ctx` passes, copy the exact kwargs from `optio-opencode/tests/test_session_resume.py::_make_ctx`, which is the maintained reference.

---

## Execution Handoff

**Plan complete and saved to `docs/2026-05-29-optio-claudecode-resume-plan.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
