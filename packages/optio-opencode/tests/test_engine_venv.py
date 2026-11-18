"""Agent sessions never inherit the engine's virtualenv (optio_agents.session_env).

The engine is made to look like it runs from a venv (sys.prefix /
sys.base_prefix / VIRTUAL_ENV monkeypatched, the venv's bin leading PATH). The
real session runs up to ``opencode web``'s launch; that launch is recorded and
replaced by ``/usr/bin/env`` under the same env and env_remove, so the test
reads the environment LocalHost really builds for the child, then the session
stops.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
import pytest_asyncio
from bson import ObjectId

from optio_core.context import ProcessContext
from optio_host.host import LocalHost, proc_wait

from optio_opencode import host_actions
from optio_opencode.session import run_opencode_session
from optio_opencode.types import OpencodeTaskConfig

pytestmark = pytest.mark.serial

_LAUNCH = LocalHost.launch_subprocess


class _Launched(Exception):
    """Raised in place of the agent launch once it is recorded."""


@pytest_asyncio.fixture
async def ctx(mongo_db):
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({
        "_id": oid, "processId": "p", "name": "P", "params": {}, "metadata": {},
        "parentId": None, "rootId": None, "depth": 0, "order": 0, "adhoc": False,
        "ephemeral": False, "status": {"state": "running"},
        "progress": {"percent": None, "message": None}, "log": [],
    })
    return ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={}, services={},
        db=mongo_db, prefix="test", cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
    )


@pytest.fixture
def engine_venv(tmp_path, monkeypatch) -> str:
    venv = tmp_path / "engine-venv"
    (venv / "bin").mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr(sys, "base_prefix", "/opt/base-python")
    monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(
        p for p in (str(venv / "lib"), os.environ.get("PYTHONPATH")) if p))
    monkeypatch.setenv("PATH", f"{venv}/bin:{os.environ['PATH']}")
    return f"{venv}/bin"


async def _child_env(host, kw) -> dict[str, str]:
    probe = await _LAUNCH(host, "/usr/bin/env", env=kw.get("env"), env_remove=kw.get("env_remove"))
    out = b"".join([chunk async for chunk in probe.stdout])
    await proc_wait(probe)
    env: dict[str, str] = {}
    for line in out.decode("utf-8", "replace").splitlines():
        name, sep, value = line.partition("=")
        if sep and name.isidentifier():
            env.setdefault(name, value)
    return env


def _capture_launch(monkeypatch, is_agent) -> dict:
    seen: dict = {}

    async def _launch(self, command, **kw):
        if seen or not is_agent(command):
            return await _LAUNCH(self, command, **kw)
        seen["command"] = command
        seen["env"] = await _child_env(self, kw)
        raise _Launched(command)

    monkeypatch.setattr(LocalHost, "launch_subprocess", _launch)
    return seen


async def test_the_web_launch_inherits_no_engine_venv(engine_venv, ctx, monkeypatch):
    async def _ensure(host, **kwargs):
        return "opencode"

    async def _version(host, *, opencode_executable="opencode"):
        return None

    monkeypatch.setattr(host_actions, "ensure_opencode_installed", _ensure)
    monkeypatch.setattr(host_actions, "opencode_version", _version)
    seen = _capture_launch(monkeypatch, lambda cmd: " web --port=0" in cmd)
    config = OpencodeTaskConfig(
        consumer_instructions="do it", fs_isolation=False, supports_resume=False,
    )
    try:
        await run_opencode_session(ctx, config)
    except Exception:  # noqa: BLE001 - the recorded launch stops the session
        pass

    assert seen, "opencode was never launched"
    path = seen["env"]["PATH"].split(":")
    assert engine_venv not in path and "/usr/bin" in path
    assert not {"VIRTUAL_ENV", "PYTHONPATH"} & set(seen["env"])
