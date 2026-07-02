# optio-codex Plan B — Stage 1 (Remote/SSH) + Stage 2 (Resume/Snapshots) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `optio-codex` (a) run identically over SSH — RemoteHost path verified end-to-end against a docker-sshd harness — and (b) resume a terminated iframe session: restore the workdir (which carries codex's rollout store under `home/.codex/sessions`) and relaunch with `codex resume <recorded-session-id>`.

**Architecture:** Port `optio-grok`'s Stage-1/2 machinery (the newest full wrapper; branch `csillag/optio-grok`, sources at the main checkout `/home/csillag/deai/optio/packages/optio-grok`): docker-sshd remote test harness, Mongo snapshot store (single workdir GridFS blob, retention 5), restore branch in `_prepare`, `resume.log` + prompt resume section. One codex-specific delta over grok: the snapshot doc records a **`sessionId`** (grok's cwd-keyed `--continue` has no codex analog — `codex resume --last` is cwd-filtered and silently starts a NEW session on a miss, so resume is ALWAYS by explicit id), captured at snapshot time by scanning the newest rollout filename under `home/.codex/sessions`.

**Tech Stack:** Python ≥3.11, pytest + pytest-asyncio (`asyncio_mode=auto`), optio-core/host/agents driver stack, tmux + ttyd, MongoDB (GridFS blobs) on `localhost:27017`, Docker (`linuxserver/openssh-server` sshd container) for the remote leg.

## Global Constraints

- Worktree: `/home/csillag/deai/optio/.worktrees/csillag/optio-codex` — branch `csillag/optio-codex`. All relative paths below are from this worktree root.
- **Baseline = Plan A's end state** (`docs/2026-07-02-optio-codex-plan-a-stage0-hardening.md`). Plan A is executing in this same worktree; do NOT start Plan B until Plan A's final verification task has passed (Task 1 below checks). Plan A deltas this plan builds on: per-task codex path `<workdir>/home/.local/bin/codex` (kill-scoping), host-side PATH composition in the launch payload, `launch_ttyd_with_codex` returning a 5-tuple `(handle, tmux_path, port, socket, session)`, orphan-ttyd reap in `teardown_session_tree`, prompt composed via the `optio_agents.prompt` SSOT with a `documentation` kwarg, the Stage-0 `ssh` `NotImplementedError` guard (removed here), fake-codex scenarios `happy|deliverable|error|exit_zero|exit_nonzero|long`, Makefile `PY_PACKAGES` including `optio-codex`.
- Python env: the worktree venv **only**: `.venv/bin/python` / `.venv/bin/pip` (the venv Plan A Task 1 verified). NEVER `pip install` against the global interpreter.
- Test command shape (from worktree root): `.venv/bin/python -m pytest packages/optio-codex/tests/ -q` (needs MongoDB on `localhost:27017`; if down: `cd packages/optio-demo && make deps-up`). The remote test additionally needs Docker; it skip-chains cleanly when Docker is absent — never weaken the skip into a pass.
- Commit style: conventional commits, one commit per task step marked "Commit". **NO `Co-Authored-By` lines** (user rule).
- SSOT / never-duplicate rule: the resume-section template is codex-owned per-wrapper content (established pattern — grok/claudecode/opencode each carry their own), but the **effective exclude list** used by the snapshot archive and by the AGENTS.md resume section must come from ONE function (`snapshots.effective_workdir_exclude`) so the prompt's claims can never drift from what is actually preserved.
- No `isinstance` branches in production code except the one sanctioned bind-interface decision already in `session.py` (`ttyd_iface = bind_addr if isinstance(host, LocalHost) else "127.0.0.1"`). Test code may use `isinstance`.
- Grok reference sources (read for context, adapt with `grok`→`codex` renames; noted per task): `packages/optio-grok/{src/optio_grok,tests}` **at the main checkout** `/home/csillag/deai/optio/packages/optio-grok` (the grok branch is checked out there).
- Every task leaves the whole codex suite green before its commit.

---

### Task 1: Baseline — Plan A complete, suite green

**Files:** none (verification only).

**Interfaces:**
- Consumes: Plan A's finished branch state.
- Produces: a recorded green baseline every later task diffs against.

- [ ] **Step 1: Confirm Plan A finished**

Run: `git -C . log --oneline -20` and `git status --short`
Expected: Plan A's commits are present (look for `fix(optio-codex): reject ssh config with a clear Stage-0 error`, `feat(optio-demo): codex Stage-0 iframe demo task`, `docs(optio-codex): truthful Stage-0 status…`); working tree clean apart from the `docs/*.md` plan files. If Plan A commits are missing, STOP — Plan B's baseline does not exist yet.

- [ ] **Step 2: Verify the venv and run the baseline suite**

Run: `.venv/bin/python -c "import optio_codex, optio_host, optio_agents; print(optio_codex.__file__)"`
Expected: a path inside this worktree's `packages/optio-codex/src/`.
Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass (~24 tests after Plan A), 0 failed. Do not proceed on a red baseline (known repo flakes: re-run once before suspecting a regression).

*(No commit — nothing changed.)*

---

### Task 2: Stage 1 — docker-sshd harness + remote session proof; ssh guard removed

Port grok's remote harness (`packages/optio-grok/tests/{Dockerfile.sshd,docker-compose.sshd.yml,test_session_remote.py}` at the main checkout) with codex renames. The failing test IS the remote integration test: with the harness up, `run_codex_session` currently dies on Plan A's Stage-0 `NotImplementedError` guard. Removing the guard turns it green — the generic `RemoteHost` (selected by `build_host` when `config.ssh` is set) already drives the whole tmux+ttyd+codex flow, and Plan A's host-side PATH composition is exactly what makes the launch payload resolve `python3` (the codex shim's interpreter) against the *container's* PATH. Scenario selection travels via `config.env` — the test-process env never reaches the remote. Port 22223 (grok's choice; opencode holds 22222). The compose project is directory-scoped (`tests`), same as every other package's sshd fixture — suites run sequentially via the Makefile, so no port/name clash.

**Files:**
- Create: `packages/optio-codex/tests/Dockerfile.sshd`
- Create: `packages/optio-codex/tests/docker-compose.sshd.yml`
- Create: `packages/optio-codex/tests/test_session_remote.py`
- Create: `packages/optio-codex/.gitignore` (ignore the generated test keypair, mirroring `packages/optio-opencode/.gitignore`)
- Modify: `packages/optio-codex/src/optio_codex/session.py` (delete the Stage-0 guard, first statement of `run_codex_session`)
- Modify: `packages/optio-codex/tests/test_config.py` (replace the guard test with a RemoteHost-routing test)

**Interfaces:**
- Consumes: `optio_host.host.RemoteHost` (`ssh_config`, `taskdir`; `workdir == f"{taskdir}/workdir"`), `optio_host.types.SSHConfig(host, user, key_path, port=22)` (re-exported by `optio_codex`), `optio_codex.session.run_codex_session`, `_build_host`, conftest's `ctx_and_captures`.
- Produces: no production API change beyond deleting the guard. `run_codex_session` accepts `config.ssh` and routes to `RemoteHost`.

- [ ] **Step 1: Create the harness files**

`packages/optio-codex/tests/Dockerfile.sshd`:

```dockerfile
FROM linuxserver/openssh-server:latest
# optio-codex runs codex inside a detached tmux session fronted by ttyd, so
# the "remote" needs bash (the launch wrappers use `bash -lc`/`bash -c`),
# tmux, and python3 (the fake-codex shim). ttyd is provided by the mounted
# shim.
RUN apk add --no-cache python3 tmux bash
```

`packages/optio-codex/tests/docker-compose.sshd.yml`:

```yaml
services:
  sshd:
    build:
      context: .
      dockerfile: Dockerfile.sshd
    environment:
      - PUID=1000
      - PGID=1000
      - USER_NAME=optiotest
      - PUBLIC_KEY_FILE=/keys/id_ed25519.pub
      - SUDO_ACCESS=false
      - PASSWORD_ACCESS=false
    volumes:
      - ./ssh-keys:/keys:ro
      - ./fake_codex.py:/usr/local/bin/fake_codex.py:ro
      - ./codex-shim.sh:/usr/local/bin/codex:ro
      - ./ttyd-shim.sh:/usr/local/bin/ttyd:ro
    ports:
      - "127.0.0.1:22223:2222"
```

`packages/optio-codex/.gitignore`:

```gitignore
tests/ssh-keys/
```

- [ ] **Step 2: Write the (initially failing) remote test**

Create `packages/optio-codex/tests/test_session_remote.py`:

