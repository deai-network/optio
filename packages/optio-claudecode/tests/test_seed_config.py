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


def test_claudecode_config_scrub_env_default_none():
    from optio_claudecode.types import ClaudeCodeTaskConfig
    assert ClaudeCodeTaskConfig(consumer_instructions="hi").scrub_env is None


def test_manifest_shape():
    assert CLAUDE_SEED_SUFFIX == "_claudecode_seeds"
    assert CLAUDE_SEED_MANIFEST.home_subdir == "home"
    assert ".claude/.credentials.json" in CLAUDE_SEED_MANIFEST.include
    assert ".claude.json" in CLAUDE_SEED_MANIFEST.include
    assert ".claude/settings.json" in CLAUDE_SEED_MANIFEST.include
    # plugins (the official marketplace) are NOT seeded -- re-installed on launch
    assert ".claude/plugins" not in CLAUDE_SEED_MANIFEST.include


async def test_rekey_single_entry_to_new_cwd(tmp_workdir):
    host = LocalHost(taskdir=os.path.join(tmp_workdir, "r"))
    await host.setup_workdir()
    cdir = os.path.join(host.workdir, "home", ".claude")
    os.makedirs(cdir, exist_ok=True)
    cj = os.path.join(cdir, ".claude.json")
    with open(cj, "w") as fh:
        json.dump({"userID": "u", "projects": {"/old/cwd": {"allowedTools": ["Bash"]}}}, fh)

    await _rekey_claude_json_projects(host)

    with open(cj) as fh:
        data = json.load(fh)
    assert list(data["projects"].keys()) == [host.workdir]
    # value preserved + trust forced so an autonomous launch isn't prompted
    assert data["projects"][host.workdir] == {"allowedTools": ["Bash"], "hasTrustDialogAccepted": True}
    assert data["userID"] == "u"


async def test_rekey_multi_entry_collapses_to_single_trusted(tmp_workdir):
    host = LocalHost(taskdir=os.path.join(tmp_workdir, "m"))
    await host.setup_workdir()
    cdir = os.path.join(host.workdir, "home", ".claude")
    os.makedirs(cdir, exist_ok=True)
    cj = os.path.join(cdir, ".claude.json")
    # two foreign entries, neither the new cwd -> collapse to one trusted entry
    with open(cj, "w") as fh:
        json.dump({"projects": {"/a": {}, "/b": {}}}, fh)

    await _rekey_claude_json_projects(host)

    with open(cj) as fh:
        data = json.load(fh)
    assert data["projects"] == {host.workdir: {"hasTrustDialogAccepted": True}}


async def test_rekey_missing_file_is_noop(tmp_workdir):
    host = LocalHost(taskdir=os.path.join(tmp_workdir, "n"))
    await host.setup_workdir()
    await _rekey_claude_json_projects(host)  # must not raise


def test_cred_manifest_is_credentials_only():
    from optio_claudecode.seed_manifest import (
        CLAUDE_CRED_MANIFEST, CLAUDE_SEED_MANIFEST,
    )

    assert CLAUDE_CRED_MANIFEST.include == [".claude/.credentials.json"]
    # narrow manifest needs no rekey transform
    assert CLAUDE_CRED_MANIFEST.consume_transform is None
    # full manifest is composed FROM the narrow one (no duplicated path)
    assert CLAUDE_SEED_MANIFEST.include[:1] == CLAUDE_CRED_MANIFEST.include
    assert ".claude/plugins" not in CLAUDE_SEED_MANIFEST.include
    assert CLAUDE_SEED_MANIFEST.consume_transform is not None
