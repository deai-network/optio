"""Config defaults + the claudecode seed-manifest rekey transform."""

import json
import os

from optio_host.host import LocalHost

from optio_claudecode import ClaudeCodeTaskConfig
from optio_claudecode.seed_manifest import (
    CLAUDE_SEED_MANIFEST,
    CLAUDE_SEED_SUFFIX,
    _rekey_claude_json_projects,
)


def test_seed_config_defaults_none():
    cfg = ClaudeCodeTaskConfig(consumer_instructions="hi")
    assert cfg.seed_id is None
    assert cfg.on_seed_saved is None


def test_manifest_shape():
    assert CLAUDE_SEED_SUFFIX == "_claudecode_seeds"
    assert CLAUDE_SEED_MANIFEST.home_subdir == "home"
    assert ".claude/.credentials.json" in CLAUDE_SEED_MANIFEST.include
    assert ".claude.json" in CLAUDE_SEED_MANIFEST.include
    assert ".claude/plugins" in CLAUDE_SEED_MANIFEST.include


async def test_rekey_single_entry_to_new_cwd(tmp_workdir):
    host = LocalHost(taskdir=os.path.join(tmp_workdir, "r"))
    await host.setup_workdir()
    home = os.path.join(host.workdir, "home")
    os.makedirs(home, exist_ok=True)
    cj = os.path.join(home, ".claude.json")
    with open(cj, "w") as fh:
        json.dump({"userID": "u", "projects": {"/old/cwd": {"allowedTools": ["Bash"]}}}, fh)

    await _rekey_claude_json_projects(host)

    with open(cj) as fh:
        data = json.load(fh)
    assert list(data["projects"].keys()) == [host.workdir]
    assert data["projects"][host.workdir] == {"allowedTools": ["Bash"]}
    assert data["userID"] == "u"


async def test_rekey_multi_entry_left_untouched(tmp_workdir):
    host = LocalHost(taskdir=os.path.join(tmp_workdir, "m"))
    await host.setup_workdir()
    home = os.path.join(host.workdir, "home")
    os.makedirs(home, exist_ok=True)
    cj = os.path.join(home, ".claude.json")
    original = {"projects": {"/a": {}, "/b": {}}}
    with open(cj, "w") as fh:
        json.dump(original, fh)

    await _rekey_claude_json_projects(host)

    with open(cj) as fh:
        assert json.load(fh) == original


async def test_rekey_missing_file_is_noop(tmp_workdir):
    host = LocalHost(taskdir=os.path.join(tmp_workdir, "n"))
    await host.setup_workdir()
    await _rekey_claude_json_projects(host)  # must not raise
