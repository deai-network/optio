"""write_grok_config: force ``[cli] auto_update = false`` in the task's
``$GROK_HOME/config.toml`` so grok never self-downloads a fresh ~150 MB binary
into ``home/.grok/downloads`` mid-session. That download otherwise bloats the
resume snapshot enough that ``capture_snapshot`` overruns the cancel grace and
the task force-fails. The edit is merge-safe and TOML-correct: ``auto_update``
must land INSIDE the ``[cli]`` table (a bare key appended after a foreign
``[table]`` header would be parsed as a member of that table)."""

from __future__ import annotations

import pathlib
import tomllib

import pytest
from optio_host.host import LocalHost

from optio_grok import host_actions


async def _host(tmp_path: pathlib.Path) -> LocalHost:
    host = LocalHost(taskdir=str(tmp_path / "task"))
    await host.setup_workdir()
    return host


def _cfg(host: LocalHost) -> pathlib.Path:
    return pathlib.Path(host.workdir) / "home" / ".grok" / "config.toml"


@pytest.mark.asyncio
async def test_creates_config_when_absent(tmp_path: pathlib.Path):
    host = await _host(tmp_path)
    await host_actions.write_grok_config(host, host.workdir)
    cfg = _cfg(host)
    assert cfg.is_file()
    assert tomllib.loads(cfg.read_text())["cli"]["auto_update"] is False


@pytest.mark.asyncio
async def test_flips_existing_true_to_false(tmp_path: pathlib.Path):
    host = await _host(tmp_path)
    cfg = _cfg(host)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('[cli]\ninstaller = "internal"\nauto_update = true\n')
    await host_actions.write_grok_config(host, host.workdir)
    data = tomllib.loads(cfg.read_text())
    assert data["cli"]["auto_update"] is False
    # Other keys in [cli] are preserved.
    assert data["cli"]["installer"] == "internal"


@pytest.mark.asyncio
async def test_adds_key_to_existing_cli_without_autoupdate(tmp_path: pathlib.Path):
    host = await _host(tmp_path)
    cfg = _cfg(host)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('[cli]\ninstaller = "internal"\n')
    await host_actions.write_grok_config(host, host.workdir)
    data = tomllib.loads(cfg.read_text())
    assert data["cli"]["auto_update"] is False
    assert data["cli"]["installer"] == "internal"


@pytest.mark.asyncio
async def test_adds_cli_section_without_leaking_into_foreign_table(tmp_path: pathlib.Path):
    """When there is no [cli] table, auto_update goes under a NEW [cli] table —
    never bleeding into a preceding foreign table."""
    host = await _host(tmp_path)
    cfg = _cfg(host)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('[auth]\ntoken = "abc"\n')
    await host_actions.write_grok_config(host, host.workdir)
    data = tomllib.loads(cfg.read_text())
    assert data["cli"]["auto_update"] is False
    # The foreign table is untouched — auto_update did NOT become auth.auto_update.
    assert data["auth"] == {"token": "abc"}
    assert "auto_update" not in data["auth"]


@pytest.mark.asyncio
async def test_idempotent(tmp_path: pathlib.Path):
    host = await _host(tmp_path)
    await host_actions.write_grok_config(host, host.workdir)
    first = _cfg(host).read_text()
    await host_actions.write_grok_config(host, host.workdir)
    second = _cfg(host).read_text()
    assert first == second
    assert tomllib.loads(second)["cli"]["auto_update"] is False