```python
"""Remote-mode integration test (Stage 1) — spins up an SSH container.

Proves optio-codex runs identically over SSH: the generic ``RemoteHost``
path (selected automatically when ``config.ssh`` is set) drives the same
tmux+ttyd+codex flow as the local test, and a deliverable emitted by the
fake-codex inside the container round-trips back through the optio.log
tail. It is also the live proof of Plan A's host-side PATH fix: the launch
payload's ``export PATH=<home>/.local/bin:"$PATH"`` resolves against the
CONTAINER's PATH (python3 for the codex shim), not the engine's.

Adapted from optio-grok's ``test_session_remote.py`` (same sshd image,
grok → codex renames). The sshd image gains tmux + bash so codex's
detached-tmux launch works on the remote; scenario selection travels via
``config.env`` — the test-process env does not reach the remote.
"""

from __future__ import annotations

import asyncio
import shutil
import socket as _socket
import subprocess
import time
from pathlib import Path

import pytest
import pytest_asyncio

from optio_codex import CodexTaskConfig, SSHConfig
from optio_codex.session import run_codex_session


HERE = Path(__file__).parent
COMPOSE = HERE / "docker-compose.sshd.yml"


def _have_docker() -> bool:
    return shutil.which("docker") is not None


pytestmark = pytest.mark.skipif(
    not _have_docker(), reason="Docker not available"
)


@pytest_asyncio.fixture(scope="module")
async def sshd():
    """Start the SSH container, generate a key pair, wait for port 22223."""
    keys_dir = HERE / "ssh-keys"
    keys_dir.mkdir(exist_ok=True)
    priv = keys_dir / "id_ed25519"
    if not priv.exists():
        subprocess.check_call([
            "ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(priv)
        ])
    # Shims must be executable inside the (read-only) bind mount.
    (HERE / "codex-shim.sh").chmod(0o755)
    (HERE / "ttyd-shim.sh").chmod(0o755)

    try:
        subprocess.check_call(
            ["docker", "compose", "-f", str(COMPOSE), "up", "-d", "--build"]
        )
    except subprocess.CalledProcessError as exc:  # pragma: no cover - env dependent
        pytest.skip(f"docker compose up failed: {exc}")

    # Wait for the SSH port.
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            c = _socket.create_connection(("127.0.0.1", 22223), timeout=1)
            c.close()
            break
        except OSError:
            time.sleep(0.5)
    else:
        subprocess.call(["docker", "compose", "-f", str(COMPOSE), "down"])
        pytest.skip("sshd container did not come up")

    # Extra settle time for sshd to accept auth.
    await asyncio.sleep(2)

    yield {
        "host": "127.0.0.1",
        "port": 22223,
        "user": "optiotest",
        "key_path": str(priv),
    }

    subprocess.call(["docker", "compose", "-f", str(COMPOSE), "down"])


@pytest.mark.asyncio
async def test_remote_deliverable_callback_fired(sshd, ctx_and_captures):
    """Same as the local deliverable test, but over SSH against the container.

    The fake-codex ``deliverable`` scenario writes a file, emits a
    DELIVERABLE line to optio.log, then DONE. Selecting the scenario via
    ``config.env`` (not process env) is what makes it reach the remote
    codex process.
    """
    ctx, *_ = ctx_and_captures

    captured: list[tuple[str, str]] = []

    async def on_deliverable(hook_ctx, path, text):
        captured.append((path, text))

    config = CodexTaskConfig(
        consumer_instructions="hand back a file",
        ssh=SSHConfig(
            host=sshd["host"], user=sshd["user"],
            key_path=sshd["key_path"], port=sshd["port"],
        ),
        codex_install_dir="/usr/local/bin",
        ttyd_install_dir="/usr/local/bin",
        install_if_missing=False,
        install_ttyd_if_missing=False,
        on_deliverable=on_deliverable,
        # Remote codex can't inherit the test process env — the scenario
        # must travel in the launch env.
        env={"FAKE_CODEX_SCENARIO": "deliverable"},
    )

    await run_codex_session(ctx, config)

    assert len(captured) == 1
    path, text = captured[0]
    assert path.endswith("greeting.txt")
    assert text == "hello from fake codex\n"
```

- [ ] **Step 3: Run to verify it fails on the Stage-0 guard**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_session_remote.py -q`
Expected (Docker present): FAIL with `NotImplementedError: optio-codex Stage 0 supports the local host only…` — the guard fires before any host is built.
Expected (no Docker): SKIP `Docker not available` — in that case the removal is still test-guarded by the routing unit test in Step 4 plus the stale guard test turning red once the guard is gone; note the skip in the executor summary.

- [ ] **Step 4: Remove the guard and replace the guard test**

In `packages/optio-codex/src/optio_codex/session.py`, DELETE the first statement of `run_codex_session` (added by Plan A Task 8):

```python
    if config.ssh is not None:
        raise NotImplementedError(
            "optio-codex Stage 0 supports the local host only; remote (SSH) "
            "sessions arrive with the Stage 1 work. Remove the ssh field or "
            "wait for optio-codex >= 0.2."
        )
```

(the function body now starts with `host: Host = _build_host(config, ctx.process_id)`).

In `packages/optio-codex/tests/test_config.py`, DELETE `test_ssh_config_rejected_at_stage0` entirely (it asserts the guard exists — red after this step by design) and ADD:

```python
def test_ssh_config_routes_to_remote_host():
    """Stage 1: ssh config selects the RemoteHost path (the Stage-0
    NotImplementedError guard is gone). Construction only — no connection
    is attempted; the end-to-end proof is test_session_remote.py."""
    from optio_host.host import RemoteHost

    from optio_codex import SSHConfig
    from optio_codex.session import _build_host

    config = CodexTaskConfig(
        consumer_instructions="x",
        ssh=SSHConfig(host="worker.example", user="u", key_path="/k", port=2222),
    )
    host = _build_host(config, "codex-remote-route")
    assert isinstance(host, RemoteHost)
    # Remote taskdir layout: /tmp/<consumer>/<process_id>/workdir (no
    # OPTIO_CODEX_REMOTE_TASK_ROOT override in the test env).
    assert host.workdir == "/tmp/optio-codex/codex-remote-route/workdir"
```

- [ ] **Step 5: Run remote + full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_session_remote.py packages/optio-codex/tests/test_config.py -q`
Expected: remote test PASSES (or SKIPS with the documented reason when Docker is absent); routing test PASSES.
Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-codex/tests/Dockerfile.sshd packages/optio-codex/tests/docker-compose.sshd.yml packages/optio-codex/tests/test_session_remote.py packages/optio-codex/.gitignore packages/optio-codex/src/optio_codex/session.py packages/optio-codex/tests/test_config.py
git commit -m "feat(optio-codex): Stage 1 — remote/SSH sessions live, docker-sshd proof

Removes the Stage-0 NotImplementedError guard: RemoteHost (selected by
build_host when config.ssh is set) drives the identical tmux+ttyd+codex
flow. Docker-sshd harness ported from optio-grok (ro-mounted codex/ttyd
shims, scenario via config.env, port 22223, skip-chain without Docker);
the deliverable round-trip also proves Plan A's host-side PATH
composition holds on a remote worker."
```

---

### Task 3: Stage 2 — `snapshots.py` store (with `sessionId`) + codex default workdir excludes

Port grok's `snapshots.py` (main checkout `packages/optio-grok/src/optio_grok/snapshots.py`) with two codex deltas pinned by the design doc: the snapshot doc gains `sessionId` (resume is by explicit id — grok's cwd-keyed `--continue` has no analog), and the module owns `CODEX_WORKDIR_EXCLUDE_DEFAULT` + `effective_workdir_exclude()` — the single source of truth consumed by the snapshot archive call (Task 7) and the AGENTS.md resume section (Task 6). The defaults MUST exclude `home/.codex/packages` (~286 MB binary cache), `*.sqlite*` (derived rollout index with absolute-path poison; codex rebuilds it), `cache/`, `tmp/` and the other CODEX_HOME junk from the design doc — and MUST NOT exclude `home/.codex/sessions` (the resume source).

**Files:**
- Create: `packages/optio-codex/src/optio_codex/snapshots.py`
- Create: `packages/optio-codex/tests/test_snapshots.py`

**Interfaces:**
- Consumes: `motor.motor_asyncio.AsyncIOMotorDatabase`, `bson.ObjectId`, `optio_host.archive.DEFAULT_WORKDIR_EXCLUDES` (`[".git", "node_modules", "__pycache__", ".venv", "*.pyc", ".DS_Store"]`), archive matching semantics (`optio_host.archive._excluded`: fnmatch against the full workdir-relative path AND against each single path segment; `os.walk` dir-pruning applies patterns to directory relpaths, so a multi-segment pattern like `home/.codex/packages` prunes exactly that subtree).
- Produces:
  - `SESSION_SNAPSHOT_COLLECTION_SUFFIX = "_codex_session_snapshots"`, `SNAPSHOT_RETENTION = 5`
  - `CODEX_WORKDIR_EXCLUDE_DEFAULT: list[str]`
  - `effective_workdir_exclude(workdir_exclude: list[str] | None) -> list[str]`
  - `async ensure_indexes(db, prefix) -> None`
  - `async insert_snapshot(db, prefix, *, process_id: str, end_state: str, workdir_blob_id: ObjectId, session_id: str | None) -> dict`
  - `async load_latest_snapshot(db, prefix, process_id) -> dict | None`
  - `async prune_snapshots(db, prefix, process_id, *, retention=SNAPSHOT_RETENTION) -> list[ObjectId]`
- Consumed by: Task 6 (prompt), Task 7 (session).

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-codex/tests/test_snapshots.py`:

