"""optio-owned, evictable kimi binary cache + smart-install provisioning.

``ensure_kimicode_installed`` provisions the FORK ``kimi`` for a task through a
cache dir that lives OUTSIDE the task workdir (so workdir teardown never destroys
the install) and never the operator's ``~/.kimi-code``, returning a per-task
launch symlink into that cache (``<workdir>/home/.local/bin/kimi``).

Staleness/correctness is delegated to the fork's ``smart-install.sh --check``
(``_smart_install_check`` → ``("ok", None)`` / ``("download", url)``). On
``download`` the release zip is fetched with optio's ``download`` callback and
the ``kimi`` binary (at the zip ROOT) is unpacked into ``<cache>/kimi``. These
tests drive a bare ``LocalHost`` (no hook_ctx) and mock the resolver + the
download callback, so no network is touched; a gated opt-in test at the bottom
exercises the live fork release.
"""

from __future__ import annotations

import os
import pathlib
import socket
import zipfile
from types import SimpleNamespace

import pytest
from optio_host.host import LocalHost

from optio_kimicode import host_actions

# Captured at import, before the autouse ``_stub_smart_install`` fixture patches
# the module attr — so the parser test can exercise the REAL implementation.
_REAL_SMART_INSTALL_CHECK = host_actions._smart_install_check


def _write_exe(
    path: pathlib.Path, body: str = "#!/bin/bash\necho fake-kimi\n",
) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


