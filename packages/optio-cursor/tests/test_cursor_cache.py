"""Stage 5: optio-owned, evictable cursor-agent binary cache.

``ensure_cursor_installed`` resolves the ``cursor-agent`` binary through a
cache dir that lives OUTSIDE the task workdir and never the operator's
``~/.local/share/cursor-agent``, and returns a per-task launch symlink into
that cache (``<workdir>/home/.local/bin/cursor-agent``):

* cache HIT — ``<cache>/cursor-agent`` already executable → linked into the
  task path, no copy (this is also the Stage-0 session-test path: those tests
  pass ``install_dir=<shim dir>``, which is now the cache dir with the
  shim).
* cache MISS — the vendor installer is tried first (guarded: NEVER hit by
  these tests — its shell-command construction is unit-tested instead), then
  the host install is copied in. Unlike grok's single-file binary,
  ``cursor-agent`` is a symlink into a Node version dir
  (``.../cursor-agent/versions/<v>/``): the WHOLE version dir is copied and
  the cached entrypoint is a ``<cache>/cursor-agent`` symlink resolving
  through the copied tree. The task path is then linked to that entrypoint.
* nothing works — a clear error naming both failed population routes.
* default location — ``CURSOR_CACHE_DIR`` / ``${XDG_CACHE_HOME:-$HOME/.cache}/
  optio-cursor``, resolved against the worker's REAL env; never the workdir.

Adapted from optio-grok's ``test_grok_cache.py`` (version-dir copy semantics
and the vendor-installer branch are cursor-specific).
"""

from __future__ import annotations

import os
import pathlib

import pytest
from optio_host.host import LocalHost

from optio_cursor import host_actions


class _FakeHookCtx:
    """Minimal hook_ctx: a real LocalHost plus a no-op progress reporter."""

    def __init__(self, host: LocalHost) -> None:
        self._host = host

    def report_progress(self, percent, message=None) -> None:  # noqa: ANN001
        pass


def _write_exe(
    path: pathlib.Path, body: str = "#!/bin/bash\necho fake-cursor\n",
) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


def _fake_host_install(root: pathlib.Path, version: str = "1.0") -> pathlib.Path:
    """Replicate the vendor layout: ``.local/bin/cursor-agent`` symlink →
    ``.local/share/cursor-agent/versions/<v>/cursor-agent`` (plus a sibling
    dist file that MUST travel with the entrypoint)."""
    version_dir = root / ".local/share/cursor-agent/versions" / version
    entry = _write_exe(version_dir / "cursor-agent")
    (version_dir / "index.js").write_text("// fake node dist\n")
    bin_link = root / ".local/bin/cursor-agent"
    bin_link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(entry, bin_link)
    return bin_link


def _forbid_vendor_installer(monkeypatch) -> None:  # noqa: ANN001
    """Tests must NEVER reach the network-touching vendor installer."""

    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("vendor installer must not run in tests")

    monkeypatch.setattr(host_actions, "_vendor_install_cursor", _boom)


def _stub_vendor_unavailable(monkeypatch) -> None:  # noqa: ANN001
    """Vendor installer 'fails' (offline worker) → host-copy fallback."""

    async def _unavailable(host, cache_dir):  # noqa: ANN001
        return None

    monkeypatch.setattr(host_actions, "_vendor_install_cursor", _unavailable)


async def _local_ctx(tmp_path: pathlib.Path) -> _FakeHookCtx:
    host = LocalHost(taskdir=str(tmp_path / "task"))
    await host.setup_workdir()
    return _FakeHookCtx(host)


def _task_path(ctx: _FakeHookCtx) -> str:
    return f"{ctx._host.workdir.rstrip('/')}/home/.local/bin/cursor-agent"