```python
"""Tests for the per-task codex session snapshot collection (Stage 2).

Single-blob layout: codex stores its rollout JSONLs under
``$CODEX_HOME/sessions`` which lives inside the preserved workdir tar, so a
snapshot references only ``workdirBlobId`` — plus the recorded ``sessionId``
(codex resumes ONLY by explicit id; ``resume --last`` is cwd-filtered and
silently starts a new session on a miss).
"""

import asyncio
import io
import tarfile

import pytest
from bson import ObjectId

from optio_codex.snapshots import (
    CODEX_WORKDIR_EXCLUDE_DEFAULT,
    SESSION_SNAPSHOT_COLLECTION_SUFFIX,
    SNAPSHOT_RETENTION,
    effective_workdir_exclude,
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)


pytestmark = pytest.mark.asyncio


async def test_collection_suffix_is_codex_specific():
    assert SESSION_SNAPSHOT_COLLECTION_SUFFIX == "_codex_session_snapshots"


async def test_insert_and_load_latest_returns_newest(mongo_db):
    pid = "proc_a"
    first = ObjectId()
    newest = ObjectId()
    await insert_snapshot(
        mongo_db, "opt", process_id=pid, end_state="done",
        workdir_blob_id=first,
        session_id="11111111-1111-1111-1111-111111111111",
    )
    await asyncio.sleep(0.005)
    await insert_snapshot(
        mongo_db, "opt", process_id=pid, end_state="cancelled",
        workdir_blob_id=newest,
        session_id="22222222-2222-2222-2222-222222222222",
    )

    latest = await load_latest_snapshot(mongo_db, "opt", pid)
    assert latest is not None
    assert latest["endState"] == "cancelled"
    assert latest["workdirBlobId"] == newest
    assert latest["sessionId"] == "22222222-2222-2222-2222-222222222222"
    # Single-blob schema: no separate session blob field.
    assert "sessionBlobId" not in latest


async def test_insert_allows_none_session_id(mongo_db):
    """The sessionId seam stays optional: a capture that found no rollout
    (codex died pre-persist) and — later, Plan D — the conversation-mode
    caller both pass None/their own id through the same parameter."""
    await insert_snapshot(
        mongo_db, "opt", process_id="proc_none", end_state="done",
        workdir_blob_id=ObjectId(), session_id=None,
    )
    latest = await load_latest_snapshot(mongo_db, "opt", "proc_none")
    assert latest is not None
    assert latest["sessionId"] is None


async def test_load_latest_none_when_empty(mongo_db):
    assert await load_latest_snapshot(mongo_db, "opt", "nope") is None


async def test_prune_keeps_five_and_returns_two_stale_ids(mongo_db):
    pid = "proc_b"
    blob_ids: list[ObjectId] = []
    for _ in range(7):
        wid = ObjectId()
        blob_ids.append(wid)
        await insert_snapshot(
            mongo_db, "opt", process_id=pid, end_state="done",
            workdir_blob_id=wid, session_id=None,
        )
        await asyncio.sleep(0.005)

    stale = await prune_snapshots(mongo_db, "opt", pid)
    # The two oldest blob ids are returned for caller-side deletion.
    assert set(stale) == set(blob_ids[:2])

    coll = mongo_db[f"opt{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"]
    assert await coll.count_documents({"processId": pid}) == SNAPSHOT_RETENTION == 5


async def test_prune_noop_within_retention(mongo_db):
    pid = "proc_c"
    for _ in range(SNAPSHOT_RETENTION):
        await insert_snapshot(
            mongo_db, "opt", process_id=pid, end_state="done",
            workdir_blob_id=ObjectId(), session_id=None,
        )
        await asyncio.sleep(0.005)
    assert await prune_snapshots(mongo_db, "opt", pid) == []


async def test_effective_workdir_exclude_resolution():
    assert effective_workdir_exclude(None) == CODEX_WORKDIR_EXCLUDE_DEFAULT
    assert effective_workdir_exclude(["x"]) == ["x"]
    assert effective_workdir_exclude([]) == []


async def test_default_excludes_never_touch_the_session_store():
    """MUST NOT exclude home/.codex/sessions — it is the resume source."""
    assert not any("sessions" in p for p in CODEX_WORKDIR_EXCLUDE_DEFAULT)


async def test_default_excludes_drop_codex_junk_keep_sessions(tmp_path):
    """End-to-end against the real archive builder: the design-doc junk is
    dropped, the rollout store and the working files survive."""
    from optio_host.archive import yield_workdir_archive

    wd = tmp_path / "workdir"
    keep = [
        "home/.codex/sessions/2026/07/02/"
        "rollout-2026-07-02T10-00-00-01234567-89ab-cdef-0123-456789abcdef.jsonl",
        "home/.codex/auth.json",
        "home/.codex/config.toml",
        "deliverables/out.txt",
        "AGENTS.md",
        "resume.log",
    ]
    drop = [
        "home/.codex/packages/blob.bin",
        "home/.codex/state.sqlite3",
        "home/.codex/cache/models.json",
        "home/.codex/tmp/scratch",
        "home/.codex/.tmp/scratch2",
        "home/.codex/shell_snapshots/snap1",
        "home/.codex/version.json",
        "home/.codex/installation_id",
        "home/.codex/log/codex.log",
        "home/.cache/junk",
        ".git/HEAD",
    ]
    for rel in (*keep, *drop):
        p = wd / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")

    chunks = []
    async for chunk in yield_workdir_archive(str(wd), CODEX_WORKDIR_EXCLUDE_DEFAULT):
        chunks.append(chunk)
    with tarfile.open(fileobj=io.BytesIO(b"".join(chunks)), mode="r:gz") as tar:
        names = set(tar.getnames())

    for rel in keep:
        assert rel in names, f"expected {rel} to be preserved"
    for rel in drop:
        assert rel not in names, f"expected {rel} to be excluded"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_snapshots.py -q`
Expected: every test ERRORS on `ModuleNotFoundError: No module named 'optio_codex.snapshots'`.

- [ ] **Step 3: Implement `packages/optio-codex/src/optio_codex/snapshots.py`**

```python
"""MongoDB ``{prefix}_codex_session_snapshots`` collection helpers (Stage 2).

One document per terminal run per process_id. Layout:

    {
      _id:           ObjectId,
      processId:     str,
      capturedAt:    datetime,
      endState:      str,          # "done" | "cancelled" | "rescued"
      workdirBlobId: ObjectId,     # GridFS — tar.gz of the whole workdir
      sessionId:     str | None,   # codex session/rollout UUID at capture
    }

Single-blob, like optio-grok: codex persists per-session rollout JSONLs
under ``$CODEX_HOME/sessions`` (= ``<workdir>/home/.codex/sessions``), which
rides the workdir tar. The layout is path-portable (design-doc probe: a
sessions/ tree copied to a different CODEX_HOME resumes fine; the sqlite
index is derived and rebuilt — hence excluded below). Unlike grok there IS
a recorded ``sessionId``: codex must be resumed by explicit id
(``codex resume <id>``) — ``resume --last`` is cwd-filtered and silently
starts a NEW session on a miss, so optio records the id at snapshot time
and replays it on relaunch. ``sessionId`` may be ``None`` when no rollout
existed at capture time (resume then degrades to a fresh launch in the
restored workdir, loudly logged). Conversation mode (Plan D) passes its
``thread/started`` id through the same field.

Retention: keep the latest ``SNAPSHOT_RETENTION`` per processId. Older rows
are deleted by ``prune_snapshots``, which returns their workdir GridFS blob
ids so the caller can delete the corresponding blobs.
"""

from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES


SESSION_SNAPSHOT_COLLECTION_SUFFIX = "_codex_session_snapshots"
SNAPSHOT_RETENTION = 5

# Default snapshot exclude list (used when ``CodexTaskConfig.workdir_exclude``
# is None): the framework defaults plus the CODEX_HOME junk pinned in the
# design doc. MUST NOT exclude ``home/.codex/sessions`` — the rollout JSONLs
# there are the resume source. Pattern semantics (optio_host.archive):
# fnmatch against the full workdir-relative path AND against every single
# path segment — so ``*.sqlite*`` matches anywhere, while the multi-segment
# ``home/.codex/...`` entries prune exactly one subtree.
CODEX_WORKDIR_EXCLUDE_DEFAULT: list[str] = [
    *DEFAULT_WORKDIR_EXCLUDES,
    "home/.codex/packages",         # ~286 MB binary cache; re-seeded, never snapshotted
    "*.sqlite*",                    # derived rollout index; absolute-path poison
    "home/.codex/cache",
    "home/.codex/tmp",
    "home/.codex/.tmp",
    "home/.codex/shell_snapshots",
    "home/.codex/models_cache.json",
    "home/.codex/version.json",
    "home/.codex/installation_id",
    "home/.codex/log",
    "home/.cache",                  # per-task XDG cache — junk, can be large
]


def effective_workdir_exclude(workdir_exclude: list[str] | None) -> list[str]:
    """The exclude list a snapshot will actually honor.

    ``None`` (the config default) means the codex defaults above — NOT the
    bare framework defaults. Single source of truth for the archive call
    (``session._capture_snapshot``) and the AGENTS.md resume-section
    rendering (``prompt``), so the prompt's preservation claims can never
    drift from what is actually snapshotted.
    """
    if workdir_exclude is None:
        return CODEX_WORKDIR_EXCLUDE_DEFAULT
    return workdir_exclude


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
    prefix: str,
    *,
    process_id: str,
    end_state: str,
    workdir_blob_id: ObjectId,
    session_id: str | None,
) -> dict:
    """Insert one snapshot row and return the stored document (with ``_id``)."""
    await ensure_indexes(db, prefix)
    doc = {
        "processId": process_id,
        "capturedAt": datetime.now(timezone.utc),
        "endState": end_state,
        "workdirBlobId": workdir_blob_id,
        "sessionId": session_id,
    }
    result = await _collection(db, prefix).insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def load_latest_snapshot(
    db: AsyncIOMotorDatabase, prefix: str, process_id: str,
) -> dict | None:
    return await _collection(db, prefix).find_one(
        {"processId": process_id}, sort=[("capturedAt", -1)],
    )


async def prune_snapshots(
    db: AsyncIOMotorDatabase,
    prefix: str,
    process_id: str,
    *,
    retention: int = SNAPSHOT_RETENTION,
) -> list[ObjectId]:
    """Keep the latest ``retention`` snapshots; delete the rest.

    Returns the ``workdirBlobId`` of each deleted snapshot so the caller can
    remove the corresponding GridFS blob.
    """
    coll = _collection(db, prefix)
    all_docs = await coll.find(
        {"processId": process_id},
        projection={"workdirBlobId": 1, "capturedAt": 1},
        sort=[("capturedAt", -1)],
    ).to_list(None)
    stale = all_docs[retention:]
    if not stale:
        return []
    await coll.delete_many({"_id": {"$in": [d["_id"] for d in stale]}})
    return [d["workdirBlobId"] for d in stale]
```

- [ ] **Step 4: Run green + full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_snapshots.py -q`
Expected: all pass.
Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/snapshots.py packages/optio-codex/tests/test_snapshots.py
git commit -m "feat(optio-codex): Mongo session-snapshot store with recorded sessionId (Stage 2)

Single-blob workdir snapshots (grok layout) plus the codex delta: a
sessionId per snapshot, because codex resumes only by explicit id
(resume --last silently mints a new session on a cwd miss). Also owns
the default snapshot exclude list — CODEX_HOME junk out (packages/,
*.sqlite*, cache/, tmp/, …), home/.codex/sessions expressly preserved —
behind effective_workdir_exclude(), the SSOT the archive call and the
AGENTS.md resume section both consume."
```

---

### Task 4: Stage 2 — resume bookkeeping host actions

