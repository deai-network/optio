"""MCP servers for Claude Code (data chat v1, spec Part A).

On every launch, fresh and resume, the task's MCP servers are resolved
(``optio_agents.mcp.resolve_mcp_servers``) and written to
``<workdir>/home/.claude/optio-mcp.json`` (mode 0600), and claude is pointed
at that file with ``--mcp-config <path> --strict-mcp-config``. Without servers
a stale file (a restored snapshot's) is removed and no flags are passed. The
model probe gets no MCP flags; a model/effort relaunch reuses the launch's
flags. Header values (tokens) are never logged.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import pathlib
import stat
import tarfile
import time as _time

import pytest
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from optio_agents.mcp import McpServer
from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process
from optio_host.host import LocalHost
from optio_host.paths import task_dir

from optio_claudecode import ClaudeCodeTaskConfig, create_claudecode_task, host_actions
from optio_claudecode.session import run_claudecode_session
from optio_claudecode.snapshots import load_latest_snapshot

from .test_conversation_ui_session import (
    _make_optio,
    _plant_transcript_on,
    _ui_config,
    _wait_terminal,
)

MCP_FILE = "home/.claude/optio-mcp.json"


def _kontext(token: str = "t") -> McpServer:
    return McpServer(
        name="kontext", url="https://k.example/mcp",
        headers={"Authorization": f"Bearer {token}"},
    )


def _counting_provider(calls: list[str]):
    """A provider minting a new token on every call: token-1, token-2, ..."""
    async def provider(process_id: str) -> list[McpServer]:
        calls.append(process_id)
        return [_kontext(f"token-{len(calls)}")]
    return provider


def _mcp_path(process_id: str) -> str:
    taskdir = task_dir(ssh=None, process_id=process_id, consumer_name="optio-claudecode")
    return os.path.join(taskdir, "workdir", MCP_FILE)


def _assert_points_at(argv: list[str], path: str) -> None:
    i = argv.index("--mcp-config")
    assert argv[i + 1:i + 3] == [path, "--strict-mcp-config"], argv


# --- plant_mcp_config ------------------------------------------------------


def _local_host(tmp_path: pathlib.Path) -> LocalHost:
    host = LocalHost(taskdir=str(tmp_path / "task"))
    os.makedirs(host.workdir)
    return host


async def test_plant_mcp_config_writes_the_servers(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    host = _local_host(tmp_path)
    path = await host_actions.plant_mcp_config(host, host.workdir, [_kontext()])
    assert path == f"{host.workdir}/{MCP_FILE}"
    assert pathlib.Path(path).read_text(encoding="utf-8") == (
        '{"mcpServers": {"kontext": {"type": "http", "url": "https://k.example/mcp", '
        '"headers": {"Authorization": "Bearer t"}}}}'
    )
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert "Bearer t" not in caplog.text


async def test_plant_mcp_config_replaces_a_restored_file_owner_only(tmp_path):
    """A snapshot restores the previous launch's file (old token, any mode):
    it is replaced, and the new one is readable by its owner only."""
    host = _local_host(tmp_path)
    stale = pathlib.Path(host.workdir, MCP_FILE)
    stale.parent.mkdir(parents=True)
    stale.write_text('{"mcpServers": {"old": {"type": "http", "url": "https://o"}}}')
    os.chmod(stale, 0o644)
    path = await host_actions.plant_mcp_config(host, host.workdir, [_kontext("new")])
    servers = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))["mcpServers"]
    assert list(servers) == ["kontext"]
    assert servers["kontext"]["headers"] == {"Authorization": "Bearer new"}
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


async def test_plant_mcp_config_removes_a_stale_file_when_there_are_no_servers(tmp_path):
    host = _local_host(tmp_path)
    stale = pathlib.Path(host.workdir, MCP_FILE)
    stale.parent.mkdir(parents=True)
    stale.write_text('{"mcpServers": {}}')
    assert await host_actions.plant_mcp_config(host, host.workdir, []) is None
    assert not stale.exists()
    # Nothing to remove (no file, no home/.claude) is fine too.
    os.rmdir(stale.parent)
    assert await host_actions.plant_mcp_config(host, host.workdir, []) is None


# --- launches, iframe mode (tmux + fake claude) ------------------------------


async def _ctx(mongo_db, process_id: str, *, resume: bool) -> ProcessContext:
    task = TaskInstance(
        execute=lambda c: None,  # type: ignore[arg-type, return-value]
        process_id=process_id, name=process_id, supports_resume=True,
    )
    proc = await upsert_process(mongo_db, "test", task)
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"status": {"state": "running"}}},
    )
    return ProcessContext(
        process_oid=proc["_id"], process_id=process_id, root_oid=proc["_id"],
        depth=0, params={}, services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
        resume=resume,
    )


def _iframe_config(shim_install_dir, claude_cache_dir, **kw) -> ClaudeCodeTaskConfig:
    return ClaudeCodeTaskConfig(
        consumer_instructions="(mcp)",
        fs_isolation=False,
        install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        permission_mode="bypassPermissions",
        supports_resume=True,
        # The snapshot guard wants credentials on disk.
        credentials_json={"token": "test"},
        **kw,
    )


async def _blob_files(mongo_db, blob_id) -> dict[str, bytes]:
    stream = await AsyncIOMotorGridFSBucket(mongo_db).open_download_stream(blob_id)
    data = await stream.read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}


def _member(files: dict[str, bytes], suffix: str) -> bytes:
    return next(data for name, data in files.items() if name.endswith(suffix))


@pytest.mark.serial
async def test_a_resumed_launch_resolves_the_provider_again(
    mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch, caplog,
):
    """The provider is awaited on the fresh launch and again on the resume, so
    the resumed claude gets a fresh token, not the one the restored snapshot
    carried (Review Focus 5)."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "idempotent_done")
    pid = "cc_mcp_resume"
    calls: list[str] = []
    config = _iframe_config(
        shim_install_dir, claude_cache_dir, mcp_servers=_counting_provider(calls),
    )
    for resume in (False, True):
        await run_claudecode_session(await _ctx(mongo_db, pid, resume=resume), config)

    assert calls == [pid, pid]
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    session = await _blob_files(mongo_db, snap["sessionBlobId"])
    servers = json.loads(_member(session, MCP_FILE))["mcpServers"]
    assert servers["kontext"]["headers"] == {"Authorization": "Bearer token-2"}

    launches = [json.loads(line) for line in
                _member(session, "fake_claude_argv.json").decode().splitlines() if line]
    assert len(launches) == 2
    for argv in launches:
        _assert_points_at(argv, _mcp_path(pid))
    # The resume notice is a positional prompt: after `--`, so no multi-value
    # option before it can take it for one of its values.
    assert launches[1][-2] == "--"
    assert launches[1][-1].startswith("System:")

    # Tokens stay in the encrypted session blob: never in the plaintext
    # workdir blob, never in a log line.
    workdir = await _blob_files(mongo_db, snap["workdirBlobId"])
    assert not any(name.endswith("optio-mcp.json") for name in workdir)
    assert "token-1" not in caplog.text and "token-2" not in caplog.text


