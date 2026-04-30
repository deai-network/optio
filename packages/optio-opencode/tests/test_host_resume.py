"""Tests for LocalHost resume-related actions (now in host_actions)."""

import json
import os
import sys
from pathlib import Path

import pytest

from optio_host.host import LocalHost
from optio_opencode import host_actions

FAKE = os.path.join(os.path.dirname(__file__), "fake_opencode.py")
FAKE_EXEC = f"{sys.executable} {FAKE}"


def _populate_workdir(workdir: str) -> None:
    os.makedirs(workdir, exist_ok=True)
    Path(workdir, "AGENTS.md").write_text("# instructions\n")
    Path(workdir, "data.txt").write_text("payload\n")


# TODO: substitution mechanism needs rework post-host-split — the launch_opencode
# free function builds a fixed `opencode web --port=0 --hostname=127.0.0.1`
# command, so the env-dump round-trip pattern (which relied on opencode_cmd
# being a list of argv tokens) doesn't translate cleanly. The opencode_export
# / opencode_import roundtrip below works because the fake executable is used
# verbatim via opencode_executable=...
@pytest.mark.skip(
    reason="substitution mechanism needs rework post-host-split"
)
async def test_launch_opencode_env_is_propagated_to_subprocess(tmp_path):
    pass


async def test_opencode_export_then_import_roundtrip(tmp_path):
    """Use the fake's `export <id>` and `import <file>` subcommands."""
    db = tmp_path / "opencode.db"

    host = LocalHost(taskdir=str(tmp_path))
    await host.setup_workdir()

    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps({"id": "sess-42", "messages": []}))
    await host_actions.opencode_import(
        host, str(db), seed_path.read_bytes(),
        opencode_executable=FAKE_EXEC,
    )

    out = await host_actions.opencode_export(
        host, str(db), "sess-42", opencode_executable=FAKE_EXEC,
    )
    decoded = json.loads(out.decode("utf-8"))
    assert decoded["id"] == "sess-42"


async def test_archive_workdir_yields_chunks(tmp_path):
    host = LocalHost(taskdir=str(tmp_path))
    _populate_workdir(host.workdir)

    chunks = []
    async for c in host.archive_workdir(exclude=None):
        chunks.append(c)
    assert len(b"".join(chunks)) > 0


async def test_restore_workdir_empties_then_extracts(tmp_path):
    src_host = LocalHost(taskdir=str(tmp_path / "src"))
    _populate_workdir(src_host.workdir)

    dst_host = LocalHost(taskdir=str(tmp_path / "dst"))
    os.makedirs(dst_host.workdir, exist_ok=True)
    Path(dst_host.workdir, "stale.txt").write_text("zzz")

    chunks = []
    async for c in src_host.archive_workdir(exclude=None):
        chunks.append(c)

    async def replay():
        for c in chunks:
            yield c

    await dst_host.restore_workdir(replay())

    assert not Path(dst_host.workdir, "stale.txt").exists()
    assert Path(dst_host.workdir, "AGENTS.md").read_text() == "# instructions\n"