Four additions to `host_actions.py`, all engine-free (generic Host primitives only):
`_rotate_optio_log` and `_append_resume_log_entry` port verbatim from grok (main checkout `packages/optio-grok/src/optio_grok/host_actions.py:960-1005`); `read_latest_session_id` is codex-specific (newest-rollout filename scan — the design-doc capture mechanism for iframe mode); `build_resume_args` emits the `resume <id>` SUBCOMMAND prefix (before all flags — the real CLI argv shape per the design doc), and `build_auto_start_args` gains grok's `resuming` suppression.

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/host_actions.py`
- Test: `packages/optio-codex/tests/test_host_actions.py`

**Interfaces:**
- Consumes: `Host.run_command`, `Host.fetch_bytes_from_host` (raises `FileNotFoundError` on a missing local file), `Host.write_text`, `Host.workdir`; `optio_codex.host_actions.build_host` (test-side, to get a real `LocalHost` with an existing workdir).
- Produces:
  - `async _rotate_optio_log(host: Host) -> None`
  - `async _append_resume_log_entry(host: Host, *, refreshed: list[str] | None = None) -> None`
  - `async read_latest_session_id(host: Host) -> str | None`
  - `build_resume_args(session_id: str | None) -> list[str]`
  - `build_auto_start_args(*, auto_start: bool, resuming: bool = False, prompt: str = AUTO_START_PROMPT) -> list[str]` (new `resuming` kwarg; default preserves all existing call sites)
- Consumed by: Task 7 (session wiring).

- [ ] **Step 1: Write the failing tests**

Append to `packages/optio-codex/tests/test_host_actions.py` (add `import pathlib` and `import time` to the module imports if not already present):

```python
def test_build_resume_args_subcommand_precedes_flags():
    """`codex resume <id>` is a SUBCOMMAND: it and the explicit session id
    lead the argv, before every flag. None ⇒ fresh launch, no prefix."""
    from optio_codex.host_actions import build_resume_args

    sid = "01234567-89ab-cdef-0123-456789abcdef"
    assert build_resume_args(sid) == ["resume", sid]
    assert build_resume_args(None) == []


def test_build_auto_start_args_suppressed_on_resume():
    from optio_codex.host_actions import AUTO_START_PROMPT, build_auto_start_args

    assert build_auto_start_args(auto_start=True) == [AUTO_START_PROMPT]
    assert build_auto_start_args(auto_start=True, resuming=True) == []
    assert build_auto_start_args(auto_start=False) == []
    assert build_auto_start_args(auto_start=False, resuming=True) == []


@pytest.mark.asyncio
async def test_read_latest_session_id_scans_newest_rollout_by_name(tmp_path):
    """Newest by FILENAME, not mtime: rollout names embed an ISO-ordered
    timestamp, and mtimes do not survive a workdir tar restore. The test
    deliberately gives the OLDER rollout the NEWER mtime."""
    import pathlib as _p
    import time as _t

    from optio_codex.host_actions import build_host, read_latest_session_id

    host = build_host(None, str(tmp_path / "task"))
    old_id = "11111111-1111-1111-1111-111111111111"
    new_id = "22222222-2222-2222-2222-222222222222"
    sessions = _p.Path(host.workdir) / "home" / ".codex" / "sessions"
    old_dir = sessions / "2026" / "07" / "01"
    new_dir = sessions / "2026" / "07" / "02"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    (new_dir / f"rollout-2026-07-02T09-00-00-{new_id}.jsonl").write_text(
        "{}\n", encoding="utf-8",
    )
    _t.sleep(0.01)
    (old_dir / f"rollout-2026-07-01T09-00-00-{old_id}.jsonl").write_text(
        "{}\n", encoding="utf-8",
    )  # older name, newest mtime

    assert await read_latest_session_id(host) == new_id


@pytest.mark.asyncio
async def test_read_latest_session_id_none_when_no_rollouts(tmp_path):
    from optio_codex.host_actions import build_host, read_latest_session_id

    host = build_host(None, str(tmp_path / "task"))
    assert await read_latest_session_id(host) is None


@pytest.mark.asyncio
async def test_rotate_optio_log_moves_content_and_truncates(tmp_path):
    import pathlib as _p

    from optio_codex.host_actions import _rotate_optio_log, build_host

    host = build_host(None, str(tmp_path / "task"))
    wd = _p.Path(host.workdir)
    (wd / "optio.log").write_text("STATUS: old\nDONE\n", encoding="utf-8")

    await _rotate_optio_log(host)
    assert (wd / "optio.log").read_text(encoding="utf-8") == ""
    assert "DONE" in (wd / "optio.log.old").read_text(encoding="utf-8")

    # A second rotation APPENDS to optio.log.old (history preserved across
    # consecutive resumes).
    (wd / "optio.log").write_text("DONE again\n", encoding="utf-8")
    await _rotate_optio_log(host)
    old = (wd / "optio.log.old").read_text(encoding="utf-8")
    assert "DONE\n" in old and "DONE again\n" in old
    assert (wd / "optio.log").read_text(encoding="utf-8") == ""


@pytest.mark.asyncio
async def test_rotate_optio_log_missing_log_writes_empty(tmp_path):
    import pathlib as _p

    from optio_codex.host_actions import _rotate_optio_log, build_host

    host = build_host(None, str(tmp_path / "task"))
    await _rotate_optio_log(host)
    assert (_p.Path(host.workdir) / "optio.log").read_text(encoding="utf-8") == ""


@pytest.mark.asyncio
async def test_append_resume_log_entry_formats(tmp_path):
    import pathlib as _p

    from optio_codex.host_actions import _append_resume_log_entry, build_host

    host = build_host(None, str(tmp_path / "task"))
    await _append_resume_log_entry(host)
    await _append_resume_log_entry(host, refreshed=["AGENTS.md", "notes.md"])

    lines = (
        (_p.Path(host.workdir) / "resume.log")
        .read_text(encoding="utf-8").splitlines()
    )
    assert len(lines) == 2
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", lines[0])
    assert lines[1].endswith(" REFRESHED:AGENTS.md,notes.md")
