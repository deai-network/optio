"""Tests for LocalHost resume-related methods."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from optio_opencode.host import LocalHost

FAKE = os.path.join(os.path.dirname(__file__), "fake_opencode.py")


def _populate_workdir(workdir: str) -> None:
    os.makedirs(workdir, exist_ok=True)
    Path(workdir, "AGENTS.md").write_text("# instructions\n")
    Path(workdir, "data.txt").write_text("payload\n")


async def test_launch_opencode_env_is_propagated_to_subprocess(tmp_path):
    """env kwarg lands in opencode's environment.

    fake_opencode.py supports `--env-dump <path>` (added in Task 20) which
    writes os.environ as JSON to <path> on startup.
    """
    dump = tmp_path / "env.json"

    host = LocalHost(taskdir=str(tmp_path), opencode_cmd=[sys.executable, FAKE])
    await host.setup_workdir()
    proc = await host.launch_opencode(
        password="pw",
        ready_timeout_s=5.0,
        extra_args=["--scenario", "happy", "--env-dump", str(dump)],
        env={"OPENCODE_DB": "/tmp/fake.db", "X": "1"},
    )
    for _ in range(50):
        if dump.exists():
            break
        await asyncio.sleep(0.05)
    await host.terminate_opencode(proc, aggressive=True)
    assert dump.exists(), "fake_opencode did not dump its env; env propagation likely broken"
    env = json.loads(dump.read_text())
    assert env.get("OPENCODE_DB") == "/tmp/fake.db"
    assert env.get("X") == "1"


async def test_opencode_export_then_import_roundtrip(tmp_path):
    """Use the fake's `export <id>` and `import <file>` subcommands."""
    db = tmp_path / "opencode.db"

    host = LocalHost(taskdir=str(tmp_path), opencode_cmd=[sys.executable, FAKE])
    await host.setup_workdir()

    (tmp_path / "seed.json").write_text(json.dumps({"id": "sess-42", "messages": []}))
    await host.opencode_import(str(db), (tmp_path / "seed.json").read_bytes())

    out = await host.opencode_export(str(db), "sess-42")
    decoded = json.loads(out.decode("utf-8"))
    assert decoded["id"] == "sess-42"


async def test_archive_workdir_yields_chunks(tmp_path):
    host = LocalHost(taskdir=str(tmp_path), opencode_cmd=[sys.executable, FAKE])
    _populate_workdir(host.workdir)

    chunks = []
    async for c in host.archive_workdir(exclude=None):
        chunks.append(c)
    assert len(b"".join(chunks)) > 0


async def test_restore_workdir_empties_then_extracts(tmp_path):
    src_host = LocalHost(taskdir=str(tmp_path / "src"), opencode_cmd=[sys.executable, FAKE])
    _populate_workdir(src_host.workdir)

    dst_host = LocalHost(taskdir=str(tmp_path / "dst"), opencode_cmd=[sys.executable, FAKE])
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
