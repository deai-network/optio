"""Stage 5: optio-owned, evictable grok binary cache + auto-install.

``ensure_grok_installed`` resolves grok through a cache dir that lives OUTSIDE
the task workdir and never the operator's ``~/.grok``, and returns a per-task
launch symlink into that cache (``<workdir>/home/.local/bin/grok``):

* cache HIT — ``<cache>/grok`` already executable → linked into the task path,
  no seed/install.
* cache MISS, host grok present — the host grok is copied into ``<cache>/grok``
  (seed, deref), then linked into the task path.
* cache MISS, no host grok — the vendor installer bootstraps grok into the
  persistent cache (HOME = cache root, outside any workdir), then linked in.
* ``install_if_missing=False`` on a miss — a clear error (nothing to do).
* default location — ``GROK_CACHE_DIR`` / ``${XDG_CACHE_HOME:-$HOME/.cache}``,
  resolved against the worker's real env; never under the workdir.
"""

from __future__ import annotations

import os
import pathlib
from types import SimpleNamespace

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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


async def _local_ctx(tmp_path: pathlib.Path) -> _FakeHookCtx:
    host = LocalHost(taskdir=str(tmp_path / "task"))
    await host.setup_workdir()
    return _FakeHookCtx(host)


def _task_path(ctx: _FakeHookCtx) -> str:
    return f"{ctx._host.workdir.rstrip('/')}/home/.local/bin/grok"


@pytest.mark.asyncio
async def test_cache_hit_links_into_task_path(tmp_path: pathlib.Path, monkeypatch):
    cache = tmp_path / "cache"
    _write_exe(cache / "grok")

    # A cache hit must not consult the host grok or the installer at all.
    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not seed/install on a cache hit")

    monkeypatch.setattr(host_actions, "resolve_grok", _boom)
    monkeypatch.setattr(host_actions, "_install_grok_into_cache", _boom)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_grok_installed(ctx, install_dir=str(cache))
    # Returns the per-task launch path (a symlink), NOT the raw cache path.
    assert result == _task_path(ctx)
    assert os.path.islink(result)
    assert os.path.realpath(result) == str((cache / "grok").resolve())
    assert os.access(result, os.X_OK)