```

- [ ] **Step 2: Run to verify failures**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_host_actions.py -q`
Expected: `test_build_resume_args_subcommand_precedes_flags`, `test_read_latest_session_id_*`, `test_rotate_*`, `test_append_resume_log_entry_formats` FAIL with `ImportError` (names don't exist); `test_build_auto_start_args_suppressed_on_resume` FAILS with `TypeError: build_auto_start_args() got an unexpected keyword argument 'resuming'`.

- [ ] **Step 3: Implement**

In `packages/optio-codex/src/optio_codex/host_actions.py`:

Add to the imports block at the top of the file:

```python
from datetime import datetime, timezone
```

Replace `build_auto_start_args` (currently right below `AUTO_START_PROMPT`) with:

```python
def build_auto_start_args(
    *, auto_start: bool, resuming: bool = False, prompt: str = AUTO_START_PROMPT,
) -> list[str]:
    """Trailing positional prompt for an auto-start FRESH launch.

    Returns ``[prompt]`` when ``auto_start`` and not ``resuming``; empty
    otherwise. On resume the session continues via ``codex resume <id>``
    and no positional is appended: re-issuing the kickoff prompt would
    enqueue a duplicate task on top of the resumed conversation.
    """
    return [prompt] if (auto_start and not resuming) else []
```

Append at the end of the file:

```python
# --- resume bookkeeping (Stage 2; adapted from optio-grok) ------------------


# codex rollout filenames: ``rollout-<timestamp>-<uuid>.jsonl`` under
# ``$CODEX_HOME/sessions/YYYY/MM/DD/``. The UUID (v7 in real codex; any UUID
# shape accepted here) is the session id ``codex resume`` takes.
_ROLLOUT_UUID_RE = re.compile(
    r"rollout-.*-([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$"
)


async def read_latest_session_id(host: "Host") -> str | None:
    """Session id of the newest rollout under ``<workdir>/home/.codex/sessions``.

    Newest by FILENAME (lexicographic): rollout names embed an ISO-ordered
    timestamp, so a name sort IS a chronological sort — and unlike mtime it
    survives a workdir tar restore. Returns None when no rollout exists yet
    (codex never persisted a session). The derived sqlite index is
    deliberately not consulted: it is excluded from snapshots (absolute
    rollout paths) and codex rebuilds it from the rollout files.
    """
    sessions_dir = f"{host.workdir.rstrip('/')}/home/.codex/sessions"
    r = await host.run_command(
        f"find {shlex.quote(sessions_dir)} -type f -name 'rollout-*.jsonl' "
        f"2>/dev/null | sort | tail -n 1"
    )
    newest = (r.stdout or "").strip()
    if not newest:
        return None
    m = _ROLLOUT_UUID_RE.search(newest)
    if m is None:
        _LOG.warning(
            "read_latest_session_id: unparseable rollout filename %r", newest,
        )
        return None
    return m.group(1)


def build_resume_args(session_id: str | None) -> list[str]:
    """Leading argv for relaunching into a recorded session.

    ``resume`` is a codex SUBCOMMAND: it and the explicit session id must
    PRECEDE every flag — ``codex resume <id> [flags]``. Never
    ``resume --last``: it is cwd-filtered and silently starts a NEW session
    on a miss (design-doc probe), so resume is always by explicit id.
    Returns ``[]`` when ``session_id`` is None (fresh launch).
    """
    return ["resume", session_id] if session_id else []


async def _rotate_optio_log(host: "Host") -> None:
    """Append the restored optio.log to optio.log.old, then truncate it.

    Preserves historical log content across consecutive resumes while
    ensuring the tail driver only sees fresh lines from the resumed run (a
    stale DONE/ERROR carried in the restored log would otherwise be replayed
    and end the session immediately).
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
    host: "Host", *, refreshed: list[str] | None = None,
) -> None:
    """Append one line to ``<workdir>/resume.log``.

    Line format: ``<ISO 8601 UTC timestamp>[ REFRESHED:<comma-separated names>]``.
    The first line is the original launch; each later line marks a resume.
    The caller gates this on ``config.supports_resume``.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} REFRESHED:{','.join(refreshed)}" if refreshed else ts
    target = f"{host.workdir.rstrip('/')}/resume.log"
    result = await host.run_command(
        f"echo {shlex.quote(line)} >> {shlex.quote(target)}"
    )
    if result.exit_code != 0:
        raise RuntimeError(
            f"failed to append to resume.log: exit {result.exit_code}: "
            f"{result.stderr!r}"
        )
```

- [ ] **Step 4: Run green + full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_host_actions.py -q`
Expected: all pass.
Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass (no existing caller passes `resuming`, so behavior is unchanged elsewhere).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/host_actions.py packages/optio-codex/tests/test_host_actions.py
git commit -m "feat(optio-codex): resume bookkeeping host actions (Stage 2)

_rotate_optio_log + _append_resume_log_entry ported from optio-grok;
read_latest_session_id scans the newest rollout FILENAME under
home/.codex/sessions (name order = chronological order and survives tar
restores, unlike mtime); build_resume_args emits the leading
'resume <id>' subcommand (explicit-id only — resume --last silently
mints a new session); auto-start positional suppressed when resuming."
```

---

### Task 5: Stage 2 — config: `supports_resume` default ON + `workdir_exclude`; TaskInstance wiring

Flip resumability on by default (grok parity) and add the snapshot exclude knob. `create_codex_task` stops hardcoding `supports_resume=False` and forwards the config value so the dashboard surfaces the Resume affordance.

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/types.py`
- Modify: `packages/optio-codex/src/optio_codex/session.py` (`create_codex_task` only)
- Test: `packages/optio-codex/tests/test_config.py`, `packages/optio-codex/tests/test_session_local.py`

**Interfaces:**
- Produces: `CodexTaskConfig.supports_resume: bool = True`; `CodexTaskConfig.workdir_exclude: list[str] | None = None` (None ⇒ `snapshots.CODEX_WORKDIR_EXCLUDE_DEFAULT`); `create_codex_task(...)` returns a `TaskInstance` with `supports_resume=config.supports_resume`.
- Consumed by: Tasks 6, 7.

- [ ] **Step 1: Write the failing tests**

In `packages/optio-codex/tests/test_config.py`, extend `test_defaults_and_validation` — after the existing `assert c.ask_for_approval == "never" and c.sandbox == "workspace-write"` line, add:

```python
    assert c.supports_resume is True
    assert c.workdir_exclude is None
```

Append to the same file:

```python
def test_supports_resume_flows_to_task_instance():
    from optio_codex import create_codex_task

    on = create_codex_task(
        process_id="p-resume-on", name="n",
        config=CodexTaskConfig(consumer_instructions="x"),
    )
    off = create_codex_task(
        process_id="p-resume-off", name="n",
        config=CodexTaskConfig(consumer_instructions="x", supports_resume=False),
    )
    assert on.supports_resume is True
    assert off.supports_resume is False
```

In `packages/optio-codex/tests/test_session_local.py`, in `test_local_happy_path_done_in_optio_log`, change:

```python
    assert task.supports_resume is False
```

to:

```python
    assert task.supports_resume is True  # Stage 2: resumable by default
```

- [ ] **Step 2: Run to verify failures**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_config.py packages/optio-codex/tests/test_session_local.py -q`
Expected: the extended defaults assertion FAILS (`AttributeError: 'CodexTaskConfig' object has no attribute 'supports_resume'`); `test_supports_resume_flows_to_task_instance` FAILS; the local happy-path FAILS on the flipped assertion.

- [ ] **Step 3: Implement**

In `packages/optio-codex/src/optio_codex/types.py`, inside `CodexTaskConfig`, add directly below the `mode`/`host_protocol` fields:

```python
    # Resume/snapshots (Stage 2). When True (default) the session captures a
    # workdir snapshot — plus the codex sessionId — at teardown, and a later
    # run with ctx.resume=True restores it and relaunches via
    # `codex resume <id>`.
    supports_resume: bool = True
    # Snapshot exclude list. None (default) resolves to
    # optio_codex.snapshots.CODEX_WORKDIR_EXCLUDE_DEFAULT (framework defaults
    # + CODEX_HOME junk: packages/, *.sqlite*, cache/, tmp/, …). MUST NOT be
    # set to exclude home/.codex/sessions — that is the resume source.
    workdir_exclude: list[str] | None = None
```

Also update the class docstring's second paragraph from
`Stage 0 covers iframe/ttyd mode on the local host. Remote SSH, resume, seeds, conversation mode, and filesystem isolation arrive in later stages.` to:

```python
    Stages 0-2 cover iframe/ttyd mode on local and SSH-remote hosts with
    resume/snapshots. Seeds, conversation mode, and filesystem isolation
    arrive in later stages.
```

In `packages/optio-codex/src/optio_codex/session.py`, in `create_codex_task`'s `TaskInstance(...)` call, change:

```python
        supports_resume=False,
```

to:

```python
        supports_resume=config.supports_resume,
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass. (No snapshot machinery is wired yet — `supports_resume=True` on the TaskInstance is inert until Task 7; `mark_has_saved_state` is never called before then.)

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/types.py packages/optio-codex/src/optio_codex/session.py packages/optio-codex/tests/test_config.py packages/optio-codex/tests/test_session_local.py
git commit -m "feat(optio-codex): resumable-by-default config — supports_resume + workdir_exclude (Stage 2)

TaskInstance now carries config.supports_resume (was hardcoded False),
surfacing the dashboard Resume affordance; workdir_exclude defaults to
the codex snapshot exclude list via snapshots.effective_workdir_exclude."
```

---

### Task 6: Stage 2 — AGENTS.md resume section (truth-synced) + `resume.log` written each session start

Port grok's `RESUME_SECTION_TEMPLATE` (main checkout `packages/optio-grok/src/optio_grok/prompt.py:44-135`) with `.grok`→`.codex` renames, rendered from the **effective** exclude list (`snapshots.effective_workdir_exclude` — Task 3's SSOT) so the prompt never claims preservation the snapshot doesn't deliver. The session's compose call threads `workdir_exclude`/`supports_resume` through, and `_prepare` starts appending the `resume.log` line the section documents — prompt claim and harness behavior ship in the same commit.

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/prompt.py` (full rewrite below)
- Modify: `packages/optio-codex/src/optio_codex/session.py` (`_prepare`'s compose call + resume.log line)
- Test: `packages/optio-codex/tests/test_prompt.py`, `packages/optio-codex/tests/test_session_local.py`

**Interfaces:**
- Consumes: `optio_agents.prompt.compose_agents_md(consumer_instructions, *, documentation, resume_section=None) -> str`; `optio_agents.protocol.{ProtocolFeatures, build_log_channel_prompt}`; `optio_codex.snapshots.effective_workdir_exclude`; `optio_codex.host_actions._append_resume_log_entry` (Task 4).
- Produces: `compose_agents_md(consumer_instructions, *, documentation: str | None = None, host_protocol: bool = True, workdir_exclude: list[str] | None = None, supports_resume: bool = True) -> str` (existing kwargs preserved; two new ones default-compatible).

- [ ] **Step 1: Write the failing tests**

Append to `packages/optio-codex/tests/test_prompt.py`:

```python
def test_resume_section_present_and_synced_to_default_excludes():
    md = compose_agents_md("X")
    assert "## Resumes" in md
    assert "resume.log" in md
    # Truth-sync: the rendered exclude list is the EFFECTIVE codex default
    # (snapshots.effective_workdir_exclude), not the framework default…
    assert "`home/.codex/packages`" in md
    assert "`*.sqlite*`" in md
    # …and the session store is called out as preserved.
    assert "home/.codex/sessions" in md


def test_resume_section_renders_custom_and_empty_excludes():
    md = compose_agents_md("X", workdir_exclude=["bigdata"])
    assert "`bigdata`" in md
    assert "`home/.codex/packages`" not in md

    md_empty = compose_agents_md("X", workdir_exclude=[])
    assert "No paths are excluded" in md_empty


def test_supports_resume_false_omits_resume_section():
    md = compose_agents_md("X", supports_resume=False)
    assert "## Resumes" not in md
    assert "resume.log" not in md


def test_host_protocol_false_keeps_resume_section_and_explainer():
    md = compose_agents_md("X", host_protocol=False)
    assert "## Resumes" in md
    assert "System:" in md
    assert "STATUS:" not in md
```

And in `packages/optio-codex/tests/test_session_local.py`, extend `test_local_happy_path_done_in_optio_log`: add to `after_execute`:

```python
        observed["resume_log"] = (workdir / "resume.log").read_text(encoding="utf-8")
```

and to the final assertion block:

```python
    assert observed["resume_log"].count("\n") == 1  # exactly one launch line
    assert "## Resumes" in observed["agents_md"]
```

- [ ] **Step 2: Run to verify failures**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_prompt.py packages/optio-codex/tests/test_session_local.py -q`
Expected: the four new prompt tests FAIL (`TypeError: compose_agents_md() got an unexpected keyword argument 'workdir_exclude'` / missing section); the extended local test FAILS on `FileNotFoundError` for `resume.log`.

- [ ] **Step 3: Rewrite `packages/optio-codex/src/optio_codex/prompt.py`**

