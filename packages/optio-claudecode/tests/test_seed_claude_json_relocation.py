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
