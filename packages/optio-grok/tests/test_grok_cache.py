"""Stage 5: optio-owned, evictable grok binary cache.

``ensure_grok_installed`` resolves the grok binary through a cache dir that
lives outside the task workdir and never the operator's ``~/.grok``:

* cache HIT — ``<cache>/grok`` already executable → returned directly, no copy.
* cache MISS — the resolved host grok is copied into ``<cache>/grok`` (seed).
* no host grok — a clear "future refinement" error (vendor auto-install is not
  wired yet; grok's bootstrap-installer URL is unconfirmed).
* default location — ``GROK_CACHE_DIR`` / ``${XDG_CACHE_HOME:-$HOME/.cache}``,
  resolved against the worker's real env; never under the workdir.
"""

from __future__ import annotations

import os
import pathlib

import pytest
from optio_host.host import LocalHost

from optio_grok import host_actions


class _FakeHookCtx:
    """Minimal hook_ctx: a real LocalHost plus a no-op progress reporter."""

    def __init__(self, host: LocalHost) -> None:
        self._host = host

    def report_progress(self, percent, message=None) -> None:  # noqa: ANN001
        pass


def _write_exe(path: pathlib.Path, body: str = "#!/bin/bash\necho fake-grok\n") -> pathlib.Path:
    path.write_text(body)
    path.chmod(0o755)
    return path


async def _local_ctx(tmp_path: pathlib.Path) -> _FakeHookCtx:
    host = LocalHost(taskdir=str(tmp_path / "task"))
    await host.setup_workdir()
    return _FakeHookCtx(host)


@pytest.mark.asyncio
async def test_cache_hit_returns_cached_path(tmp_path: pathlib.Path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    _write_exe(cache / "grok")

    # A cache hit must not consult the host grok at all.
    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("resolve_grok should not be called on a cache hit")

    monkeypatch.setattr(host_actions, "resolve_grok", _boom)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_grok_installed(ctx, install_dir=str(cache))
    assert result == str(cache / "grok")


@pytest.mark.asyncio
async def test_cache_miss_seeds_from_host_grok(tmp_path: pathlib.Path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()  # empty → miss
    source = _write_exe(tmp_path / "hostbin" / "grok") if (tmp_path / "hostbin").mkdir() or True else None

    async def _resolve(host, *, install_dir=None, install_if_missing=True):  # noqa: ANN001
        return str(source)

    monkeypatch.setattr(host_actions, "resolve_grok", _resolve)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_grok_installed(ctx, install_dir=str(cache))
    assert result == str(cache / "grok")
    assert (cache / "grok").is_file()
    assert os.access(cache / "grok", os.X_OK)
    # Seeded as a real copy (deref), not a symlink back to the host binary.
    assert not (cache / "grok").is_symlink()


@pytest.mark.asyncio
async def test_cache_miss_no_host_grok_raises(tmp_path: pathlib.Path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()

    async def _resolve(host, *, install_dir=None, install_if_missing=True):  # noqa: ANN001
        raise RuntimeError("grok not found on the worker")

    monkeypatch.setattr(host_actions, "resolve_grok", _resolve)
    ctx = await _local_ctx(tmp_path)

    with pytest.raises(RuntimeError, match="future refinement"):
        await host_actions.ensure_grok_installed(ctx, install_dir=str(cache))


@pytest.mark.asyncio
async def test_no_install_raises_on_miss(tmp_path: pathlib.Path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()

    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not seed when install_if_missing=False")

    monkeypatch.setattr(host_actions, "resolve_grok", _boom)
    ctx = await _local_ctx(tmp_path)

    with pytest.raises(RuntimeError, match="install_if_missing=False"):
        await host_actions.ensure_grok_installed(
            ctx, install_dir=str(cache), install_if_missing=False,
        )


@pytest.mark.asyncio
async def test_default_cache_dir_from_grok_cache_dir_env(tmp_path: pathlib.Path, monkeypatch):
    """With no override, GROK_CACHE_DIR (worker real env) decides the cache dir —
    never the workdir, never the operator's ~/.grok."""
    cache = tmp_path / "xai-cache" / "bin"
    _write_exe((cache).joinpath("grok") if cache.mkdir(parents=True) or True else cache)
    monkeypatch.setenv("GROK_CACHE_DIR", str(cache))
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_grok_installed(ctx)  # no install_dir
    assert result == str(cache / "grok")
    # The cache dir is NOT under the task workdir.
    assert not result.startswith(ctx._host.workdir)