async def test_a_failing_provider_fails_the_launch_with_its_message(
    task_root, shim_install_dir, claude_cache_dir, ctx_and_captures,
):
    ctx, _captured, _flag = ctx_and_captures

    async def provider(process_id: str) -> list[McpServer]:
        raise RuntimeError("Kontext target 'Docs': token refused")

    task = create_claudecode_task(
        process_id="cc-mcp-fail", name="MCP provider fails",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="Hi.",
            fs_isolation=False,
            install_dir=str(claude_cache_dir),
            ttyd_install_dir=str(shim_install_dir),
            mcp_servers=provider,
        ),
    )
    with pytest.raises(RuntimeError, match="Kontext target 'Docs': token refused"):
        await task.execute(ctx)


# --- launches, conversation mode ---------------------------------------------


async def _wait_for(predicate, timeout: float = 60.0) -> None:
    end = _time.monotonic() + timeout
    while not predicate():
        if _time.monotonic() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


@pytest.mark.serial
async def test_the_model_probe_gets_no_mcp_flags(
    shim_install_dir, claude_cache_dir, task_root, mongo_db, tmp_path,
):
    """A resume restoring a model pick first asks a short-lived claude for its
    model list: that probe gets no MCP flags. Every real launch does (the
    fresh one, the relaunch for the pick, the resumed one), and the provider
    is awaited once per launch: a relaunch reuses the launch's flags."""
    argv_log = tmp_path / "argv.jsonl"
    pid = "cc-mcp-probe"
    calls: list[str] = []
    optio = await _make_optio(mongo_db, "ccmcp")
    try:
        task = create_claudecode_task(
            process_id=pid, name="MCP and the model probe",
            config=_ui_config(
                shim_install_dir, claude_cache_dir, show_session_controls=True,
                supports_resume=True, credentials_json={"token": "test"},
                before_execute=_plant_transcript_on("claude-opus-5[1m]"),
                env={"FAKE_CLAUDE_ARGV_LOG": str(argv_log)},
                tools=[], mcp_servers=_counting_provider(calls),
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(pid, session_id=None, timeout=60)
        await conv.set_control("model", "sonnet")  # a relaunch; saved as a pick
        await _wait_for(lambda: conv.runtime_model == "claude-sonnet-5")
        await conv.close()
        await _wait_terminal(optio, pid)

        conv2 = await optio.launch_and_await_result(
            pid, resume=True, session_id=None, timeout=60,
        )
        await _wait_for(lambda: conv2.runtime_model == "claude-sonnet-5")
        await conv2.close()
        await _wait_terminal(optio, pid)
    finally:
        await optio.shutdown(grace_seconds=1.0)

    assert calls == [pid, pid]
    launches = [json.loads(line) for line in argv_log.read_text().splitlines() if line]
    # conversation_ui launches replay user messages; the probe does not.
    probes = [argv for argv in launches if "--replay-user-messages" not in argv]
    spawned = [argv for argv in launches if "--replay-user-messages" in argv]
    assert len(probes) == 1 and len(spawned) == 3, launches
    assert "--mcp-config" not in probes[0]
    assert "--strict-mcp-config" not in probes[0]
    for argv in spawned:
        _assert_points_at(argv, _mcp_path(pid))
        assert argv[argv.index("--tools") + 1] == ""