@pytest.mark.asyncio
async def test_cache_miss_seeds_from_host_grok(tmp_path: pathlib.Path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()  # empty → miss
    source = _write_exe(tmp_path / "hostbin" / "grok")

    async def _resolve(host, *, install_dir=None, install_if_missing=True):  # noqa: ANN001
        return str(source)

    async def _no_install(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must seed from host grok, not vendor-install")

    monkeypatch.setattr(host_actions, "resolve_grok", _resolve)
    monkeypatch.setattr(host_actions, "_install_grok_into_cache", _no_install)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_grok_installed(ctx, install_dir=str(cache))
    assert result == _task_path(ctx)
    # Cache holds a real, dereferenced copy (not a symlink back to the host bin).
    assert (cache / "grok").is_file()
    assert not (cache / "grok").is_symlink()
    assert os.access(cache / "grok", os.X_OK)
    # Task path is a symlink into the cache.
    assert os.path.islink(result)
    assert os.path.realpath(result) == str((cache / "grok").resolve())


@pytest.mark.asyncio
async def test_cache_miss_no_host_grok_auto_installs(tmp_path: pathlib.Path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()

    async def _resolve(host, *, install_dir=None, install_if_missing=True):  # noqa: ANN001
        raise RuntimeError("grok not found on the worker")

    called = {}

    async def _fake_install(hook_ctx, host, *, cache_dir, cached):  # noqa: ANN001
        called["cache_dir"] = cache_dir
        called["cached"] = cached
        _write_exe(pathlib.Path(cached))  # emulate a successful install

    monkeypatch.setattr(host_actions, "resolve_grok", _resolve)
    monkeypatch.setattr(host_actions, "_install_grok_into_cache", _fake_install)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_grok_installed(ctx, install_dir=str(cache))
    assert called["cache_dir"] == str(cache)
    assert called["cached"] == str(cache / "grok")
    assert result == _task_path(ctx)
    assert os.path.islink(result)
    assert os.path.realpath(result) == str((cache / "grok").resolve())


@pytest.mark.asyncio
async def test_vendor_install_targets_persistent_cache_outside_workdir(tmp_path: pathlib.Path):
    """The installer runs with HOME = the cache ROOT (persistent, outside the
    workdir) and GROK_BIN_DIR = the cache dir, via the official install URL."""
    workdir = tmp_path / "task" / "work"
    cache_dir = tmp_path / "persistent-cache" / "optio-grok" / "bin"
    cached = cache_dir / "grok"
    cache_root = cache_dir.parent  # dirname → the persistent install HOME

    recorded: list[str] = []

    class _RecordingHost:
        def __init__(self, wd: str) -> None:
            self.workdir = wd

        async def run_command(self, cmd: str):  # noqa: ANN001
            recorded.append(cmd)
            if "install.sh" in cmd:  # emulate the installer creating the binary
                _write_exe(cached)
            ok = "[ -x" in cmd and cached.exists()
            return SimpleNamespace(exit_code=0, stdout=("OK" if ok else ""), stderr="")

    ctx = _FakeHookCtx(host=None)  # type: ignore[arg-type]
    await host_actions._install_grok_into_cache(
        ctx, _RecordingHost(str(workdir)), cache_dir=str(cache_dir), cached=str(cached),
    )

    install_cmd = next(c for c in recorded if "install.sh" in c)
    assert host_actions._GROK_INSTALL_URL in install_cmd
    assert f"HOME={str(cache_root)}" in install_cmd
    assert f"GROK_BIN_DIR={str(cache_dir)}" in install_cmd
    # The install HOME (cache root) is OUTSIDE the task workdir.
    assert not str(cache_root).startswith(str(workdir))
    assert cached.exists()


@pytest.mark.asyncio
async def test_no_install_raises_on_miss(tmp_path: pathlib.Path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()

    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not seed/install when install_if_missing=False")

    monkeypatch.setattr(host_actions, "resolve_grok", _boom)
    monkeypatch.setattr(host_actions, "_install_grok_into_cache", _boom)
    ctx = await _local_ctx(tmp_path)

    with pytest.raises(RuntimeError, match="install_if_missing=False"):
        await host_actions.ensure_grok_installed(
            ctx, install_dir=str(cache), install_if_missing=False,
        )


# --- cache-HIT staleness refresh (version-gated) ---------------------------


@pytest.mark.asyncio
async def test_grok_update_available_parses_json(tmp_path: pathlib.Path):
    """_grok_update_available returns the CLI's `updateAvailable`, running the
    check under HOME = the cache ROOT (never the operator's ~/.grok)."""
    recorded: list[str] = []

    class _H:
        async def run_command(self, cmd: str):  # noqa: ANN001
            recorded.append(cmd)
            return SimpleNamespace(
                exit_code=0,
                stdout='{"currentVersion":"0.2.82","latestVersion":"0.2.87",'
                '"updateAvailable":true,"error":null}',
                stderr="",
            )

    avail = await host_actions._grok_update_available(
        _H(), "/cache/bin/grok", cache_dir="/cache/bin",
    )
    assert avail is True
    cmd = recorded[0]
    assert "update --check --json" in cmd
    assert "/cache/bin/grok" in cmd
    # Runs under HOME = the cache ROOT (dirname of cache_dir), not operator ~.
    assert "HOME=/cache" in cmd


@pytest.mark.asyncio
async def test_grok_update_available_false_when_current():
    class _H:
        async def run_command(self, cmd: str):  # noqa: ANN001
            return SimpleNamespace(
                exit_code=0, stdout='{"updateAvailable":false}', stderr="",
            )

    assert await host_actions._grok_update_available(
        _H(), "/c/grok", cache_dir="/c",
    ) is False


@pytest.mark.asyncio
async def test_grok_update_available_best_effort_on_failure():
    """A non-zero exit (network down) or unparseable output → False: the update
    probe must never block a launch."""
    class _Fail:
        async def run_command(self, cmd: str):  # noqa: ANN001
            return SimpleNamespace(exit_code=1, stdout="", stderr="network down")

    class _Garbage:
        async def run_command(self, cmd: str):  # noqa: ANN001
            return SimpleNamespace(exit_code=0, stdout="not json", stderr="")

    assert await host_actions._grok_update_available(
        _Fail(), "/c/grok", cache_dir="/c",
    ) is False
    assert await host_actions._grok_update_available(
        _Garbage(), "/c/grok", cache_dir="/c",
    ) is False


@pytest.mark.asyncio
async def test_cache_hit_stale_refreshes_to_latest(tmp_path: pathlib.Path, monkeypatch):
    """A cache HIT whose binary is behind the latest release is refreshed via the
    vendor installer BEFORE it is linked into the task (keeps grok from feeling
    the need to self-download a fresh binary into the workdir)."""
    cache = tmp_path / "cache"
    _write_exe(cache / "grok")

    async def _stale(host, cached, *, cache_dir):  # noqa: ANN001
        return True

    refreshed: dict[str, str] = {}

    async def _fake_install(hook_ctx, host, *, cache_dir, cached):  # noqa: ANN001
        refreshed["cached"] = cached
        _write_exe(pathlib.Path(cached), body="#!/bin/bash\necho fresh\n")

    async def _no_seed(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not seed from host grok on a cache hit")

    monkeypatch.setattr(host_actions, "_grok_update_available", _stale)
    monkeypatch.setattr(host_actions, "_install_grok_into_cache", _fake_install)
    monkeypatch.setattr(host_actions, "resolve_grok", _no_seed)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_grok_installed(ctx, install_dir=str(cache))
    assert refreshed["cached"] == str(cache / "grok")
    assert result == _task_path(ctx)
    assert os.path.realpath(result) == str((cache / "grok").resolve())


@pytest.mark.asyncio
async def test_cache_hit_current_does_not_refresh(tmp_path: pathlib.Path, monkeypatch):
    cache = tmp_path / "cache"
    _write_exe(cache / "grok")

    async def _current(host, cached, *, cache_dir):  # noqa: ANN001
        return False

    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not refresh a current cache")

    monkeypatch.setattr(host_actions, "_grok_update_available", _current)
    monkeypatch.setattr(host_actions, "_install_grok_into_cache", _boom)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_grok_installed(ctx, install_dir=str(cache))
    assert result == _task_path(ctx)


@pytest.mark.asyncio
async def test_cache_hit_stale_not_refreshed_when_install_disabled(
    tmp_path: pathlib.Path, monkeypatch,
):
    """install_if_missing=False: a stale HIT still links the existing cache and
    never triggers a network update-check or install."""
    cache = tmp_path / "cache"
    _write_exe(cache / "grok")

    async def _no_check(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("update-check must be skipped when installs are off")

    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not install when install_if_missing=False")

    monkeypatch.setattr(host_actions, "_grok_update_available", _no_check)
    monkeypatch.setattr(host_actions, "_install_grok_into_cache", _boom)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_grok_installed(
        ctx, install_dir=str(cache), install_if_missing=False,
    )
    assert result == _task_path(ctx)


@pytest.mark.asyncio
async def test_default_cache_dir_from_grok_cache_dir_env(tmp_path: pathlib.Path, monkeypatch):
    """With no override, GROK_CACHE_DIR (worker real env) decides the cache dir —
    never the workdir, never the operator's ~/.grok. The returned task path is a
    symlink whose real target (the cache) is outside the workdir."""
    cache = tmp_path / "xai-cache" / "bin"
    _write_exe(cache / "grok")
    monkeypatch.setenv("GROK_CACHE_DIR", str(cache))
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_grok_installed(ctx)  # no install_dir
    assert result == _task_path(ctx)
    # The cached binary (symlink target) is NOT under the task workdir.
    assert not os.path.realpath(result).startswith(ctx._host.workdir)
    assert os.path.realpath(result) == str((cache / "grok").resolve())
