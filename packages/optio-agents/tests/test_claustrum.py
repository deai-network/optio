"""Shared claustrum provisioning + hardened validation (optio_agents.claustrum).

Covers the functional validation that replaced the fooled ``--version`` check
(a ``#!/bin/sh`` stub passes ``--version`` but silently no-ops the launch), and
the ``engine_cache_dir`` parameter that keeps a test build out of the operator's
real cache.
"""

from __future__ import annotations

import pytest

from optio_agents import claustrum
from optio_host.host import RunResult


def test_is_elf(tmp_path):
    stub = tmp_path / "stub"
    stub.write_text("#!/bin/sh\n")
    assert claustrum.is_elf(str(stub)) is False
    elf = tmp_path / "elf"
    elf.write_bytes(b"\x7fELF" + b"\x00" * 60)
    assert claustrum.is_elf(str(elf)) is True
    assert claustrum.is_elf(str(tmp_path / "missing")) is False


class _Host:
    """Mock host driving claustrum provisioning. ``functional`` controls whether
    the wrap-exec probe echoes the sentinel (i.e. whether the placed binary is a
    working claustrum)."""

    def __init__(self, *, present: bool, functional: bool, cache: str):
        self.present = present
        self.functional = functional
        self._cache = cache
        self.commands: list[str] = []

    async def run_command(self, cmd, cwd=None):
        self.commands.append(cmd)
        if cmd == "uname -s":
            return RunResult(stdout="Linux", stderr="", exit_code=0)
        if cmd == "uname -m":
            return RunResult(stdout="x86_64", stderr="", exit_code=0)
        if cmd.startswith("test -x") and "echo present" in cmd:
            return RunResult(stdout="present\n" if self.present else "", stderr="", exit_code=0)
        if claustrum._PROBE_SENTINEL in cmd:
            out = f"{claustrum._PROBE_SENTINEL}\n" if self.functional else ""
            return RunResult(stdout=out, stderr="", exit_code=0)
        # chmod etc.
        return RunResult(stdout="", stderr="", exit_code=0)

    async def put_file_to_host(self, src, dst):
        return None


@pytest.mark.asyncio
async def test_claustrum_works_true_false():
    h_ok = _Host(present=True, functional=True, cache="/c")
    assert await claustrum.claustrum_works(h_ok, "/c/claustrum") is True
    h_bad = _Host(present=True, functional=False, cache="/c")
    assert await claustrum.claustrum_works(h_bad, "/c/claustrum") is False


@pytest.mark.asyncio
async def test_cache_hit_when_functional(tmp_path):
    host = _Host(present=True, functional=True, cache=str(tmp_path / "cache"))
    path = await claustrum.ensure_claustrum_installed(
        host,
        cache_dir=str(tmp_path / "cache"),
        engine_cache_dir=str(tmp_path / "engine"),
    )
    assert path.endswith("/claustrum/v0.1.1/amd64/claustrum")
    # A functional cache hit never triggers a build (no engine cache written).
    assert not (tmp_path / "engine").exists()


@pytest.mark.asyncio
async def test_fail_closed_on_nonfunctioning(tmp_path, monkeypatch):
    # A present-but-nonfunctioning claustrum (e.g. a stub) must RAISE, never launch
    # unconfined. The fake build writes to engine_cache_dir — which is tmp, proving
    # the isolation (the bug was a hardcoded ~/.cache the test poisoned).
    built = []

    async def _fake_build(goarch, tag, dest):
        import os
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w") as f:
            f.write("#!/bin/sh\n")
        built.append(dest)

    monkeypatch.setattr(claustrum, "build_claustrum_on_engine", _fake_build)
    host = _Host(present=False, functional=False, cache=str(tmp_path / "cache"))
    with pytest.raises(RuntimeError, match="functioning claustrum"):
        await claustrum.ensure_claustrum_installed(
            host,
            cache_dir=str(tmp_path / "cache"),
            engine_cache_dir=str(tmp_path / "engine"),
        )
    # The build wrote ONLY under the tmp engine_cache_dir — never the real cache.
    assert built and built[0].startswith(str(tmp_path / "engine"))


@pytest.mark.asyncio
async def test_detect_goarch_rejects_non_linux():
    class _H:
        async def run_command(self, cmd, cwd=None):
            return RunResult(stdout="Darwin", stderr="", exit_code=0)

    with pytest.raises(RuntimeError, match="Linux"):
        await claustrum.detect_goarch(_H())
