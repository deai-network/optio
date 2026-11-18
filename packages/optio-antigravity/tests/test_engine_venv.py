"""Agent sessions never inherit the engine's virtualenv (optio_agents.session_env).

The engine is made to look like it runs from a venv (sys.prefix /
sys.base_prefix / VIRTUAL_ENV monkeypatched, the venv's bin leading PATH). The
real session runs up to agy's launch; that launch is recorded and the
environment LocalHost builds for it is read with ``/usr/bin/env`` under the
same env and env_remove, then the session stops. In conversation mode each turn
is its own ``agy -p`` launched by AntigravityConversation.send, so the session
test records what the session hands the conversation, and a unit test checks
that send launches each turn with it.
"""

from __future__ import annotations

import os
import sys

import pytest

from optio_host.host import LocalHost, proc_wait

from optio_antigravity import AntigravityTaskConfig, create_antigravity_task
from optio_antigravity import session as session_mod
from optio_antigravity.conversation import AntigravityConversation

_LAUNCH = LocalHost.launch_subprocess


class _Launched(Exception):
    """Raised in place of the agent launch once it is recorded."""


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
    probe = await _LAUNCH(
        host, "/usr/bin/env", env=kw.get("env"), env_remove=kw.get("env_remove"), cwd="/",
    )
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


async def _run(ctx, shim_install_dir, **kw) -> None:
    task = create_antigravity_task(
        process_id="agy-engine-venv", name="venv",
        config=AntigravityTaskConfig(
            consumer_instructions="do it",
            install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            fs_isolation=False,
            supports_resume=False,
            **kw,
        ),
    )
    try:
        await task.execute(ctx)
    except Exception:  # noqa: BLE001 - the recorded launch stops the session
        pass


async def test_the_tmux_launch_inherits_no_engine_venv(
    engine_venv, shim_install_dir, task_root, ctx_and_captures, monkeypatch,
):
    ctx, *_ = ctx_and_captures
    seen = _capture_launch(monkeypatch, lambda cmd: "new-session" in cmd)
    await _run(ctx, shim_install_dir)

    assert seen, "agy was never launched"
    # agy's PATH is set on its command line; the venv is nowhere in it.
    assert "PATH=" in seen["command"]
    assert engine_venv not in seen["command"]
    # The tmux server (which holds agy) starts without the engine's Python.
    assert not {"VIRTUAL_ENV", "PYTHONPATH"} & set(seen["env"])


async def test_the_conversation_turns_inherit_no_engine_venv(
    engine_venv, shim_install_dir, task_root, ctx_and_captures, monkeypatch,
):
    ctx, *_ = ctx_and_captures
    made: dict = {}

    def _record(**kw):
        made.update(kw)
        raise _Launched("AntigravityConversation")

    monkeypatch.setattr(session_mod, "AntigravityConversation", _record)
    await _run(ctx, shim_install_dir, mode="conversation", auto_start=False)

    assert made, "the conversation was never made"
    env = await _child_env(made["host"], made)
    path = env["PATH"].split(":")
    assert engine_venv not in path and "/usr/bin" in path
    assert not {"VIRTUAL_ENV", "PYTHONPATH"} & set(env)


async def test_each_turn_is_launched_with_the_env_remove(tmp_path):
    calls: list[dict] = []

    class _Host:
        async def launch_subprocess(self, command, **kw):
            calls.append(kw)
            raise _Launched(command)

    conv = AntigravityConversation(
        host=_Host(), agy_path="agy", cwd=str(tmp_path), home=str(tmp_path),
        env={"HOME": str(tmp_path)}, env_remove=["VIRTUAL_ENV", "PYTHONPATH"], pty=False,
    )
    with pytest.raises(_Launched):
        await conv.send("say hi")
    assert calls[0]["env_remove"] == ["VIRTUAL_ENV", "PYTHONPATH"]
    assert calls[0]["env"] == {"HOME": str(tmp_path)}
