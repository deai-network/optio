"""Stage 5: optio-owned, evictable ``agy`` binary cache + real two-tier install.

``ensure_antigravity_installed`` resolves ``agy`` through a cache dir that lives
OUTSIDE the task workdir and never the operator's autoupdating ``~/.gemini``, and
returns a per-task launch symlink into that cache
(``<workdir>/home/.local/bin/agy``):

* cache HIT — ``<cache>/agy`` already executable AND functionally an ``agy``
  (``_is_agy``) → linked into the task path, no seed/install.
* cache MISS, host ``agy`` present — the host ``agy`` is copied (deref) into the
  cache (Tier-1, fast), then linked.
* cache MISS, no host ``agy`` — Tier-2: fetch the platform manifest from the
  updater, download the tarball, SHA512-verify it, extract the ``antigravity``
  binary into the cache as ``agy``, then link.
* poisoned cache — an executable at ``<cache>/agy`` that is NOT an ``agy`` is
  invalidated and repopulated (functional identity gate).
* ``install_if_missing=False`` on a miss — a clear error (nothing to do).
* default location — ``ANTIGRAVITY_CACHE_DIR`` / ``${XDG_CACHE_HOME:-$HOME/.cache}``,
  resolved against the worker's real env; never under the workdir (so the resume
  snapshot never captures the binary).

Plus the launch environment: ``build_launch_env`` (HOME/XDG isolation + PATH) and
``disable_agy_self_update`` (best-effort ``AutoUpdate:false`` settings key —
TODO(S2): reconcile with the self-update-disable spike).
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess

import pytest
from optio_host.host import LocalHost

from optio_antigravity import host_actions


# An identity script that answers ``--help`` the way the real ``agy`` does (its
# banner names the tool), so the functional ``_is_agy`` gate accepts it. A plain
# ``echo`` script (no agy marker) models a poisoned / wrong binary.
_AGY_IDENTITY_BODY = (
    "#!/bin/bash\n"
    'if [ "$1" = "--help" ]; then\n'
    '  echo "agy — Antigravity CLI"\n'
    '  echo "usage: agy [--print] [PROMPT]"\n'
    "  exit 0\n"
    "fi\n"
    'echo "agy running"\n'
)

_NOT_AGY_BODY = "#!/bin/bash\necho some-other-tool\n"


class _FakeHookCtx:
    """Minimal hook_ctx: a real LocalHost, a no-op progress reporter, and a
    ``download_file`` that copies from a local url→path map (no network)."""

    def __init__(self, host: LocalHost, downloads: dict[str, str] | None = None) -> None:
        self._host = host
        self._downloads = downloads or {}

    def report_progress(self, percent, message=None) -> None:  # noqa: ANN001
        pass

    async def download_file(self, url: str, dest: str) -> None:
        # Route by extension: the manifest is the only ``.json`` fetch; anything
        # else is the tarball. Values are local fixture paths we copy verbatim.
        key = "manifest" if url.endswith(".json") else "tarball"
        src = self._downloads[key]
        with open(src, "rb") as fh:
            data = fh.read()
        with open(dest, "wb") as out:
            out.write(data)


def _write_exe(path: pathlib.Path, body: str = _AGY_IDENTITY_BODY) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


async def _local_ctx(
    tmp_path: pathlib.Path, downloads: dict[str, str] | None = None,
) -> _FakeHookCtx:
    host = LocalHost(taskdir=str(tmp_path / "task"))
    await host.setup_workdir()
    return _FakeHookCtx(host, downloads)


def _task_path(ctx: _FakeHookCtx) -> str:
    return f"{ctx._host.workdir.rstrip('/')}/home/.local/bin/agy"


# --- cache resolution / linking --------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_links_into_task_path(tmp_path: pathlib.Path, monkeypatch):
    cache = tmp_path / "cache"
    _write_exe(cache / "agy")

    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not seed/install on a cache hit")

    monkeypatch.setattr(host_actions, "_populate_antigravity_cache", _boom)
    monkeypatch.setattr(host_actions, "_install_antigravity_into_cache", _boom)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_antigravity_installed(ctx, install_dir=str(cache))
    assert result == _task_path(ctx)
    assert os.path.islink(result)
    assert os.path.realpath(result) == str((cache / "agy").resolve())
    assert os.access(result, os.X_OK)


@pytest.mark.asyncio
async def test_cache_miss_seeds_from_host_agy(tmp_path: pathlib.Path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()  # empty → miss
    source = _write_exe(tmp_path / "hostbin" / "agy")

    async def _resolve(host, *, install_dir=None, install_if_missing=True):  # noqa: ANN001
        return str(source)

    async def _no_install(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must seed from host agy, not Tier-2 install")

    monkeypatch.setattr(host_actions, "resolve_agy", _resolve)
    monkeypatch.setattr(host_actions, "_install_antigravity_into_cache", _no_install)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_antigravity_installed(ctx, install_dir=str(cache))
    assert result == _task_path(ctx)
    # Cache holds a real, dereferenced copy (not a symlink back to the host bin).
    assert (cache / "agy").is_file()
    assert not (cache / "agy").is_symlink()
    assert os.access(cache / "agy", os.X_OK)
    assert os.path.islink(result)
    assert os.path.realpath(result) == str((cache / "agy").resolve())


@pytest.mark.asyncio
async def test_cache_miss_no_host_agy_tier2_installs(tmp_path: pathlib.Path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()

    async def _resolve(host, *, install_dir=None, install_if_missing=True):  # noqa: ANN001
        raise RuntimeError("agy not found on the worker")

    called = {}

    async def _fake_install(hook_ctx, host, *, cache_dir, cached):  # noqa: ANN001
        called["cache_dir"] = cache_dir
        called["cached"] = cached
        _write_exe(pathlib.Path(cached))  # emulate a successful Tier-2 install

    monkeypatch.setattr(host_actions, "resolve_agy", _resolve)
    monkeypatch.setattr(host_actions, "_install_antigravity_into_cache", _fake_install)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_antigravity_installed(ctx, install_dir=str(cache))
    assert called["cache_dir"] == str(cache)
    assert called["cached"] == str(cache / "agy")
    assert result == _task_path(ctx)
    assert os.path.islink(result)
    assert os.path.realpath(result) == str((cache / "agy").resolve())


@pytest.mark.asyncio
async def test_poisoned_cache_invalidated_and_repopulated(tmp_path: pathlib.Path, monkeypatch):
    """An executable at ``<cache>/agy`` that fails the functional identity gate
    is a poisoned cache: it is invalidated and repopulated (never adopted)."""
    cache = tmp_path / "cache"
    _write_exe(cache / "agy", body=_NOT_AGY_BODY)  # exists + executable, but NOT agy

    called = {}

    async def _repopulate(hook_ctx, host, *, cache_dir, cached):  # noqa: ANN001
        called["hit"] = True
        _write_exe(pathlib.Path(cached))  # a real agy replaces the poison

    monkeypatch.setattr(host_actions, "_populate_antigravity_cache", _repopulate)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_antigravity_installed(ctx, install_dir=str(cache))
    assert called.get("hit") is True
    assert await host_actions._is_agy(ctx._host, os.path.realpath(result))


@pytest.mark.asyncio
async def test_no_install_raises_on_miss(tmp_path: pathlib.Path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()

    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not seed/install when install_if_missing=False")

    monkeypatch.setattr(host_actions, "_populate_antigravity_cache", _boom)
    ctx = await _local_ctx(tmp_path)

    with pytest.raises(RuntimeError, match="install_if_missing=False"):
        await host_actions.ensure_antigravity_installed(
            ctx, install_dir=str(cache), install_if_missing=False,
        )


@pytest.mark.asyncio
async def test_default_cache_dir_from_env_outside_workdir(tmp_path: pathlib.Path, monkeypatch):
    """With no override, ANTIGRAVITY_CACHE_DIR (worker real env) decides the cache
    dir — never the workdir, never the operator's ~/.gemini. The returned task
    path is a symlink whose real target (the binary) is OUTSIDE the workdir, so
    the resume snapshot never captures it."""
    cache = tmp_path / "gem-cache" / "bin"
    _write_exe(cache / "agy")
    monkeypatch.setenv("ANTIGRAVITY_CACHE_DIR", str(cache))
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_antigravity_installed(ctx)  # no install_dir
    assert result == _task_path(ctx)
    # The cached binary (symlink target) is NOT under the task workdir.
    assert not os.path.realpath(result).startswith(ctx._host.workdir)
    assert os.path.realpath(result) == str((cache / "agy").resolve())


# --- Tier-2 real two-tier install (manifest + tarball + SHA512) -------------


def _build_tarball_fixture(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, str]:
    """Build a ``antigravity.tar.gz`` carrying an ``antigravity`` identity binary
    plus a matching manifest.json. Returns (manifest_path, tarball_path, sha512)."""
    staging = tmp_path / "staging"
    staging.mkdir()
    _write_exe(staging / "antigravity")
    tarball = tmp_path / "antigravity.tar.gz"
    subprocess.run(
        ["tar", "-czf", str(tarball), "-C", str(staging), "antigravity"],
        check=True,
    )
    sha = hashlib.sha512(tarball.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": "1.0.16",
        "url": "http://fake-updater.invalid/antigravity.tar.gz",
        "sha512": sha,
    }))
    return manifest, tarball, sha


@pytest.mark.asyncio
async def test_tier2_install_verifies_sha512_and_extracts_binary(tmp_path: pathlib.Path):
    manifest, tarball, _sha = _build_tarball_fixture(tmp_path)
    cache = tmp_path / "cache"
    cached = str(cache / "agy")
    ctx = await _local_ctx(
        tmp_path, downloads={"manifest": str(manifest), "tarball": str(tarball)},
    )

    await host_actions._install_antigravity_into_cache(
        ctx, ctx._host, cache_dir=str(cache), cached=cached,
    )

    assert os.path.isfile(cached)
    assert os.access(cached, os.X_OK)
    # The extracted binary is a functional agy (identity gate passes).
    assert await host_actions._is_agy(ctx._host, cached)


@pytest.mark.asyncio
async def test_tier2_install_rejects_sha512_mismatch(tmp_path: pathlib.Path):
    manifest, tarball, _sha = _build_tarball_fixture(tmp_path)
    # Tamper the manifest checksum → the download must be rejected (no install).
    doc = json.loads(manifest.read_text())
    doc["sha512"] = "0" * 128
    manifest.write_text(json.dumps(doc))
    cache = tmp_path / "cache"
    cached = str(cache / "agy")
    ctx = await _local_ctx(
        tmp_path, downloads={"manifest": str(manifest), "tarball": str(tarball)},
    )

    with pytest.raises(RuntimeError, match="(?i)sha512"):
        await host_actions._install_antigravity_into_cache(
            ctx, ctx._host, cache_dir=str(cache), cached=cached,
        )
    assert not os.path.exists(cached)


# --- launch environment: isolation + self-update off ------------------------


def test_build_launch_env_isolation_and_path():
    env = host_actions.build_launch_env("/w/task")
    for k, v in host_actions._isolation_env("/w/task").items():
        assert env[k] == v
    # PATH prepends the per-task home/.local/bin ahead of the base PATH.
    assert env["PATH"].startswith("/w/task/home/.local/bin:")


def test_build_launch_env_extra_env_overrides_and_path_base():
    env = host_actions.build_launch_env(
        "/w/task", {"PATH": "/custom/bin", "FOO": "bar"},
    )
    assert env["FOO"] == "bar"
    assert env["PATH"] == "/w/task/home/.local/bin:/custom/bin"


@pytest.mark.asyncio
async def test_disable_agy_self_update_writes_settings_key(tmp_path: pathlib.Path):
    """Best-effort self-update disable: ``AutoUpdate:false`` is set in the task's
    isolated ``settings.json`` as a parsed-JSON mutation that PRESERVES existing
    keys (never a blind append). TODO(S2): reconcile with the real spike."""
    host = LocalHost(taskdir=str(tmp_path / "task"))
    await host.setup_workdir()
    workdir = host.workdir.rstrip("/")
    settings = pathlib.Path(workdir) / "home" / ".gemini" / "antigravity-cli" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"model": "gemini-2.5-pro"}))

    await host_actions.disable_agy_self_update(host, workdir)

    doc = json.loads(settings.read_text())
    assert doc["AutoUpdate"] is False
    assert doc["model"] == "gemini-2.5-pro"  # pre-existing key preserved


@pytest.mark.asyncio
async def test_disable_agy_self_update_creates_settings_when_absent(tmp_path: pathlib.Path):
    host = LocalHost(taskdir=str(tmp_path / "task"))
    await host.setup_workdir()
    workdir = host.workdir.rstrip("/")

    await host_actions.disable_agy_self_update(host, workdir)

    settings = pathlib.Path(workdir) / "home" / ".gemini" / "antigravity-cli" / "settings.json"
    assert json.loads(settings.read_text())["AutoUpdate"] is False


# --- Regression: manifest platform slug must match the real updater host ------
# The auto-updater serves `linux_amd64.json` (underscore, musl-aware), NOT the
# Go `linux-amd64` (hyphen). A wrong slug 404s the manifest fetch — a bug the
# fake-download harness could not catch (it routes by extension, not URL).
def test_platform_slug_matches_install_sh():
    slug = host_actions._platform_slug
    assert slug("Linux", "x86_64", is_musl=False) == "linux_amd64"
    assert slug("Linux", "amd64", is_musl=False) == "linux_amd64"
    assert slug("Linux", "aarch64", is_musl=False) == "linux_arm64"
    assert slug("Linux", "x86_64", is_musl=True) == "linux_amd64_musl"
    assert slug("Linux", "aarch64", is_musl=True) == "linux_arm64_musl"
    # non-linux never gets a musl suffix
    assert slug("Darwin", "arm64", is_musl=False) == "darwin_arm64"
    assert slug("Darwin", "x86_64", is_musl=False) == "darwin_amd64"