@pytest.mark.asyncio
async def test_cache_hit_links_into_task_path(tmp_path: pathlib.Path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    _write_exe(cache / "cursor-agent")

    # A cache hit must not consult the host cursor-agent nor the installer.
    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("resolve_cursor must not be called on a cache hit")

    monkeypatch.setattr(host_actions, "resolve_cursor", _boom)
    _forbid_vendor_installer(monkeypatch)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_cursor_installed(ctx, install_dir=str(cache))
    # Returns the per-task launch path (a symlink), NOT the raw cache path.
    assert result == _task_path(ctx)
    assert os.path.islink(result)
    assert os.path.realpath(result) == str((cache / "cursor-agent").resolve())
    assert os.access(result, os.X_OK)


@pytest.mark.asyncio
async def test_cache_miss_copies_host_version_dir(
    tmp_path: pathlib.Path, monkeypatch,
):
    """Host-copy fallback carries the WHOLE version dir, not just the file the
    ``cursor-agent`` symlink points at."""
    cache = tmp_path / "cache"
    cache.mkdir()  # empty → miss
    bin_link = _fake_host_install(tmp_path / "hosthome")

    async def _resolve(host, *, install_dir=None, install_if_missing=True):  # noqa: ANN001
        return str(bin_link)

    monkeypatch.setattr(host_actions, "resolve_cursor", _resolve)
    _stub_vendor_unavailable(monkeypatch)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_cursor_installed(ctx, install_dir=str(cache))
    # Returns the per-task launch symlink, NOT the raw cache path.
    assert result == _task_path(ctx)
    assert os.path.islink(result)
    assert os.access(result, os.X_OK)
    # The cache entrypoint is a symlink into the COPIED version dir.
    assert os.path.islink(cache / "cursor-agent")
    # The entrypoint resolves (task symlink → cache symlink → copied tree)
    # into the cache, not back into the host install.
    real = pathlib.Path(result).resolve()
    assert real == (cache / "versions/1.0/cursor-agent").resolve()
    assert str(real).startswith(str(cache))
    # The sibling dist file travelled with the entrypoint (version-DIR copy).
    assert (cache / "versions/1.0/index.js").is_file()


@pytest.mark.asyncio
async def test_cache_miss_nothing_available_raises(
    tmp_path: pathlib.Path, monkeypatch,
):
    cache = tmp_path / "cache"
    cache.mkdir()

    async def _resolve(host, *, install_dir=None, install_if_missing=True):  # noqa: ANN001
        raise RuntimeError("cursor-agent not found on the worker")

    monkeypatch.setattr(host_actions, "resolve_cursor", _resolve)
    _stub_vendor_unavailable(monkeypatch)
    ctx = await _local_ctx(tmp_path)

    with pytest.raises(RuntimeError, match="no cursor-agent available"):
        await host_actions.ensure_cursor_installed(ctx, install_dir=str(cache))


@pytest.mark.asyncio
async def test_no_install_raises_on_miss(tmp_path: pathlib.Path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()

    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not populate when install_if_missing=False")

    monkeypatch.setattr(host_actions, "resolve_cursor", _boom)
    _forbid_vendor_installer(monkeypatch)
    ctx = await _local_ctx(tmp_path)

    with pytest.raises(RuntimeError, match="install_if_missing=False"):
        await host_actions.ensure_cursor_installed(
            ctx, install_dir=str(cache), install_if_missing=False,
        )


@pytest.mark.asyncio
async def test_default_cache_dir_from_cursor_cache_dir_env(
    tmp_path: pathlib.Path, monkeypatch,
):
    """With no override, CURSOR_CACHE_DIR (worker REAL env) decides the cache
    dir — never the task workdir, never ~/.local/share/cursor-agent."""
    cache = tmp_path / "optio-cursor-cache"
    cache.mkdir()
    _write_exe(cache / "cursor-agent")
    monkeypatch.setenv("CURSOR_CACHE_DIR", str(cache))
    _forbid_vendor_installer(monkeypatch)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_cursor_installed(ctx)  # no install_dir
    # The returned task path is a symlink whose real target (the cache) is
    # outside the workdir.
    assert result == _task_path(ctx)
    assert not os.path.realpath(result).startswith(ctx._host.workdir)
    assert os.path.realpath(result) == str((cache / "cursor-agent").resolve())


def test_vendor_install_command_construction(tmp_path: pathlib.Path):
    """The vendor-installer branch is never RUN by tests (network); its shell
    command is unit-tested instead: curl|bash with HOME pointed at the cache's
    staging tree so the install lands there, not in the operator's ~."""
    cache = str(tmp_path / "cache")
    cmd = host_actions._vendor_install_command(cache)
    assert "curl" in cmd
    assert "https://cursor.com/install" in cmd
    assert "-fsS" in cmd
    assert "| bash" in cmd
    assert f"HOME={cache}/staging" in cmd


# --- cache-HIT staleness refresh (launch-time freshness check) ---------------
#
# Freshness is provisioning-owned: claustrum grants the cache --rox to confined
# sessions, so only this unconfined path can ever refresh it. The upstream
# version source is the vendor install script itself (server-rendered with the
# latest version baked in — no separate version endpoint exists; see the
# _CURSOR_INSTALL_URL provenance comment). The fixture below mirrors the REAL
# script's shape as fetched 2026-08-13 (literal version token in
# DOWNLOAD_URL/FINAL_DIR lines).

_INSTALL_SCRIPT_UPSTREAM = (
    '#!/usr/bin/env bash\n'
    'TEMP_EXTRACT_DIR="$HOME/.local/share/cursor-agent/versions/'
    '.tmp-2026.08.11-e8db854-$(date +%s)"\n'
    'DOWNLOAD_URL="https://downloads.cursor.com/lab/2026.08.11-e8db854/'
    '${OS}/${ARCH}/agent-cli-package.tar.gz"\n'
    'FINAL_DIR="$HOME/.local/share/cursor-agent/versions/2026.08.11-e8db854"\n'
)


class _ScriptedHost:
    """Recording fake host: dispatches --version and curl commands."""

    def __init__(
        self,
        *,
        version_stdout: str = "2026.07.01-41b2de7\n",
        version_exit: int = 0,
        curl_stdout: str = _INSTALL_SCRIPT_UPSTREAM,
        curl_exit: int = 0,
    ) -> None:
        self.commands: list[str] = []
        self._version = (version_exit, version_stdout)
        self._curl = (curl_exit, curl_stdout)

    async def run_command(self, cmd: str):  # noqa: ANN001
        from types import SimpleNamespace

        self.commands.append(cmd)
        if "--version" in cmd:
            code, out = self._version
        elif "curl" in cmd:
            code, out = self._curl
        else:
            raise AssertionError(f"unexpected command: {cmd}")
        return SimpleNamespace(exit_code=code, stdout=out, stderr="fail-detail")


@pytest.mark.asyncio
async def test_cursor_update_target_returns_upstream_when_newer():
    """Cached 2026.07.01 vs upstream 2026.08.11 → the upstream token, probed
    as cached-version-first (HOME pinned into the cache dir) then curl of the
    install script."""
    h = _ScriptedHost()
    target = await host_actions._cursor_update_target(
        h, "/cache/cursor-agent", cache_dir="/cache",
    )
    assert target == "2026.08.11-e8db854"
    assert len(h.commands) == 2
    # Version probe runs the CACHED binary under HOME = the cache dir (its
    # incidental state writes must never touch the operator's home).
    assert "--version" in h.commands[0]
    assert "/cache/cursor-agent" in h.commands[0]
    assert "HOME=/cache" in h.commands[0]
    # Upstream resolution fetches the vendor install script (the version is
    # baked into it; it doubles as the exact refresh installer).
    assert "curl" in h.commands[1]
    assert host_actions._CURSOR_INSTALL_URL in h.commands[1]


@pytest.mark.asyncio
async def test_cursor_update_target_none_when_current():
    h = _ScriptedHost(
        version_stdout="2026.08.11-e8db854\n",
    )
    assert await host_actions._cursor_update_target(
        h, "/cache/cursor-agent", cache_dir="/cache",
    ) is None


@pytest.mark.asyncio
async def test_cursor_update_target_none_on_same_day_hash_drift():
    """Two same-day builds with different hashes carry no discoverable
    ordering — conservatively treated as current (no refresh churn)."""
    h = _ScriptedHost(version_stdout="2026.08.11-aaaa111\n")
    assert await host_actions._cursor_update_target(
        h, "/cache/cursor-agent", cache_dir="/cache",
    ) is None


@pytest.mark.asyncio
async def test_cursor_update_target_bails_before_network_on_bad_cached_version():
    """An unreadable cached version (failed probe OR unparseable output — e.g.
    a test shim printing 'fake-cursor') bails BEFORE any curl: offline/test
    environments never see a network round-trip."""
    for h in (
        _ScriptedHost(version_exit=1, version_stdout=""),
        _ScriptedHost(version_stdout="fake-cursor\n"),
    ):
        assert await host_actions._cursor_update_target(
            h, "/cache/cursor-agent", cache_dir="/cache",
        ) is None
        assert len(h.commands) == 1  # --version only, no curl
        assert "--version" in h.commands[0]


@pytest.mark.asyncio
async def test_cursor_update_target_best_effort_on_upstream_failure():
    """curl failure (offline) or a script with no version token → None: the
    probe must never block a launch."""
    for h in (
        _ScriptedHost(curl_exit=22, curl_stdout=""),
        _ScriptedHost(curl_stdout="<html>maintenance</html>"),
    ):
        assert await host_actions._cursor_update_target(
            h, "/cache/cursor-agent", cache_dir="/cache",
        ) is None


@pytest.mark.asyncio
async def test_cache_hit_stale_refreshes_via_vendor_installer(
    tmp_path: pathlib.Path, monkeypatch,
):
    """A cache HIT whose binary is behind upstream is refreshed via the
    EXISTING vendor staging install BEFORE it is linked into the task."""
    cache = tmp_path / "cache"
    cache.mkdir()
    _write_exe(cache / "cursor-agent")

    async def _stale(host, cached, *, cache_dir):  # noqa: ANN001
        return "2026.08.11-e8db854"

    refreshed: dict[str, str] = {}

    async def _fake_vendor(host, cache_dir):  # noqa: ANN001
        refreshed["cache_dir"] = cache_dir
        _write_exe(cache / "cursor-agent", body="#!/bin/bash\necho fresh\n")
        return f"{cache_dir.rstrip('/')}/cursor-agent"

    async def _no_seed(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not seed from host install on a cache hit")

    monkeypatch.setattr(host_actions, "_cursor_update_target", _stale)
    monkeypatch.setattr(host_actions, "_vendor_install_cursor", _fake_vendor)
    monkeypatch.setattr(host_actions, "resolve_cursor", _no_seed)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_cursor_installed(ctx, install_dir=str(cache))
    assert refreshed["cache_dir"] == str(cache)
    assert result == _task_path(ctx)
    assert os.path.realpath(result) == str((cache / "cursor-agent").resolve())


@pytest.mark.asyncio
async def test_cache_hit_refresh_failure_still_links_cached(
    tmp_path: pathlib.Path, monkeypatch,
):
    """A stale HIT whose refresh fails (network flake mid-install) still links
    the existing cached binary — a failed refresh never blocks the launch."""
    cache = tmp_path / "cache"
    cache.mkdir()
    _write_exe(cache / "cursor-agent")

    async def _stale(host, cached, *, cache_dir):  # noqa: ANN001
        return "2026.08.11-e8db854"

    async def _vendor_fails(host, cache_dir):  # noqa: ANN001
        return None

    monkeypatch.setattr(host_actions, "_cursor_update_target", _stale)
    monkeypatch.setattr(host_actions, "_vendor_install_cursor", _vendor_fails)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_cursor_installed(ctx, install_dir=str(cache))
    assert result == _task_path(ctx)
    assert os.path.realpath(result) == str((cache / "cursor-agent").resolve())


@pytest.mark.asyncio
async def test_cache_hit_current_does_not_refresh(
    tmp_path: pathlib.Path, monkeypatch,
):
    cache = tmp_path / "cache"
    cache.mkdir()
    _write_exe(cache / "cursor-agent")

    async def _current(host, cached, *, cache_dir):  # noqa: ANN001
        return None

    monkeypatch.setattr(host_actions, "_cursor_update_target", _current)
    _forbid_vendor_installer(monkeypatch)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_cursor_installed(ctx, install_dir=str(cache))
    assert result == _task_path(ctx)


@pytest.mark.asyncio
async def test_cache_hit_skips_update_probe_when_check_update_false(
    tmp_path: pathlib.Path, monkeypatch,
):
    """check_update=False (the resume re-link call) re-links the cache WITHOUT
    the network update probe: the earlier ensure_cursor_installed on the same
    resume already validated/refreshed the cache."""
    cache = tmp_path / "cache"
    cache.mkdir()
    _write_exe(cache / "cursor-agent")

    async def _no_probe(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("update probe must be skipped when check_update=False")

    monkeypatch.setattr(host_actions, "_cursor_update_target", _no_probe)
    _forbid_vendor_installer(monkeypatch)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_cursor_installed(
        ctx, install_dir=str(cache), check_update=False,
    )
    assert result == _task_path(ctx)


@pytest.mark.asyncio
async def test_cache_hit_stale_not_checked_when_install_disabled(
    tmp_path: pathlib.Path, monkeypatch,
):
    """install_if_missing=False: a HIT links the existing cache and never
    triggers a network update-check or install (pinned/offline worker)."""
    cache = tmp_path / "cache"
    cache.mkdir()
    _write_exe(cache / "cursor-agent")

    async def _no_check(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("update-check must be skipped when installs are off")

    monkeypatch.setattr(host_actions, "_cursor_update_target", _no_check)
    _forbid_vendor_installer(monkeypatch)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_cursor_installed(
        ctx, install_dir=str(cache), install_if_missing=False,
    )
    assert result == _task_path(ctx)
