"""Stage 5: optio-owned, evictable codex binary cache.

``ensure_codex_installed`` resolves the codex binary through a cache dir
that lives outside the task workdir and never the operator's ``~/.codex``:

* cache HIT — ``<cache>/codex`` already executable → per-task symlink to it.
* cache MISS — the resolved host codex is copied into ``<cache>/codex``
  (``cp -L`` deref: a stable copy, independent of host autoupdates).
* default location — ``OPTIO_CODEX_CACHE_DIR`` /
  ``${XDG_CACHE_HOME:-$HOME/.cache}/optio-codex/bin``, resolved against the
  worker's real env; never under the workdir.
* the RETURNED path is always Plan A's per-task
  ``<workdir>/home/.local/bin/codex`` symlink (kill-scoping preserved) —
  it now resolves INTO the cache.
"""

from __future__ import annotations

import os
import pathlib

import pytest
from optio_host.host import LocalHost

from optio_codex import host_actions


class _FakeHookCtx:
    """Minimal hook_ctx: a real LocalHost plus a no-op progress reporter."""

    def __init__(self, host: LocalHost) -> None:
        self._host = host

    def report_progress(self, percent, message=None) -> None:  # noqa: ANN001
        pass


def _write_exe(path: pathlib.Path, body: str = "#!/bin/bash\necho fake-codex\n") -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


async def _local_ctx(tmp_path: pathlib.Path) -> _FakeHookCtx:
    host = LocalHost(taskdir=str(tmp_path / "task"))
    await host.setup_workdir()
    return _FakeHookCtx(host)


def _per_task_path(ctx: _FakeHookCtx) -> str:
    return f"{ctx._host.workdir}/home/.local/bin/codex"


@pytest.mark.asyncio
async def test_cache_hit_returns_per_task_symlink_into_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    _write_exe(cache / "codex")

    # A cache hit must not consult the host codex at all.
    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("resolve_codex should not be called on a cache hit")

    monkeypatch.setattr(host_actions, "resolve_codex", _boom)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_codex_installed(ctx, install_dir=str(cache))
    # Plan A's kill-scoped per-task launch path is preserved…
    assert result == _per_task_path(ctx)
    # …and now resolves into the optio-owned cache.
    assert os.path.realpath(result) == os.path.realpath(str(cache / "codex"))


@pytest.mark.asyncio
async def test_cache_miss_seeds_from_host_codex(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()  # empty → miss
    source = _write_exe(tmp_path / "hostbin" / "codex")

    async def _resolve(host, *, install_dir=None, install_if_missing=True):  # noqa: ANN001
        return str(source)

    monkeypatch.setattr(host_actions, "resolve_codex", _resolve)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_codex_installed(ctx, install_dir=str(cache))
    assert result == _per_task_path(ctx)
    assert (cache / "codex").is_file()
    assert os.access(cache / "codex", os.X_OK)
    # Seeded as a real copy (cp -L deref), not a symlink back to the host
    # binary (which the operator may autoupdate under us).
    assert not (cache / "codex").is_symlink()
    assert os.path.realpath(result) == os.path.realpath(str(cache / "codex"))


@pytest.mark.asyncio
async def test_no_install_raises_on_miss(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()

    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not seed when install_if_missing=False")

    monkeypatch.setattr(host_actions, "resolve_codex", _boom)
    ctx = await _local_ctx(tmp_path)

    with pytest.raises(RuntimeError, match="install_if_missing=False"):
        await host_actions.ensure_codex_installed(
            ctx, install_dir=str(cache), install_if_missing=False,
        )


@pytest.mark.asyncio
async def test_default_cache_dir_from_env(tmp_path, monkeypatch):
    """With no override, OPTIO_CODEX_CACHE_DIR (worker real env) decides the
    cache dir — never the workdir, never the operator's ~/.codex."""
    cache = tmp_path / "oai-cache" / "bin"
    _write_exe(cache / "codex")
    monkeypatch.setenv("OPTIO_CODEX_CACHE_DIR", str(cache))
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_codex_installed(ctx)  # no install_dir
    assert result == _per_task_path(ctx)
    assert os.path.realpath(result) == os.path.realpath(str(cache / "codex"))


@pytest.mark.asyncio
async def test_cache_miss_no_host_codex_raises(tmp_path, monkeypatch):
    # Task 8 replaces this expectation with the real release download.
    cache = tmp_path / "cache"
    cache.mkdir()

    async def _resolve(host, *, install_dir=None, install_if_missing=True):  # noqa: ANN001
        raise RuntimeError("codex not found on the worker")

    monkeypatch.setattr(host_actions, "resolve_codex", _resolve)
    ctx = await _local_ctx(tmp_path)

    with pytest.raises(RuntimeError, match="no codex binary"):
        await host_actions.ensure_codex_installed(ctx, install_dir=str(cache))
