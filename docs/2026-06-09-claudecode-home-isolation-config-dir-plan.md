# Claude Code home-isolation config-dir fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the per-task claude session from reading the host user's global Claude config by setting `CLAUDE_CONFIG_DIR=<workdir>/home/.claude`, and follow the one file that relocates (`.claude.json`) through the seed/oauth machinery with consume-time backward-compat (no seed migration).

**Architecture:** Add `CLAUDE_CONFIG_DIR` to the claude launch env. Because that flattens `.claude.json` into `<home>/.claude/`, update the three sites that reference its old `<home>/.claude.json` location — the seed manifest, the consume-time rekey (which also normalizes an old seed's root `.claude.json` into `.claude/`), and the oauth seed-diff noise filter. Seeds are caller-encrypted so they can't be migrated offline; the rekey normalizes at consume time instead.

**Tech Stack:** Python, asyncio, pytest (mongodb-memory-server for Mongo, LocalHost + a real `claude` binary for integration). Spec: `docs/2026-06-09-claudecode-home-isolation-config-dir-design.md`.

---

> **EXECUTION SHAPE — parallel.** Per the standing project directive this plan is parallel-shaped. **Every file is owned by exactly one task; no two tasks edit the same file.** Tasks 1–3 are file-disjoint and may run concurrently. Each task writes its own tests in its own *new* test file (existing test files are not touched, avoiding contention). **ALL verification (pytest) is deferred to Task V** — do NOT gate individual tasks on green tests; the tree need not pass mid-execution. Commit per task without running tests.

## File Structure

| File | Owner | Responsibility |
|---|---|---|
| `src/optio_claudecode/host_actions.py` | T1 | add `CLAUDE_CONFIG_DIR` to the launch env |
| `tests/test_config_dir_isolation.py` (new) | T1 | unit (env contains var) + real-claude no-leak regression |
| `src/optio_claudecode/seed_manifest.py` | T2 | manifest includes both `.claude.json` paths; rekey normalizes old→new |
| `tests/test_seed_claude_json_relocation.py` (new) | T2 | rekey back-compat (old root layout → `.claude/`) + new layout in place |
| `src/optio_claudecode/oauth.py` | T3 | seed-diff noise filter accepts both `.claude.json` paths |
| `tests/test_oauth_filter_claude_json.py` (new) | T3 | filter ignores `.claude/.claude.json` |

All paths are under `packages/optio-claudecode/`.

---

## Task 1: `CLAUDE_CONFIG_DIR` in the launch env

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py` (`_build_claude_shell_command`, the `env_assignments` list ~line 421)
- Test: `packages/optio-claudecode/tests/test_config_dir_isolation.py` (create)

- [ ] **Step 1: Add the env assignment.** In `_build_claude_shell_command`, change the `env_assignments` list (currently `HOME` + `PATH`) to also set `CLAUDE_CONFIG_DIR`:

```python
    env_assignments: list[str] = [
        f"HOME={home_dir}",
        f"PATH={home_local_bin}:{base_path}",
        # Claude Code resolves its config dir from CLAUDE_CONFIG_DIR (else
        # ~/.claude via the OS passwd home, which IGNORES $HOME). Without this
        # the host operator's global ~/.claude/CLAUDE.md, settings, etc. leak
        # into the sandboxed task. Point it at the planted per-task dir.
        f"CLAUDE_CONFIG_DIR={home_dir}/.claude",
    ]
```

(`home_dir` is `f"{workdir_clean}/home"`, already defined just above.)

- [ ] **Step 2: Write the tests.** Create `tests/test_config_dir_isolation.py`:

```python
"""Claude must read config only from the planted per-task dir, never the host
user's global ~/.claude (the leak this closes)."""
import os
import shutil
import subprocess

import pytest

from optio_claudecode import host_actions


def test_launch_env_sets_claude_config_dir_to_planted_dir():
    env, _shell = host_actions._build_claude_shell_command(
        claude_path="/x/home/.local/bin/claude",
        workdir="/wd",
        extra_env=None,
        claude_flags=[],
        local_mode=True,
    )
    assert "CLAUDE_CONFIG_DIR=/wd/home/.claude" in env


@pytest.mark.skipif(shutil.which("claude") is None, reason="no real claude binary on PATH")
def test_real_claude_resolves_config_under_isolated_dir_not_host_home(tmp_path):
    # Run the real claude under the isolation env. Config-path resolution is
    # written to the debug file at startup, BEFORE any API call, so this needs
    # no auth (the process exits non-zero on "Not logged in" — fine).
    claude = shutil.which("claude")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    dbg = tmp_path / "dbg.txt"
    env = {
        **os.environ,
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
    }
    subprocess.run(
        [claude, "--debug-file", str(dbg), "--print", "x"],
        env=env, capture_output=True, timeout=60,
    )
    log = dbg.read_text(errors="replace") if dbg.exists() else ""
    isolated = str(home / ".claude")
    host_global = os.path.join(os.path.expanduser("~"), ".claude")

    # Claude resolved its config dir under the isolated dir...
    assert isolated in log, f"isolated config dir not referenced in debug log:\n{log[:2000]}"
    # ...and NEVER touched the host user's real global config dir.
    assert f"{host_global}/" not in log, (
        f"host global config dir {host_global} leaked into resolution:\n{log[:2000]}"
    )
```

- [ ] **Step 3: Commit** (no test run — deferred to Task V):

```bash
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py \
        packages/optio-claudecode/tests/test_config_dir_isolation.py
git commit -m "fix(optio-claudecode): set CLAUDE_CONFIG_DIR to the planted dir (close host config leak)"
```

---

## Task 2: seed manifest + rekey follow the relocated `.claude.json`

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/seed_manifest.py` (`_rekey_claude_json_projects` ~line 27; `CLAUDE_SEED_MANIFEST.include` ~line 82)
- Test: `packages/optio-claudecode/tests/test_seed_claude_json_relocation.py` (create)

Background: with `CLAUDE_CONFIG_DIR=<home>/.claude`, claude reads `.claude.json` at `<home>/.claude/.claude.json` (not the old `<home>/.claude.json`). The manifest must capture the new path AND still extract an old seed's root member; the rekey must normalize an old-layout file into `.claude/` before collapsing project trust.

- [ ] **Step 1: Update the rekey to normalize old→new, then collapse.** Replace `_rekey_claude_json_projects` (seed_manifest.py:27–72) with:

```python
async def _rekey_claude_json_projects(host: Host) -> None:
    """Collapse `.claude.json` `projects` to a SINGLE trusted entry keyed at the
    launch workdir, so an autonomous task is never blocked by claude's
    folder-trust prompt ("Is this a project you trust?", which
    `--permission-mode bypassPermissions` does NOT suppress -> the session exits
    in tmux).

    With CLAUDE_CONFIG_DIR=<home>/.claude, claude uses `<home>/.claude/.claude.json`.
    A seed captured before that change has the file at the old `<home>/.claude.json`
    (home root); normalize it into `.claude/` first so old seeds keep working
    (seeds are caller-encrypted and cannot be migrated offline — this is the
    consume-time equivalent).

    Reuse an existing entry's value (preserving trust flags / allowedTools / MCP
    enablement) else synthesize one, force `hasTrustDialogAccepted: true`, and drop
    every other (stale, foreign) workdir entry. Missing / malformed .claude.json
    -> left as-is.
    """
    workdir = host.workdir.rstrip("/")
    new_path = f"{workdir}/home/.claude/.claude.json"
    old_path = f"{workdir}/home/.claude.json"

    moved_from_old = False
    try:
        raw = await host.fetch_bytes_from_host(new_path)
    except FileNotFoundError:
        try:
            raw = await host.fetch_bytes_from_host(old_path)
        except FileNotFoundError:
            return
        moved_from_old = True  # old-layout seed: relocate into .claude/

    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        _LOG.warning("seed: .claude.json is not valid JSON; leaving projects as-is")
        return
    if not isinstance(data, dict):
        return
    projects = data.get("projects")
    value: dict = {}
    if isinstance(projects, dict) and projects:
        chosen = next(
            (v for v in projects.values()
             if isinstance(v, dict) and v.get("hasTrustDialogAccepted")),
            next(iter(projects.values())),
        )
        if isinstance(chosen, dict):
            value = dict(chosen)
    value["hasTrustDialogAccepted"] = True
    data["projects"] = {workdir: value}
    await host.put_file_to_host(json.dumps(data).encode("utf-8"), new_path)
    if moved_from_old:
        await host.remove_file(old_path)
```

- [ ] **Step 2: Add the new path to the manifest include.** In `CLAUDE_SEED_MANIFEST` (seed_manifest.py:82–91), add `.claude/.claude.json` to the include list (keep `.claude.json` for back-compat extraction of old seeds):

```python
CLAUDE_SEED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=CLAUDE_CRED_MANIFEST.include + [
        ".claude/settings.json",
        ".claude/mcp-needs-auth-cache.json",
        ".claude/.claude.json",  # new (CLAUDE_CONFIG_DIR layout)
        ".claude.json",          # old layout — kept so pre-existing seeds still extract
    ],
    version=CLAUDE_SEED_MANIFEST_VERSION,
    consume_transform=_rekey_claude_json_projects,
)
```

- [ ] **Step 3: Write the tests.** Create `tests/test_seed_claude_json_relocation.py`:

```python
"""Rekey handles both the new CLAUDE_CONFIG_DIR layout (.claude/.claude.json)
and a pre-existing seed's old root layout (.claude.json), normalizing the latter."""
import json
import os

import pytest

from optio_host.host import LocalHost
from optio_claudecode.seed_manifest import _rekey_claude_json_projects


def _host(tmp_path):
    taskdir = str(tmp_path / "task")
    os.makedirs(taskdir, exist_ok=True)
    host = LocalHost(taskdir=taskdir)
    os.makedirs(f"{host.workdir}/home/.claude", exist_ok=True)
    return host


async def _read(host, rel):
    path = f"{host.workdir}/{rel}"
    if not os.path.exists(path):
        return None
    return json.loads(open(path).read())


async def test_new_layout_rekeyed_in_place(tmp_path):
    host = _host(tmp_path)
    new_path = f"{host.workdir}/home/.claude/.claude.json"
    with open(new_path, "w") as f:
        json.dump({"projects": {"/old/cwd": {"hasTrustDialogAccepted": True, "k": 1}}}, f)

    await _rekey_claude_json_projects(host)

    data = await _read(host, "home/.claude/.claude.json")
    assert list(data["projects"].keys()) == [host.workdir]
    assert data["projects"][host.workdir]["hasTrustDialogAccepted"] is True
    assert data["projects"][host.workdir]["k"] == 1  # preserved existing value


async def test_old_root_layout_normalized_into_dot_claude(tmp_path):
    host = _host(tmp_path)
    old_path = f"{host.workdir}/home/.claude.json"
    with open(old_path, "w") as f:
        json.dump({"projects": {"/old/cwd": {"hasTrustDialogAccepted": True}}}, f)

    await _rekey_claude_json_projects(host)

    # moved into .claude/ and rekeyed to the launch workdir...
    new = await _read(host, "home/.claude/.claude.json")
    assert new is not None
    assert list(new["projects"].keys()) == [host.workdir]
    assert new["projects"][host.workdir]["hasTrustDialogAccepted"] is True
    # ...and the orphan root copy is gone.
    assert not os.path.exists(old_path)


async def test_missing_claude_json_is_noop(tmp_path):
    host = _host(tmp_path)
    await _rekey_claude_json_projects(host)  # must not raise
    assert not os.path.exists(f"{host.workdir}/home/.claude/.claude.json")
```

- [ ] **Step 4: Commit:**

```bash
git add packages/optio-claudecode/src/optio_claudecode/seed_manifest.py \
        packages/optio-claudecode/tests/test_seed_claude_json_relocation.py
git commit -m "fix(optio-claudecode): seed manifest+rekey follow relocated .claude.json (consume-time back-compat)"
```

---

## Task 3: oauth seed-diff noise filter accepts both paths

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/oauth.py:242`
- Test: `packages/optio-claudecode/tests/test_oauth_filter_claude_json.py` (create)

The noise filter is `seed_signature(blob_plain: bytes) -> dict` (oauth.py:230), which returns `{"members": sorted([...]), "settingsKeys": [...]}` and skips volatile files (`.claude/.credentials.json`, `.claude.json`). After the relocation the live file is `.claude/.claude.json`, so the filter must skip that too — otherwise two equivalent seeds would diff on it.

- [ ] **Step 1: Add the new path to the skip set + update the docstring.** In `seed_signature`, change the membership check (oauth.py:242) and the docstring's exclusion note (oauth.py:234–235):

```python
def seed_signature(blob_plain: bytes) -> dict:
    """Format/value-agnostic structural signature of a seed's non-auth
    environment, for divergence comparison against the pool's reference seed:
    the sorted member paths plus the sorted key set of .claude/settings.json,
    EXCLUDING .claude/.credentials.json (auth -- differs per seed) and the
    .claude.json project-trust file (noisy: timestamps/userID differ between
    good seeds) at either layout (.claude.json or .claude/.claude.json)."""
    members = []
    settings_keys = []
    try:
        with tarfile.open(fileobj=io.BytesIO(blob_plain), mode="r:gz") as tar:
            for m in tar.getmembers():
                if not m.isfile() or m.name in (
                    ".claude/.credentials.json", ".claude.json", ".claude/.claude.json",
                ):
                    continue
```

(Leave the rest of the function — `members.append`, the settings.json key extraction, and the return — unchanged.)

- [ ] **Step 2: Write the test.** Create `tests/test_oauth_filter_claude_json.py`:

```python
"""seed_signature must ignore the relocated .claude/.claude.json (volatile:
timestamps/userID differ between good seeds), like it already ignores the old
root .claude.json."""
import io
import tarfile

from optio_claudecode import oauth


def _targz(names: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in names.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_relocated_claude_json_is_filtered_out():
    blob = _targz({
        ".claude/.claude.json": b"{}",
        ".claude/settings.json": b'{"a": 1}',
        ".claude/agents/x.md": b"hello",
    })
    sig = oauth.seed_signature(blob)
    assert ".claude/.claude.json" not in sig["members"]
    assert ".claude.json" not in sig["members"]
    assert ".claude/agents/x.md" in sig["members"]
    assert sig["settingsKeys"] == ["a"]  # settings.json still parsed
```

- [ ] **Step 3: Commit:**

```bash
git add packages/optio-claudecode/src/optio_claudecode/oauth.py \
        packages/optio-claudecode/tests/test_oauth_filter_claude_json.py
git commit -m "fix(optio-claudecode): oauth seed-diff filter ignores relocated .claude/.claude.json"
```

---

## Task V: Verification (run AFTER Tasks 1–3 land)

**Files:** none.

- [ ] **Step 1: Reinstall the worktree venv** (no new deps, but ensure editable installs are current):

```bash
.venv/bin/pip install -e packages/optio-core -e packages/optio-host -e packages/optio-agents -e packages/optio-claudecode
```

- [ ] **Step 2: Run the optio-claudecode suite.**

Run: `cd packages/optio-claudecode && ../../.venv/bin/python -m pytest -q`
Expected: all pass, including the three new test files. The real-claude regression
runs if `claude` is on PATH (it is on the dev host) and skips otherwise.

- [ ] **Step 3: Run optio-agents (seed engine unchanged, sanity).**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest -q`
Expected: all pass (no code changed there; confirms the manifest change didn't break seed round-trip tests).

- [ ] **Step 4: Commit any fixes** discovered during verification (small errors are expected — that's the point of deferred verification; e.g. the oauth function name/shape in Task 3):

```bash
git add -A && git commit -m "fix(optio-claudecode): address verification findings"
```

---

## Self-review (spec coverage)

- `CLAUDE_CONFIG_DIR=<home>/.claude` in launch env → T1.
- `.claude.json` relocation followed: manifest include both paths + rekey normalize → T2; oauth filter → T3.
- Consume-time back-compat for pre-existing (caller-encrypted) seeds, no migration → T2 (`moved_from_old` normalize) + the old-layout test.
- Archive now captures `.claude.json` — benign, no code change (noted, nothing to do).
- Tests: unit env assert + real-claude no-leak regression → T1; seed back-compat → T2; oauth filter → T3.
- `optio-agents` untouched (verified: include/extract already supports two paths) → only optio-claudecode files in the plan.

## Release (after merge, separate step)

Single package: patch-release **optio-claudecode** only.