Full new content (builds on Plan A Task 6's SSOT version; the resume template is the sanctioned per-wrapper copy, same as grok/claudecode/opencode carrying their own):

```python
"""AGENTS.md composition for optio-codex.

Codex reads an ``AGENTS.md`` file in its workdir. The shared framing and
the keyword-protocol documentation are owned by ``optio-agents`` (the
prompt SSOT); this module threads codex's protocol mode through and owns
the codex-specific resume-awareness section (Stage 2), rendered from the
EFFECTIVE snapshot exclude list so its preservation claims never drift
from what the snapshot actually keeps.
"""

from optio_agents.prompt import compose_agents_md as _compose_agents_md_host
from optio_agents.protocol import ProtocolFeatures, build_log_channel_prompt

from optio_codex.snapshots import effective_workdir_exclude


# Self-contained System: explainer for sessions without the keyword-protocol
# docs (which normally explain the convention). Per-wrapper copy is the
# established pattern (claudecode/opencode/grok each carry their own).
_SYSTEM_PREFIX_EXPLAINER = """\
(Messages prefixed `System:` on your input channel originate from the
harness coordinating this session, not from the human user.)
"""


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

- **Your `home/.codex/` directory — the codex session store (rollout
  files under `home/.codex/sessions`, auth, config) — IS preserved
  across resumes** (minus the excluded paths above), so your history
  travels with you even when the underlying process and host change.

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

You may also be notified of a resume by a `System:` message on your input
channel; when you see one, follow the `resume.log` procedure above.
"""


def _render_resume_section(workdir_exclude: list[str] | None) -> str:
    """Render RESUME_SECTION_TEMPLATE with the EFFECTIVE exclude list.

    ``effective_workdir_exclude`` is the same resolver the snapshot archive
    uses (``None`` → the codex defaults), so what this section claims is
    preserved is exactly what the snapshot preserves.
    """
    effective = effective_workdir_exclude(workdir_exclude)
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
    documentation: str | None = None,
    host_protocol: bool = True,
    workdir_exclude: list[str] | None = None,
    supports_resume: bool = True,
) -> str:
    """Build the full AGENTS.md body.

    ``documentation`` is the keyword-protocol block; the session passes
    ``get_protocol(browser="suppress").documentation``. Defaults (for unit
    tests / standalone callers) to codex's ``suppress`` docs. It must
    always come from the session's ``Protocol`` where one exists — never
    rebuild features at a second site.

    ``host_protocol=False`` omits the keyword-protocol documentation and
    instead includes a self-contained ``System:`` message explainer
    (guide Part 2D); iframe mode always runs with ``host_protocol=True``
    (validated in ``CodexTaskConfig``), the False branch serves
    conversation mode in a later stage.

    ``supports_resume=True`` (default) appends the resume-awareness section
    so the agent watches ``resume.log`` and knows ``home/.codex`` (minus
    ``workdir_exclude``) survives across resumes. ``workdir_exclude`` is
    this task's snapshot exclude list (None → the codex defaults), used to
    keep the section's claims in sync with what is actually preserved.
    """
    if host_protocol:
        if documentation is None:
            documentation = build_log_channel_prompt(
                ProtocolFeatures(browser="suppress")
            )
    else:
        documentation = None
    resume_section: str | None = (
        _render_resume_section(workdir_exclude) if supports_resume else None
    )
    if not host_protocol:
        # The protocol docs normally explain the `System:` convention;
        # without them the composed prompt carries its own explainer.
        resume_section = (
            resume_section + _SYSTEM_PREFIX_EXPLAINER
            if resume_section
            else _SYSTEM_PREFIX_EXPLAINER
        )
    return _compose_agents_md_host(
        consumer_instructions,
        documentation=documentation,
        resume_section=resume_section,
    )
```

In `packages/optio-codex/src/optio_codex/session.py`, inside `_prepare`, replace the `await host.write_text("AGENTS.md", …)` call with:

```python
        await host.write_text(
            "AGENTS.md",
            compose_agents_md(
                config.consumer_instructions,
                documentation=protocol.documentation if config.host_protocol else None,
                host_protocol=config.host_protocol,
                workdir_exclude=config.workdir_exclude,
                supports_resume=config.supports_resume,
            ),
        )
        if config.supports_resume:
            await host_actions._append_resume_log_entry(host)
```

(the `if config.before_execute is not None:` block stays below it, unchanged).

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass. Plan A's prompt tests keep passing by construction: `test_documentation_threads_from_session_protocol` compares two calls that now both carry the default resume section (still equal); `test_host_protocol_false_adds_system_explainer` still sees `System:` and no `STATUS:`.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/prompt.py packages/optio-codex/src/optio_codex/session.py packages/optio-codex/tests/test_prompt.py packages/optio-codex/tests/test_session_local.py
git commit -m "feat(optio-codex): resume-awareness AGENTS.md section + resume.log per session start (Stage 2)

Grok's RESUME_SECTION ported (.grok → .codex), rendered from the
EFFECTIVE snapshot exclude list (snapshots.effective_workdir_exclude —
the same resolver the archive uses) so the prompt's preservation claims
cannot drift; _prepare now appends the documented resume.log line on
every session start, gated on supports_resume."
```

---

### Task 7: Stage 2 — session resume wiring: restore → `codex resume <id>` → snapshot capture

The core Stage-2 cycle, mirroring grok's `session.py` invariants with one deliberate divergence: **restore runs BEFORE `ensure_codex_installed`** (grok ensures first). Grok's launch binary lives outside the workdir, so restore order didn't matter there; codex's launch path is the per-task symlink INSIDE the workdir (`<workdir>/home/.local/bin/codex`), and `restore_workdir` empties the workdir before extracting — provisioning after the restore re-creates the home tree and re-points the symlink (`mkdir -p`/`ln -sfn` are idempotent). Ported invariants: restore-failure fails LOUD (call deliberately outside any `except`); `_rotate_optio_log` right after restore (before the driver subscribes the optio.log tail, so a stale DONE/ERROR is never replayed); AGENTS.md planted AFTER restore; auto-start positional suppressed on resume; reached-live gate on capture (`launched_handle is not None`); capture failure logged, never fatal.

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/session.py`
- Modify: `packages/optio-codex/tests/fake_codex.py` (new `resume` scenario + rollout emulation)
- Create: `packages/optio-codex/tests/test_session_resume.py`

**Interfaces:**
- Consumes: Task 3 (`insert_snapshot`, `load_latest_snapshot`, `prune_snapshots`, `effective_workdir_exclude`), Task 4 (`build_resume_args`, `build_auto_start_args(resuming=…)`, `read_latest_session_id`, `_rotate_optio_log`), Task 5/6 (config + prompt); `ProcessContext.resume: bool`, `ctx._db`, `ctx._prefix`, `ctx.store_blob(name)` (async CM whose writer exposes `.file_id`), `ctx.load_blob(file_id)`, `ctx.delete_blob(file_id)`, `ctx.mark_has_saved_state()`; `Host.archive_workdir(exclude) -> AsyncIterator[bytes]` (plain method), `Host.restore_workdir(stream)`; `optio_core.store.upsert_process` (test-side, so the process doc carries `supportsResume` and `mark_has_saved_state` is honored).
- Produces: `_capture_snapshot(ctx, host, *, end_state: str, workdir_exclude: list[str] | None, session_id: str | None) -> None` and `_stream_blob(ctx, blob_id) -> AsyncIterator[bytes]` (module-level, session.py); the `sessionId` seam for Plan D is exactly `_capture_snapshot`'s `session_id` parameter — the conversation body will pass its `thread/started` id instead of the rollout scan.

- [ ] **Step 1: Extend the fake agent**

In `packages/optio-codex/tests/fake_codex.py`:

Add to the module imports (top of file):

```python
import datetime
import json
import sys
import uuid
```

Change `SCENARIOS` to:

```python
SCENARIOS = (
    "happy", "deliverable", "error",
    "exit_zero", "exit_nonzero", "long",
    "resume",
)
```

Add after `_scenario_long` (the last Plan A scenario):

```python
def _codex_home() -> Path:
    """The per-task CODEX_HOME (``<workdir>/home/.codex``) set by the launcher.

    Lives INSIDE the workdir, so anything written here is captured by the
    workdir snapshot and restored on resume — exactly like real codex's
    rollout store (``$CODEX_HOME/sessions``).
    """
    ch = os.environ.get("CODEX_HOME") or str(Path.cwd() / "home" / ".codex")
    return Path(ch)


def _rollouts(ch: Path) -> "list[Path]":
    sessions = ch / "sessions"
    if not sessions.is_dir():
        return []
    return sorted(sessions.rglob("rollout-*.jsonl"))


def _write_rollout(ch: Path) -> Path:
    """Create a plausible codex rollout JSONL for a NEW session.

    Real codex: ``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl``
    (UUIDv7; any UUID satisfies the wrapper's filename scan)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    day_dir = (
        ch / "sessions" / now.strftime("%Y") / now.strftime("%m")
        / now.strftime("%d")
    )
    day_dir.mkdir(parents=True, exist_ok=True)
    session_id = str(uuid.uuid4())
    ts = now.strftime("%Y-%m-%dT%H-%M-%S")
    path = day_dir / f"rollout-{ts}-{session_id}.jsonl"
    path.write_text(
        json.dumps({
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": str(Path.cwd())},
        }) + "\n",
        encoding="utf-8",
    )
    return path


def _scenario_resume() -> None:
    """Model codex's session-id-keyed rollout persistence for the resume test.

    Every launch appends its argv to ``$CODEX_HOME/fake_codex_argv.jsonl``
    (append-only; after a workdir restore the first run's line survives, so
    the file carries one line per launch — proving the restore worked and
    revealing whether the resumed launch led with ``resume <id>``).

    Fresh launch (argv does not start with ``resume``): writes a NEW
    rollout. Resumed launch: appends a turn to the newest EXISTING rollout —
    real ``codex resume <id>`` continues the same session, same id. Also
    plants exclusion-proof junk (packages/ blob, sqlite index) that the
    snapshot MUST drop.
    """
    ch = _codex_home()
    ch.mkdir(parents=True, exist_ok=True)
    with (ch / "fake_codex_argv.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(sys.argv[1:]) + "\n")
        fh.flush()
    if sys.argv[1:2] == ["resume"]:
        existing = _rollouts(ch)
        if existing:
            with existing[-1].open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps({"type": "turn_context", "resumed": True}) + "\n"
                )
        else:
            _write_rollout(ch)
    else:
        _write_rollout(ch)
    # Junk the default workdir_exclude must drop (asserted by the test).
    (ch / "packages").mkdir(exist_ok=True)
    (ch / "packages" / "blob.bin").write_bytes(b"\x00" * 1024)
    (ch / "state.sqlite3").write_bytes(b"sqlite-junk")
    time.sleep(0.05)
    _log("STATUS: 10% resume-scenario alive")
    time.sleep(0.05)
    _log("DONE: resume scenario completed")
    time.sleep(30.0)
```

In `main()`, add the dispatch entry so the dict reads:

```python
    {
        "happy": _scenario_happy,
        "deliverable": _scenario_deliverable,
        "error": _scenario_error,
        "exit_zero": _scenario_exit_zero,
        "exit_nonzero": _scenario_exit_nonzero,
        "long": _scenario_long,
        "resume": _scenario_resume,
    }[scenario]()
```

(If `main()` still prints its unknown-scenario error via `__import__("sys").stderr`, simplify it to `file=sys.stderr` now that `sys` is a top-level import.)

- [ ] **Step 2: Write the failing integration tests**

Create `packages/optio-codex/tests/test_session_resume.py`:

```python
"""Full-cycle resume test for optio-codex against fake_codex.py (Stage 2).

Codex persists per-session rollout JSONLs under ``$CODEX_HOME/sessions``
(inside the workdir) and resumes ONLY by explicit id — ``codex resume <id>``
(``resume --last`` is cwd-filtered and silently starts a new session; never
used). So resume = restore the workdir tar (carrying ``home/.codex``) +
relaunch with the sessionId recorded in the snapshot. This test proves the
whole cycle in one shot: the fake-codex argv log carries TWO launches (the
first run's line survived the restore), only the second launch leads with
``resume <recorded-id>``, the auto-start positional is suppressed on the
resumed launch, and the snapshot honors the default exclude list (sessions
kept, packages/sqlite junk dropped).
"""

from __future__ import annotations

import asyncio
import io
import json
import pathlib
import re
import tarfile

from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_codex import CodexTaskConfig
from optio_codex.host_actions import AUTO_START_PROMPT
from optio_codex.session import run_codex_session
from optio_codex.snapshots import (
    SESSION_SNAPSHOT_COLLECTION_SUFFIX,
    load_latest_snapshot,
)


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


async def _make_ctx(mongo_db, process_id: str, *, resume: bool) -> ProcessContext:
    """A ProcessContext whose backing process doc carries supportsResume=True
    (via upsert_process) so mark_has_saved_state is honored, not ignored."""
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


def _cfg(shim_install_dir: pathlib.Path) -> CodexTaskConfig:
    return CodexTaskConfig(
        consumer_instructions="do the thing",
        codex_install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        supports_resume=True,
    )


async def _run(mongo_db, pid, shim, *, resume, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "resume")
    ctx = await _make_ctx(mongo_db, pid, resume=resume)
    await run_codex_session(ctx, _cfg(shim))


async def _open_workdir_tar(mongo_db, snap) -> tarfile.TarFile:
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    stream = await bucket.open_download_stream(snap["workdirBlobId"])
    blob = await stream.read()
    return tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz")


async def test_terminal_flow_captures_snapshot_with_session_id(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    pid = "codex_terminal_1"
    await _run(mongo_db, pid, shim_install_dir, resume=False, monkeypatch=monkeypatch)

    snap = await load_latest_snapshot(mongo_db, "test", pid)
    assert snap is not None
    assert "workdirBlobId" in snap and "sessionBlobId" not in snap
    # The sessionId was scanned from the fake's rollout filename.
    assert snap["sessionId"] is not None and _UUID_RE.match(snap["sessionId"])

    proc = await mongo_db["test_processes"].find_one({"processId": pid})
    assert proc["hasSavedState"] is True


async def test_resume_restores_workdir_and_relaunches_by_session_id(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    pid = "codex_resume_1"
    # Fresh run: captures snapshot 1 (1-line argv log, new rollout).
    await _run(mongo_db, pid, shim_install_dir, resume=False, monkeypatch=monkeypatch)
    snap1 = await load_latest_snapshot(mongo_db, "test", pid)
    session_id = snap1["sessionId"]
    assert session_id is not None and _UUID_RE.match(session_id)

    # Resume run: restores the workdir (incl. home/.codex) and relaunches
    # via `codex resume <session_id>`.
    await _run(mongo_db, pid, shim_install_dir, resume=True, monkeypatch=monkeypatch)

    count = await mongo_db[
        f"test{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"
    ].count_documents({"processId": pid})
    assert count == 2

    snap2 = await load_latest_snapshot(mongo_db, "test", pid)
    # The resumed launch continued the SAME session — same recorded id.
    assert snap2["sessionId"] == session_id

    with await _open_workdir_tar(mongo_db, snap2) as tar:
        names = set(tar.getnames())
        argv_member = next(
            m for m in tar.getmembers()
            if m.name.endswith("home/.codex/fake_codex_argv.jsonl")
        )
        argv_lines = (
            tar.extractfile(argv_member).read().decode("utf-8").splitlines()
        )
        resume_member = next(
            m for m in tar.getmembers() if m.name == "resume.log"
        )
        resume_lines = (
            tar.extractfile(resume_member).read().decode("utf-8").splitlines()
        )

    # Exclude-list truth: the session store IS in the tar, the junk is NOT.
    assert any(n.startswith("home/.codex/sessions/") for n in names)
    assert not any(n.startswith("home/.codex/packages") for n in names)
    assert not any(".sqlite" in n for n in names)

    launches = [json.loads(line) for line in argv_lines if line]
    # Two lines ⟹ the first run's line survived the restore (restore worked).
    assert len(launches) == 2, launches
    # Fresh launch: no resume subcommand; auto-start positional LAST.
    assert launches[0][:1] != ["resume"]
    assert launches[0][-1] == AUTO_START_PROMPT
    # Resumed launch: `resume <recorded id>` BEFORE the flags; positional
    # suppressed (re-kicking would enqueue a duplicate task).
    assert launches[1][:2] == ["resume", session_id]
    assert AUTO_START_PROMPT not in launches[1]
    # resume.log: one line per session start (fresh + resume).
    assert len(resume_lines) == 2


async def test_resume_with_no_prior_snapshot_falls_back_to_fresh(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    """resume=True with no snapshot on record must still run (fresh) and, on
    a normal terminal exit, capture its own snapshot — not raise."""
    pid = "codex_resume_no_prior"
    await _run(mongo_db, pid, shim_install_dir, resume=True, monkeypatch=monkeypatch)
    snap = await load_latest_snapshot(mongo_db, "test", pid)
    assert snap is not None
```

- [ ] **Step 3: Run to verify failures**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_session_resume.py -q`
Expected: all three FAIL — `load_latest_snapshot` returns `None` (no capture is wired), so `test_terminal_flow…` fails its `snap is not None` assertion and the others follow.

- [ ] **Step 4: Implement the session wiring**

In `packages/optio-codex/src/optio_codex/session.py`:

**(a) Imports** — add after the existing `from optio_codex.prompt import compose_agents_md` import:

```python
from optio_codex.snapshots import (
    effective_workdir_exclude,
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)
```

and extend the `typing` needs by adding at the top (with the other stdlib imports):

```python
from typing import AsyncIterator
```

**(b) Variable prologue** — in `run_codex_session`, after `cancelled = False`, add:

```python
    # Whether a snapshot was restored this run (suppresses the auto-start
    # positional). Set by _prepare, read by the body.
    resuming = False
    # The session/rollout id recorded in the restored snapshot; drives the
    # `codex resume <id>` relaunch. None ⇒ fresh codex session even when the
    # workdir was restored (the snapshot predates any rollout).
    resume_session_id: str | None = None
```

**(c) `_prepare`** — replace the whole function with:

```python
    async def _prepare(host: Host, hook_ctx: HookContext) -> None:
        """Restore a resume snapshot, provision codex + ttyd, plant AGENTS.md.

        Handed to run_log_protocol_session, which runs it AFTER
        host.setup_workdir() has wiped the workdir and BEFORE it subscribes
        the optio.log tail. That ordering is why the restore belongs here:
        the restored optio.log is rotated away below before the tail can
        replay its stale DONE/ERROR, and AGENTS.md is planted AFTER the
        restore so the restore cannot wipe it.

        Restore runs BEFORE ensure_codex_installed — a deliberate divergence
        from the grok template (which ensures first): codex's launch path is
        the per-task symlink INSIDE the workdir
        (<workdir>/home/.local/bin/codex), and restore_workdir empties the
        workdir before extracting. Provisioning after the restore re-creates
        the home tree and re-points the symlink (mkdir -p / ln -sfn are
        idempotent), so the launch path can never dangle.
        """
        nonlocal codex_path, ttyd_path, resuming, resume_session_id

        resume_requested = bool(getattr(ctx, "resume", False))
        snapshot = None
        if resume_requested:
            snapshot = await load_latest_snapshot(
                ctx._db, ctx._prefix, ctx.process_id,
            )
        resuming = snapshot is not None
        if resuming:
            # Restore the workdir tar (carries home/.codex — sessions/,
            # auth, config). A present snapshot that fails to restore is
            # fatal — the call is intentionally outside any except so it
            # surfaces to the caller (no silent fresh-start).
            await host.restore_workdir(_stream_blob(ctx, snapshot["workdirBlobId"]))
            await host_actions._rotate_optio_log(host)
            resume_session_id = snapshot.get("sessionId")
            if resume_session_id is None:
                _LOG.warning(
                    "resume: snapshot for %s carries no sessionId (codex never "
                    "persisted a rollout in that run); the workdir is restored "
                    "but codex starts a FRESH session — explicit-id resume "
                    "only, never `resume --last` (it silently mints a new "
                    "session on a miss).",
                    ctx.process_id,
                )

        codex_path = await host_actions.ensure_codex_installed(
            hook_ctx,
            install_if_missing=config.install_if_missing,
            install_dir=config.codex_install_dir,
        )
        ttyd_path = await host_actions.ensure_ttyd_installed(
            hook_ctx,
            install_if_missing=config.install_ttyd_if_missing,
            install_dir=config.ttyd_install_dir,
        )
        await host.write_text(
            "AGENTS.md",
            compose_agents_md(
                config.consumer_instructions,
                documentation=protocol.documentation if config.host_protocol else None,
                host_protocol=config.host_protocol,
                workdir_exclude=config.workdir_exclude,
                supports_resume=config.supports_resume,
            ),
        )
        if config.supports_resume:
            await host_actions._append_resume_log_entry(host)
        if config.before_execute is not None:
            # End-of-prepare placement matches claudecode (its
            # _plant_session_content ends with before_execute, inside its
            # _prepare); opencode fires it inside the body instead.
            await config.before_execute(hook_ctx)
```

**(d) `_codex_body` flag assembly** — replace the current two-statement assembly (`codex_flags = host_actions.build_codex_flags(…)` followed by `codex_flags = [*codex_flags, *host_actions.build_auto_start_args(auto_start=config.auto_start)]`) with:

```python
        codex_flags = [
            # `codex resume <id>` is a SUBCOMMAND — it must precede the flags.
            *host_actions.build_resume_args(resume_session_id),
            *host_actions.build_codex_flags(
                model=config.model,
                ask_for_approval=config.ask_for_approval,
                sandbox=config.sandbox,
            ),
            # Positional kickoff prompt: fresh launches only (suppressed when
            # a snapshot was restored — re-kicking would duplicate the task).
            *host_actions.build_auto_start_args(
                auto_start=config.auto_start, resuming=resuming,
            ),
        ]
```

**(e) Snapshot capture in the `finally` block** — insert between the `teardown_session_tree` block and the `await host.cleanup_taskdir(...)` block:

```python
        # Reached-live gate: only capture if codex actually came up
        # (launched_handle is assigned strictly after a successful ttyd/codex
        # launch). An interrupt before launch leaves it None — skip capture
        # so any prior good snapshot survives and hasSavedState is untouched.
        if config.supports_resume and launched_handle is not None:
            try:
                await _capture_snapshot(
                    ctx, host,
                    end_state="cancelled" if cancelled else "done",
                    workdir_exclude=config.workdir_exclude,
                    # Iframe mode: scan the newest rollout filename. Plan D's
                    # conversation body passes its thread/started id through
                    # this same parameter instead.
                    session_id=await host_actions.read_latest_session_id(host),
                )
            except Exception:
                _LOG.exception(
                    "snapshot capture failed; proceeding with workdir wipe",
                )
```

**(f) Module-level helpers** — add above `create_codex_task`:

```python
async def _stream_blob(ctx: ProcessContext, blob_id) -> "AsyncIterator[bytes]":
    async with ctx.load_blob(blob_id) as reader:
        while True:
            chunk = await reader.read(1 << 20)
            if not chunk:
                break
            yield chunk


async def _capture_snapshot(
    ctx: ProcessContext,
    host: Host,
    *,
    end_state: str,
    workdir_exclude: list[str] | None,
    session_id: str | None,
) -> None:
    """Capture a single-blob resume snapshot of the (now static) workdir.

    Codex's rollout store lives under ``home/.codex/sessions`` INSIDE the
    workdir, so one workdir tar carries everything ``codex resume <id>``
    needs; ``session_id`` records WHICH session to resume. Streams the tar
    into GridFS honoring the effective exclude list, records the snapshot
    row, prunes to the retention limit (deleting stale blobs), and surfaces
    the Resume affordance.
    """
    exclude = effective_workdir_exclude(workdir_exclude)
    async with ctx.store_blob("workdir") as wwriter:
        async for chunk in host.archive_workdir(exclude):
            await wwriter.write(chunk)
        workdir_blob_id = wwriter.file_id

    await insert_snapshot(
        ctx._db, ctx._prefix,
        process_id=ctx.process_id,
        end_state=end_state,
        workdir_blob_id=workdir_blob_id,
        session_id=session_id,
    )

    stale = await prune_snapshots(ctx._db, ctx._prefix, ctx.process_id)
    for blob_id in stale:
        try:
            await ctx.delete_blob(blob_id)
        except Exception:
            _LOG.exception("delete_blob(workdir) failed")

    await ctx.mark_has_saved_state()
```

- [ ] **Step 5: Run the resume tests, then the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_session_resume.py -q`
Expected: 3 passed.
Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass. Note the local Stage-0 tests now also exercise capture at teardown (`supports_resume` defaults True); their `ctx_and_captures` process doc has no `supportsResume`, so `mark_has_saved_state` logs its documented warning and is otherwise a no-op — green by design. If `test_cancellation_returns_clean_and_tears_down` got slower, that is the (cancelled-end-state) snapshot archive running — it must still pass.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-codex/src/optio_codex/session.py packages/optio-codex/tests/fake_codex.py packages/optio-codex/tests/test_session_resume.py
git commit -m "feat(optio-codex): resume — restore workdir, relaunch codex resume <id>, snapshot capture (Stage 2)

Grok's resume invariants ported (loud restore failure, optio.log
rotation before the tail subscribes, AGENTS.md planted after restore,
auto-start suppressed on resume, reached-live capture gate) with the
codex deltas: restore runs BEFORE per-task provisioning (the launch
symlink lives inside the workdir), and relaunch is by recorded
sessionId — 'resume <id>' subcommand ahead of the flags, never
resume --last. Conversation mode (Plan D) reuses _capture_snapshot's
session_id parameter for its thread id."
```

---

### Task 8: README truth-up + final verification sweep

**Files:**
- Modify: `packages/optio-codex/README.md`

- [ ] **Step 1: Update the Status section**

In the `## Status — Stage 0 (hardened MVP)` section written by Plan A Task 12: retitle it and move the shipped items. Change the heading to:

```markdown
## Status — Stages 0–2 (iframe, remote SSH, resume)
```

Append to the `Shipped:` list:

```markdown
- remote SSH workers (`ssh=SSHConfig(...)` routes to `RemoteHost`; verified
  end-to-end against a docker-sshd harness)
- resume / workdir snapshots: session-id-keyed relaunch (`codex resume <id>`,
  never `resume --last`), Mongo snapshot store (retention 5, single workdir
  GridFS blob carrying `home/.codex/sessions`), `resume.log` + AGENTS.md
  resume section synced to the snapshot exclude list
  (`workdir_exclude`; defaults drop `home/.codex/packages`, `*.sqlite*`,
  caches — never `home/.codex/sessions`)
```

In the `Still missing …` list, delete these two lines:

```markdown
- remote SSH host (`ssh` config is rejected until then)
- resume / workdir snapshots; crash-orphan rescue
```

and add instead:

```markdown
- crash-orphan rescue (snapshot capture for a crashed engine)
```

- [ ] **Step 2: Full suite, fresh run**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass, 0 failed/errored; the only allowed skip is `test_session_remote.py` when Docker is absent.

- [ ] **Step 3: Remote leg explicitly (when Docker is available)**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_session_remote.py -v`
Expected: `test_remote_deliverable_callback_fired PASSED`. Confirm the container is gone afterwards: `docker ps --format '{{.Names}}' | grep -i sshd || echo none` → `none` (module teardown ran `docker compose down`).

- [ ] **Step 4: Cross-package sanity**

Run: `.venv/bin/python -m pytest packages/optio-agents/tests/ packages/optio-host/tests/ -q`
Expected: green (codex only consumed their public APIs; known repo flakes: re-run once before suspecting a regression).

- [ ] **Step 5: Branch state + commit**

Run: `git log --oneline main..HEAD | head -30` and `git status --short`
Expected: Plan A's commits + one commit per Task 2–7 above; clean tree apart from plan docs.

```bash
git add packages/optio-codex/README.md
git commit -m "docs(optio-codex): README status — Stages 1-2 (remote SSH + resume) shipped"
```

---

## Self-Review (performed while writing)

1. **Coverage vs scope.**
   - *Stage 1*: ssh `NotImplementedError` guard removed → Task 2 Step 4; RemoteHost path verified end-to-end → Task 2's docker test; harness ported from grok (Dockerfile.sshd with tmux+bash+python3, compose with ro-mounted codex/ttyd shims + `fake_codex.py`, scenario via `config.env`, port 22223, skip-chain: `pytestmark` no-docker skip → compose-up skip → port-wait skip) → Task 2 Steps 1–2; no new `isinstance` in production code (the only one remains the pre-existing sanctioned bind decision; Task 2's test-side `isinstance` is allowed per Global Constraints); PATH host-side composition verified remote → the deliverable round-trip depends on the container-side `python3` resolution, called out in the test docstring.
   - *Stage 2*: `snapshots.py` with `{prefix}_codex_session_snapshots`, retention 5, doc + `sessionId` → Task 3; `_capture_snapshot` with reached-live gate → Task 7(e,f); resume path in `_prepare` with LOUD restore failure, `_rotate_optio_log`, AGENTS.md after restore, auto-start suppressed → Task 7(c,d); session-id capture via the exact shell one-liner `find <sessions> -type f -name 'rollout-*.jsonl' 2>/dev/null | sort | tail -n 1` (name order = chronological; restore-safe) → Task 4; conversation seam = `_capture_snapshot(session_id=…)` parameter, `insert_snapshot(session_id: str | None)` → Tasks 3/7; relaunch `codex resume <id>` as a subcommand BEFORE flags → Task 4 `build_resume_args` + Task 7(d) argv order, asserted by `launches[1][:2] == ["resume", session_id]`; `supports_resume` config + TaskInstance wiring → Task 5; `resume.log` prompt section (grok RESUME_SECTION, `.grok`→`.codex`) → Task 6; `workdir_exclude` with codex defaults (MUST-exclude `home/.codex/packages`, `*.sqlite*`, cache/tmp dirs; MUST-NOT-exclude `home/.codex/sessions`) → Task 3, asserted both unit-level (archive-builder test) and integration-level (tar contents in Task 7's test); fake_codex resume scenario with plausible rollout emulation under `$CODEX_HOME/sessions` → Task 7 Step 1; `test_snapshots` + `test_session_resume` ported → Tasks 3/7.
2. **Placeholder scan.** Every code step carries complete, paste-ready code; no "TBD", no "similar to Task N" references — the two full `_prepare` rewrites (Tasks 6 and 7) intentionally repeat shared lines rather than referencing each other. The only conditional instruction is Task 7 Step 1's parenthetical about `main()`'s `__import__("sys")` cleanup, which names the exact expression to replace.
3. **Type consistency across tasks.** `insert_snapshot(db, prefix, *, process_id, end_state, workdir_blob_id, session_id)` — defined Task 3, called with all six in Task 7(f) and in Task 3's tests. `effective_workdir_exclude(list|None) -> list` — defined Task 3, consumed by Task 6's `_render_resume_section` and Task 7's `_capture_snapshot`. `build_resume_args(str|None) -> list[str]` and `build_auto_start_args(*, auto_start, resuming=False, prompt=…)` — defined Task 4, called in Task 7(d) with keyword args matching. `read_latest_session_id(host) -> str | None` feeds `_capture_snapshot(session_id=…)` (`str | None`) which feeds `insert_snapshot(session_id=…)` (`str | None`) — consistent optional-string chain. `compose_agents_md(…, documentation=None, host_protocol=True, workdir_exclude=None, supports_resume=True)` — Task 6's signature matches both call sites (Task 6/7 `_prepare` and the prompt unit tests). `CodexTaskConfig.supports_resume/workdir_exclude` (Task 5) match every consumer (`create_codex_task`, `_prepare`, `_capture_snapshot` call). The `sshd` fixture dict keys (`host/port/user/key_path`) match the `SSHConfig(host=, user=, key_path=, port=)` constructor (`optio_host.types.SSHConfig`).
4. **Baseline-drift guard.** Tasks assume Plan A's end state (5-tuple launch return, per-task codex path, prompt `documentation` kwarg, fake scenarios `exit_zero|exit_nonzero|long`, `test_ssh_config_rejected_at_stage0` present). Task 1 verifies Plan A completed; if any Plan A detail landed differently, adjust the *edited surrounding lines* to what is actually in the file while keeping this plan's NEW code verbatim — the new interfaces above are the contract.
