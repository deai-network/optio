"""Task 4.1: optio-owned, evictable kimi binary cache + two-tier install.

``ensure_kimicode_installed`` provisions ``kimi`` for a task through a cache dir
that lives OUTSIDE the task workdir (so workdir teardown never destroys the
install) and never the operator's ``~/.kimi-code``, and returns a per-task launch
symlink into that cache (``<workdir>/home/.local/bin/kimi``):

* cache HIT — ``<cache>/kimi`` already executable → linked into the task path,
  no seed/install (the idempotent relink a resume restore depends on).
* cache MISS, worker kimi on the login-shell PATH — TIER 1: the worker kimi is
  fast-copied (dereferenced) into ``<cache>/kimi``, then linked in. No download.
* cache MISS, no worker kimi — TIER 2: the vendor installer bootstraps kimi into
  the persistent cache (HOME = a staging root outside any workdir), then linked in.
* ``install_if_missing=False`` on a miss — a clear error (nothing to do).
* default location — ``OPTIO_KIMICODE_CACHE_DIR`` / ``${XDG_CACHE_HOME:-$HOME/.cache}``,
  resolved against the worker's real env; never under the workdir (the snapshot
  excludes it — ``Host.archive_workdir`` only tars ``host.workdir``).

Mirrors optio-grok's ``test_grok_cache.py`` (grok's ``ensure_grok_installed`` is
hook_ctx-based; kimi's is host-based, symmetric with ``resolve_kimi`` and the
engine-free ``verify`` path — so the tests drive a bare ``LocalHost``, no
hook_ctx). The vendor installer is exercised only through a FAKE (no network);
the real download is a tracked opt-in follow-up below.
"""

from __future__ import annotations

import os
import pathlib
import socket
from types import SimpleNamespace

import pytest
from optio_host.host import LocalHost

from optio_kimicode import host_actions


def _write_exe(
    path: pathlib.Path, body: str = "#!/bin/bash\necho fake-kimi\n",
) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


async def _local_host(tmp_path: pathlib.Path) -> LocalHost:
    host = LocalHost(taskdir=str(tmp_path / "task"))
    await host.setup_workdir()
    return host


def _task_path(host: LocalHost) -> str:
    return f"{host.workdir.rstrip('/')}/home/.local/bin/kimi"


