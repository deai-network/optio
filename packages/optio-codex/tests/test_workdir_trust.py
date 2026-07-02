"""ensure_workdir_trusted: idempotent [projects."<workdir>"] trust edit.

The edit is deliberately minimal (append-if-absent, never a structured TOML
rewrite): codex rewrites config.toml itself at runtime, so optio only
guarantees the trust entry exists at launch and otherwise keeps its hands
off the file.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from optio_host.host import LocalHost

from optio_codex.host_actions import ensure_workdir_trusted


@pytest_asyncio.fixture
async def host(tmp_path):
    h = LocalHost(taskdir=str(tmp_path / "t"))
    await h.setup_workdir()
    return h


def _config_path(host) -> str:
    return os.path.join(host.workdir, "home", ".codex", "config.toml")


def _read(host) -> str:
    with open(_config_path(host), encoding="utf-8") as fh:
        return fh.read()


async def test_creates_config_with_trust_entry_when_absent(host):
    # No home/.codex at all (a seed may lack config.toml entirely).
    await ensure_workdir_trusted(host)
    text = _read(host)
    assert f'[projects."{host.workdir}"]' in text
    assert 'trust_level = "trusted"' in text


async def test_appends_to_existing_config_preserving_content(host):
    d = os.path.join(host.workdir, "home", ".codex")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "config.toml"), "w", encoding="utf-8") as fh:
        fh.write('model = "gpt-5.5"\n')
    await ensure_workdir_trusted(host)
    text = _read(host)
    assert text.startswith('model = "gpt-5.5"\n')          # untouched prefix
    assert f'[projects."{host.workdir}"]' in text
    assert 'trust_level = "trusted"' in text


async def test_idempotent_second_call_is_byte_identical(host):
    await ensure_workdir_trusted(host)
    first = _read(host)
    await ensure_workdir_trusted(host)
    assert _read(host) == first


async def test_existing_trust_entry_not_duplicated(host):
    d = os.path.join(host.workdir, "home", ".codex")
    os.makedirs(d, exist_ok=True)
    entry = f'[projects."{host.workdir}"]\ntrust_level = "trusted"\n'
    with open(os.path.join(d, "config.toml"), "w", encoding="utf-8") as fh:
        fh.write(entry)
    await ensure_workdir_trusted(host)
    assert _read(host).count(f'[projects."{host.workdir}"]') == 1