def _make_kimi_zip(path: pathlib.Path, body: str = "#!/bin/bash\necho fork-kimi\n") -> pathlib.Path:
    """A release-shaped zip: a single executable ``kimi`` entry at the ROOT
    (kimi-code archives carry the binary directly, unlike opencode's bin/)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        info = zipfile.ZipInfo("kimi")
        info.external_attr = 0o755 << 16
        z.writestr(info, body)
    return path


async def _local_host(tmp_path: pathlib.Path) -> LocalHost:
    host = LocalHost(taskdir=str(tmp_path / "task"))
    await host.setup_workdir()
    return host


def _task_path(host: LocalHost) -> str:
    return f"{host.workdir.rstrip('/')}/home/.local/bin/kimi"


@pytest.mark.asyncio
async def test_ok_relinks_without_download(tmp_path: pathlib.Path, monkeypatch):
    """smart-install says ``kimi ok`` and the cache already holds the binary →
    only the task symlink is (re)made; ``download`` is never called. Calling twice
    (the resume relink) is idempotent."""
    cache = tmp_path / "cache"
    _write_exe(cache / "kimi")

    async def _check_ok(host, *, cache_dir):  # noqa: ANN001
        return ("ok", None)

    async def _no_download(url, dest):  # noqa: ANN001
        raise AssertionError("an 'ok' result must not download")

    monkeypatch.setattr(host_actions, "_smart_install_check", _check_ok)
    host = await _local_host(tmp_path)

    before_ino = (cache / "kimi").stat().st_ino
    result = await host_actions.ensure_kimicode_installed(
        host, install_dir=str(cache), download=_no_download,
    )
    assert result == _task_path(host)
    assert os.path.islink(result)
    assert os.path.realpath(result) == str((cache / "kimi").resolve())
    # Untouched cache binary — only the symlink was made.
    assert (cache / "kimi").stat().st_ino == before_ino

    result2 = await host_actions.ensure_kimicode_installed(
        host, install_dir=str(cache), download=_no_download,
    )
    assert result2 == result


@pytest.mark.asyncio
async def test_ok_but_empty_cache_seeds_from_path(tmp_path: pathlib.Path, monkeypatch):
    """``kimi ok`` but ``<cache>/kimi`` absent (a fork kimi is on PATH elsewhere)
    → the cache is seeded from it before linking."""
    cache = tmp_path / "cache"
    cache.mkdir()  # empty

    async def _check_ok(host, *, cache_dir):  # noqa: ANN001
        return ("ok", None)

    seeded: dict[str, str] = {}

    async def _fake_seed(host, *, cache_dir, cached):  # noqa: ANN001
        seeded["cached"] = cached
        _write_exe(pathlib.Path(cached))

    monkeypatch.setattr(host_actions, "_smart_install_check", _check_ok)
    monkeypatch.setattr(host_actions, "_seed_cache_from_path", _fake_seed)
    host = await _local_host(tmp_path)

    result = await host_actions.ensure_kimicode_installed(host, install_dir=str(cache))
    assert seeded["cached"] == str(cache / "kimi")
    assert os.path.realpath(result) == str((cache / "kimi").resolve())


@pytest.mark.asyncio
async def test_download_installs_fork_zip(tmp_path: pathlib.Path, monkeypatch):
    """``download <url>`` → optio's ``download`` fetches the zip, and the ``kimi``
    entry (zip root) is unpacked into ``<cache>/kimi`` and linked into the task."""
    cache = tmp_path / "cache"
    cache.mkdir()  # empty → miss
    zip_src = _make_kimi_zip(tmp_path / "release" / "kimi-code-linux-x64.zip")

    async def _check_download(host, *, cache_dir):  # noqa: ANN001
        return ("download", "https://example.invalid/kimi-code-linux-x64.zip")

    downloaded: dict[str, str] = {}

    async def _fake_download(url, dest):  # noqa: ANN001
        # Emulate optio's downloader landing the release zip at ``dest``.
        downloaded["url"] = url
        pathlib.Path(dest).write_bytes(zip_src.read_bytes())

    monkeypatch.setattr(host_actions, "_smart_install_check", _check_download)
    host = await _local_host(tmp_path)

    result = await host_actions.ensure_kimicode_installed(
        host, install_dir=str(cache), download=_fake_download,
    )
    assert downloaded["url"].endswith("kimi-code-linux-x64.zip")
    assert result == _task_path(host)
    assert (cache / "kimi").is_file()
    assert os.access(cache / "kimi", os.X_OK)
    assert os.path.realpath(result) == str((cache / "kimi").resolve())


@pytest.mark.asyncio
async def test_install_if_missing_false_raises(tmp_path: pathlib.Path, monkeypatch):
    """``download`` needed + ``install_if_missing=False`` → a clear error, no
    download."""
    cache = tmp_path / "cache"
    cache.mkdir()

    async def _check_download(host, *, cache_dir):  # noqa: ANN001
        return ("download", "https://example.invalid/kimi-code-linux-x64.zip")

    async def _boom_download(url, dest):  # noqa: ANN001
        raise AssertionError("must not download when install_if_missing=False")

    monkeypatch.setattr(host_actions, "_smart_install_check", _check_download)
    host = await _local_host(tmp_path)

    with pytest.raises(RuntimeError, match="install_if_missing=False"):
        await host_actions.ensure_kimicode_installed(
            host, install_dir=str(cache), install_if_missing=False,
            download=_boom_download,
        )


@pytest.mark.asyncio
async def test_default_cache_dir_is_outside_workdir(tmp_path: pathlib.Path, monkeypatch):
    """With no override, ``OPTIO_KIMICODE_CACHE_DIR`` (worker real env) decides the
    cache dir — never under the workdir, so the resume snapshot
    (``cd host.workdir && tar``) can never capture it."""
    cache = tmp_path / "kimi-cache" / "bin"
    _write_exe(cache / "kimi")
    monkeypatch.setenv("OPTIO_KIMICODE_CACHE_DIR", str(cache))

    async def _check_ok(host, *, cache_dir):  # noqa: ANN001
        assert cache_dir == str(cache)  # resolved from the env, not the workdir
        return ("ok", None)

    monkeypatch.setattr(host_actions, "_smart_install_check", _check_ok)
    host = await _local_host(tmp_path)

    result = await host_actions.ensure_kimicode_installed(host)  # no install_dir
    real = os.path.realpath(result)
    assert real == str((cache / "kimi").resolve())
    assert not real.startswith(host.workdir.rstrip("/"))


@pytest.mark.asyncio
async def test_smart_install_check_parses_contract():
    """``_smart_install_check`` maps the one-line contract to (kind, url) and
    rejects garbage."""
    def _host(stdout: str, exit_code: int = 0):
        class _H:
            async def run_command(self, cmd: str):  # noqa: ANN001
                return SimpleNamespace(exit_code=exit_code, stdout=stdout, stderr="")
        return _H()

    assert await _REAL_SMART_INSTALL_CHECK(_host("kimi ok\n"), cache_dir="/c") == ("ok", None)
    kind, url = await _REAL_SMART_INSTALL_CHECK(
        _host("download https://x/kimi-code-linux-x64.zip\n"), cache_dir="/c",
    )
    assert kind == "download" and url == "https://x/kimi-code-linux-x64.zip"
    with pytest.raises(RuntimeError, match="unexpected output"):
        await _REAL_SMART_INSTALL_CHECK(_host("weird\n"), cache_dir="/c")
    with pytest.raises(RuntimeError, match="--check failed"):
        await _REAL_SMART_INSTALL_CHECK(_host("", exit_code=7), cache_dir="/c")


def _online() -> bool:
    try:
        socket.create_connection(("raw.githubusercontent.com", 443), timeout=3).close()
        return True
    except OSError:
        return False


# TRACKED REAL-BINARY FOLLOW-UP. The mocked tests above prove the ORCHESTRATION
# (resolve → download → unpack → link) without the network. This opt-in test
# proves the LIVE fork smart-install + release actually lands a runnable kimi
# whose version carries the fork suffix — it downloads a real release, so it is
# gated behind both an explicit opt-in env var AND connectivity.
@pytest.mark.skipif(
    not (os.environ.get("OPTIO_KIMICODE_REAL_INSTALL") and _online()),
    reason="real fork install (network + heavy download): opt-in via OPTIO_KIMICODE_REAL_INSTALL=1",
)
@pytest.mark.asyncio
async def test_real_fork_install_lands_runnable_kimi(tmp_path: pathlib.Path):
    cache = tmp_path / "real-cache" / "bin"
    cache.mkdir(parents=True)
    host = await _local_host(tmp_path)

    result = await host_actions.ensure_kimicode_installed(host, install_dir=str(cache))
    assert os.access(result, os.X_OK)
    assert (cache / "kimi").is_file()
    probe = await host.run_command(f"{result} --version")
    assert probe.exit_code == 0
    assert "csillag" in (probe.stdout or "")  # the fork version suffix
