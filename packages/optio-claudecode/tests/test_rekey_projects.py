"""_rekey_claude_json_projects collapses .claude.json projects to a single
trusted entry at the launch workdir, so an autonomous session is never blocked
by claude's folder-trust prompt (which bypassPermissions does not suppress)."""

import json
import os

from optio_host.host import LocalHost

from optio_claudecode.seed_manifest import _rekey_claude_json_projects


async def _run(tmp_workdir, name, claude_json):
    host = LocalHost(taskdir=os.path.join(tmp_workdir, name))
    await host.setup_workdir()
    # claude runs under CLAUDE_CONFIG_DIR=<home>/.claude, so .claude.json lives
    # inside .claude/ (not the old home root).
    cdir = os.path.join(host.workdir, "home", ".claude")
    os.makedirs(cdir, exist_ok=True)
    with open(os.path.join(cdir, ".claude.json"), "w") as fh:
        json.dump(claude_json, fh)
    await _rekey_claude_json_projects(host)
    with open(os.path.join(cdir, ".claude.json")) as fh:
        return host, json.load(fh)


async def test_multi_entry_collapses_to_single_trusted(tmp_workdir):
    # two foreign workdir entries, neither is the new cwd
    host, out = await _run(tmp_workdir, "m", {
        "userID": "u",
        "projects": {
            "/old/setup/wd": {"hasTrustDialogAccepted": True, "allowedTools": ["x"]},
            "/home/csillag/deai/excavator": {"hasTrustDialogAccepted": False},
        },
    })
    assert list(out["projects"].keys()) == [host.workdir]
    v = out["projects"][host.workdir]
    assert v["hasTrustDialogAccepted"] is True
    assert v.get("allowedTools") == ["x"]  # preserved from the trusted entry
    assert out["userID"] == "u"  # other top-level keys untouched


async def test_single_entry_rekeyed_and_trusted(tmp_workdir):
    host, out = await _run(tmp_workdir, "s", {
        "projects": {"/old/cwd": {"hasTrustDialogAccepted": True, "mcp": 1}},
    })
    assert list(out["projects"].keys()) == [host.workdir]
    assert out["projects"][host.workdir]["hasTrustDialogAccepted"] is True
    assert out["projects"][host.workdir].get("mcp") == 1


async def test_no_projects_gets_a_trusted_entry(tmp_workdir):
    host, out = await _run(tmp_workdir, "e", {"userID": "u"})
    assert out["projects"] == {host.workdir: {"hasTrustDialogAccepted": True}}