@pytest.mark.asyncio
async def test_tier1_seeds_from_worker_kimi(tmp_path: pathlib.Path, monkeypatch):
    """TIER 1: an empty cache + a kimi on the worker login-shell PATH → the
    worker kimi is copied (dereferenced) into the cache and symlinked into the
    task launch path; the vendor installer is never touched."""
    cache = tmp_path / "cache"
    cache.mkdir()  # empty → miss
    source = _write_exe(tmp_path / "workerbin" / "kimi")

    async def _resolve(host, *, install_dir=None, install_if_missing=True):  # noqa: ANN001
        # A kimi already resolvable on the worker (the PATH-probe seam).
        return str(source)

    async def _no_install(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("tier-1 must copy the worker kimi, not vendor-install")

    monkeypatch.setattr(host_actions, "resolve_kimi", _resolve)
    monkeypatch.setattr(host_actions, "_install_kimicode_into_cache", _no_install)
    host = await _local_host(tmp_path)

    result = await host_actions.ensure_kimicode_installed(host, install_dir=str(cache))

    assert result == _task_path(host)
    # The cache holds a real, dereferenced copy (not a symlink back to the worker
    # bin the operator may replace / autoupdate).
    assert (cache / "kimi").is_file()
    assert not (cache / "kimi").is_symlink()
    assert os.access(cache / "kimi", os.X_OK)
    # The task launch path is a symlink into the cache; it resolves + is runnable.
    assert os.path.islink(result)
    assert os.path.realpath(result) == str((cache / "kimi").resolve())
    assert os.access(result, os.X_OK)


@pytest.mark.asyncio
async def test_cache_hit_relinks_only(tmp_path: pathlib.Path, monkeypatch):
    """A cache HIT re-links the task path only — it does NOT re-copy or
    re-download. Calling twice (the resume relink) is idempotent."""
    cache = tmp_path / "cache"
    _write_exe(cache / "kimi")

    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("a cache hit must not seed, populate, or install")

    monkeypatch.setattr(host_actions, "resolve_kimi", _boom)
    monkeypatch.setattr(host_actions, "_install_kimicode_into_cache", _boom)
    monkeypatch.setattr(host_actions, "_populate_kimicode_cache", _boom)
    host = await _local_host(tmp_path)

    before_ino = (cache / "kimi").stat().st_ino
    before_bytes = (cache / "kimi").read_bytes()

    result = await host_actions.ensure_kimicode_installed(host, install_dir=str(cache))
    assert result == _task_path(host)
    assert os.path.islink(result)
    assert os.path.realpath(result) == str((cache / "kimi").resolve())
    # The cache binary is byte- and inode-identical: only the symlink was (re)made.
    assert (cache / "kimi").stat().st_ino == before_ino
    assert (cache / "kimi").read_bytes() == before_bytes

    # Second call (mirrors the post-resume relink after the workdir was wiped +
    # restored) is idempotent — same path, still no population.
    result2 = await host_actions.ensure_kimicode_installed(host, install_dir=str(cache))
    assert result2 == result
    assert os.path.realpath(result2) == str((cache / "kimi").resolve())


@pytest.mark.asyncio
async def test_tier2_vendor_install_on_bare_worker(tmp_path: pathlib.Path, monkeypatch):
    """The done-criterion: a BARE worker (empty cache, no kimi on PATH) bootstraps
    itself via the vendor installer, then links the freshly installed binary into
    the task launch path."""
    cache = tmp_path / "cache"
    cache.mkdir()  # empty → miss

    async def _no_worker_kimi(host, *, install_dir=None, install_if_missing=True):  # noqa: ANN001
        raise RuntimeError("kimi not found on the worker")

    called: dict[str, str] = {}

    async def _fake_install(host, *, cache_dir, cached):  # noqa: ANN001
        called["cache_dir"] = cache_dir
        called["cached"] = cached
        _write_exe(pathlib.Path(cached))  # emulate a successful vendor install

    monkeypatch.setattr(host_actions, "resolve_kimi", _no_worker_kimi)
    monkeypatch.setattr(host_actions, "_install_kimicode_into_cache", _fake_install)
    host = await _local_host(tmp_path)

    result = await host_actions.ensure_kimicode_installed(host, install_dir=str(cache))
    assert called["cache_dir"] == str(cache)
    assert called["cached"] == str(cache / "kimi")
    assert result == _task_path(host)
    assert os.path.islink(result)
    assert os.path.realpath(result) == str((cache / "kimi").resolve())


@pytest.mark.asyncio
async def test_install_if_missing_false_empty_cache_raises(
    tmp_path: pathlib.Path, monkeypatch,
):
    """An empty cache + ``install_if_missing=False`` raises a clear error and
    never consults the worker kimi or the installer."""
    cache = tmp_path / "cache"
    cache.mkdir()  # empty

    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not seed/install when install_if_missing=False")

    monkeypatch.setattr(host_actions, "resolve_kimi", _boom)
    monkeypatch.setattr(host_actions, "_install_kimicode_into_cache", _boom)
    host = await _local_host(tmp_path)

    with pytest.raises(RuntimeError, match="install_if_missing=False"):
        await host_actions.ensure_kimicode_installed(
            host, install_dir=str(cache), install_if_missing=False,
        )


@pytest.mark.asyncio
async def test_default_cache_dir_is_outside_workdir(
    tmp_path: pathlib.Path, monkeypatch,
):
    """With no override, ``OPTIO_KIMICODE_CACHE_DIR`` (worker real env) decides the
    cache dir — never under the workdir. Because the resolved cache (the symlink
    target) is outside ``host.workdir``, the resume snapshot — which is
    ``cd host.workdir && tar czf - .`` (``Host.archive_workdir``) — can never
    capture it."""
    cache = tmp_path / "kimi-cache" / "bin"
    _write_exe(cache / "kimi")
    monkeypatch.setenv("OPTIO_KIMICODE_CACHE_DIR", str(cache))
    host = await _local_host(tmp_path)

    result = await host_actions.ensure_kimicode_installed(host)  # no install_dir
    assert result == _task_path(host)
    real = os.path.realpath(result)
    assert real == str((cache / "kimi").resolve())
    # The cached binary is NOT under the task workdir → excluded from the snapshot.
    assert not real.startswith(host.workdir.rstrip("/"))


@pytest.mark.asyncio
async def test_vendor_installer_targets_cache_outside_workdir(tmp_path: pathlib.Path):
    """``_install_kimicode_into_cache`` runs the official install URL with
    ``HOME`` = a staging root under the cache (persistent, outside any workdir),
    leaving an executable ``<cache>/kimi``."""
    workdir = tmp_path / "task" / "work"
    cache_dir = tmp_path / "persistent" / "optio-kimicode" / "bin"
    cached = cache_dir / "kimi"
    cache_root = cache_dir.parent  # dirname → the installer HOME

    recorded: list[str] = []

    class _RecordingHost:
        def __init__(self, wd: str) -> None:
            self.workdir = wd

        async def run_command(self, cmd: str):  # noqa: ANN001
            recorded.append(cmd)
            if "install.sh" in cmd:  # emulate the installer's net effect
                _write_exe(cached)
            ok = "[ -x" in cmd and cached.exists()
            return SimpleNamespace(exit_code=0, stdout=("OK" if ok else ""), stderr="")

    await host_actions._install_kimicode_into_cache(
        _RecordingHost(str(workdir)), cache_dir=str(cache_dir), cached=str(cached),
    )

    install_cmd = next(c for c in recorded if "install.sh" in c)
    assert host_actions._KIMICODE_INSTALL_URL in install_cmd
    assert f"HOME={cache_root}" in install_cmd
    # The install HOME (cache root) is OUTSIDE the task workdir.
    assert not str(cache_root).startswith(str(workdir))
    assert cached.exists()


def _online() -> bool:
    try:
        socket.create_connection(("code.kimi.com", 443), timeout=3).close()
        return True
    except OSError:
        return False


# TRACKED REAL-BINARY FOLLOW-UP (plan group 6 / row 30). The fake installer above
# proves the ORCHESTRATION (URL, HOME staging, link) without touching the network.
# This opt-in test proves the LIVE vendor installer actually lands a runnable kimi
# — it downloads a real release, so it is gated behind both an explicit opt-in env
# var AND connectivity. It also confirms the assumed install layout (the script
# places ``kimi`` under ``$HOME/.local/bin`` — see ``_KIMICODE_INSTALL_REL``).
@pytest.mark.skipif(
    not (os.environ.get("OPTIO_KIMICODE_REAL_INSTALL") and _online()),
    reason=(
        "real vendor install (network + heavy download): opt-in via "
        "OPTIO_KIMICODE_REAL_INSTALL=1 — tracked real-binary follow-up "
        "(plan group 6 / row 30)"
    ),
)
@pytest.mark.asyncio
async def test_real_vendor_install_lands_runnable_kimi(tmp_path: pathlib.Path):
    cache = tmp_path / "real-cache" / "bin"
    cache.mkdir(parents=True)
    host = await _local_host(tmp_path)

    result = await host_actions.ensure_kimicode_installed(host, install_dir=str(cache))
    assert os.access(result, os.X_OK)
    assert (cache / "kimi").is_file()
    probe = await host.run_command(f"{result} --version")
    assert probe.exit_code == 0
