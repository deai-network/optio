"""Agent sessions never inherit the engine's virtualenv (optio_agents.session_env).

The engine is made to look like it runs from a venv (sys.prefix /
sys.base_prefix / VIRTUAL_ENV monkeypatched, the venv's bin leading PATH). The
real session runs up to codex's launch; that launch is recorded and replaced by
``/usr/bin/env`` under the same env and env_remove, so the test reads the
environment LocalHost really builds for the child, then the session stops.
"""

from __future__ import annotations

import os
import sys

import pytest

from optio_host.host import LocalHost, proc_wait

from optio_codex import CodexTaskConfig, create_codex_task

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


async def _run(ctx, shim_install_dir, **kw) -> None:
    task = create_codex_task(
        process_id="codex-engine-venv", name="venv",
        config=CodexTaskConfig(
            consumer_instructions="do it",
            install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            delivery_type="audit",
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

    assert seen, "codex was never launched"
    # codex's PATH is exported on its command line; the venv is nowhere in it.
    assert "PATH=" in seen["command"]
    assert engine_venv not in seen["command"]
    # The tmux server (which holds codex) starts without the engine's Python.
    assert not {"VIRTUAL_ENV", "PYTHONPATH"} & set(seen["env"])


async def test_the_conversation_launch_inherits_no_engine_venv(
    engine_venv, shim_install_dir, task_root, ctx_and_captures, monkeypatch,
):
    ctx, *_ = ctx_and_captures
    seen = _capture_launch(monkeypatch, lambda cmd: "app-server" in cmd)
    await _run(ctx, shim_install_dir, mode="conversation", auto_start=False)

    assert seen, "codex was never launched"
    path = seen["env"]["PATH"].split(":")
    assert engine_venv not in path and "/usr/bin" in path
    assert not {"VIRTUAL_ENV", "PYTHONPATH"} & set(seen["env"])


def test_the_probe_path_leaves_out_the_engine_venv(engine_venv):
    from optio_codex import host_actions

    class _Host:
        workdir = "/w"

    path = host_actions._codex_isolation_env(_Host())["PATH"].split(":")
    assert path[0] == "/w/home/.local/bin"
    assert engine_venv not in path and "/usr/bin" in path
