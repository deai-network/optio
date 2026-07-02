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

import asyncio
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


def _fake_release_tarball(member_name: str = "codex-x86_64-unknown-linux-musl") -> bytes:
    """A codex release tar.gz: a single static-binary member."""
    import io
    import tarfile

    body = b"#!/bin/bash\necho downloaded-codex\n"
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=member_name)
        info.size = len(body)
        info.mode = 0o644          # releases ship without +x; install chmods
        tar.addfile(info, io.BytesIO(body))
    return out.getvalue()


class _DownloadingHookCtx(_FakeHookCtx):
    """Fake hook_ctx whose download_file writes a prepared release tarball."""

    def __init__(self, host, payload: bytes) -> None:
        super().__init__(host)
        self.payload = payload
        self.urls: list[str] = []

    async def download_file(self, url: str, dest: str) -> None:
        self.urls.append(url)
        with open(dest, "wb") as fh:
            fh.write(self.payload)


@pytest.mark.asyncio
async def test_cache_miss_no_host_codex_downloads_release(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()

    async def _resolve(host, *, install_dir=None, install_if_missing=True):  # noqa: ANN001
        raise RuntimeError("codex not found on the worker")

    monkeypatch.setattr(host_actions, "resolve_codex", _resolve)
    host = LocalHost(taskdir=str(tmp_path / "task"))
    await host.setup_workdir()
    ctx = _DownloadingHookCtx(host, _fake_release_tarball())

    result = await host_actions.ensure_codex_installed(ctx, install_dir=str(cache))

    # The pinned release URL for this machine's arch was requested…
    assert len(ctx.urls) == 1
    url = ctx.urls[0]
    assert host_actions._CODEX_VERSION in url
    assert url.startswith(
        "https://github.com/openai/codex/releases/download/rust-v"
    )
    assert url.endswith("-unknown-linux-musl.tar.gz")
    # …the single tarball member landed as <cache>/codex, executable…
    assert (cache / "codex").is_file()
    assert os.access(cache / "codex", os.X_OK)
    assert (cache / "codex").read_bytes().startswith(b"#!/bin/bash")
    # …behind the per-task launch symlink, and no temp litter remains.
    assert result == _per_task_path(ctx)
    assert os.path.realpath(result) == os.path.realpath(str(cache / "codex"))
    leftovers = [p.name for p in cache.iterdir() if p.name != "codex"]
    assert leftovers == [], leftovers


class _RecordingDownloadCtx(_DownloadingHookCtx):
    """Records every ``dest`` path passed to download_file."""

    def __init__(self, host, payload: bytes) -> None:
        super().__init__(host, payload)
        self.dests: list[str] = []

    async def download_file(self, url: str, dest: str) -> None:
        self.dests.append(dest)
        await super().download_file(url, dest)


@pytest.mark.asyncio
async def test_download_uses_unique_tarball_path_per_invocation(
    tmp_path, monkeypatch,
):
    """Each cold-cache download writes to a PER-INVOCATION tarball path in the
    SAME cache dir, not a single fixed one — so concurrent fleet spin-up
    tasks never interleave writes into / cross-delete the same in-flight
    tarball. Drives ``_download_codex_into_cache`` directly (twice into one
    cache) so both invocations genuinely fill; ``ensure_codex_installed``
    would short-circuit the second on a cache hit."""
    cache = tmp_path / "cache"
    cache.mkdir()
    cached = str(cache / "codex")
    payload = _fake_release_tarball()

    host = LocalHost(taskdir=str(tmp_path / "task"))
    await host.setup_workdir()
    ctx = _RecordingDownloadCtx(host, payload)

    await host_actions._download_codex_into_cache(
        ctx, cache_dir=str(cache), cached=cached,
    )
    await host_actions._download_codex_into_cache(
        ctx, cache_dir=str(cache), cached=cached,
    )
    assert len(ctx.dests) == 2
    assert ctx.dests[0] != ctx.dests[1], ctx.dests


@pytest.mark.asyncio
async def test_concurrent_cold_cache_downloads_do_not_corrupt(
    tmp_path, monkeypatch,
):
    """N tasks hitting an empty cache simultaneously each download + extract
    through private per-invocation scratch/tarball paths; none wipes the
    other's tree or deletes its in-flight files, ``<cache>/codex`` ends up a
    complete executable binary, and no temp litter is left behind."""
    async def _resolve(host, *, install_dir=None, install_if_missing=True):  # noqa: ANN001
        raise RuntimeError("codex not found on the worker")

    monkeypatch.setattr(host_actions, "resolve_codex", _resolve)

    cache = tmp_path / "cache"
    cache.mkdir()
    payload = _fake_release_tarball()

    async def _one(i: int) -> str:
        host = LocalHost(taskdir=str(tmp_path / f"task{i}"))
        await host.setup_workdir()
        ctx = _DownloadingHookCtx(host, payload)
        return await host_actions.ensure_codex_installed(ctx, install_dir=str(cache))

    results = await asyncio.gather(*[_one(i) for i in range(4)])

    assert all(r.endswith("/home/.local/bin/codex") for r in results)
    assert (cache / "codex").is_file()
    assert os.access(cache / "codex", os.X_OK)
    assert (cache / "codex").read_bytes().startswith(b"#!/bin/bash")
    leftovers = [p.name for p in cache.iterdir() if p.name != "codex"]
    assert leftovers == [], leftovers


class _UnameResult:
    def __init__(self, stdout: str, exit_code: int) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.exit_code = exit_code


class _UnameHost:
    """Fake Host answering only the two uname probes."""

    def __init__(self, os_name: str, arch: str) -> None:
        self._answers = {"uname -s": os_name, "uname -m": arch}

    async def run_command(self, cmd, **kwargs):
        if cmd in self._answers:
            return _UnameResult(self._answers[cmd], 0)
        return _UnameResult("", 1)


@pytest.mark.asyncio
async def test_detect_codex_asset_name_arch_map():
    assert await host_actions._detect_codex_asset_name(
        _UnameHost("Linux", "x86_64")
    ) == "codex-x86_64-unknown-linux-musl.tar.gz"
    assert await host_actions._detect_codex_asset_name(
        _UnameHost("Linux", "aarch64")
    ) == "codex-aarch64-unknown-linux-musl.tar.gz"
    with pytest.raises(RuntimeError, match="arch"):
        await host_actions._detect_codex_asset_name(_UnameHost("Linux", "armv7l"))
    with pytest.raises(RuntimeError, match="OS"):
        await host_actions._detect_codex_asset_name(_UnameHost("Darwin", "x86_64"))


@pytest.mark.asyncio
async def test_download_multi_member_tarball_rejected(tmp_path, monkeypatch):
    """A tarball that does not contain exactly one file is refused — never
    guess which member is the binary."""
    import io
    import tarfile

    async def _resolve(host, *, install_dir=None, install_if_missing=True):  # noqa: ANN001
        raise RuntimeError("codex not found on the worker")

    monkeypatch.setattr(host_actions, "resolve_codex", _resolve)

    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name in ("codex-x86_64-unknown-linux-musl", "README.md"):
            info = tarfile.TarInfo(name=name)
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))

    cache = tmp_path / "cache"
    cache.mkdir()
    host = LocalHost(taskdir=str(tmp_path / "task"))
    await host.setup_workdir()
    ctx = _DownloadingHookCtx(host, out.getvalue())

    with pytest.raises(RuntimeError, match="exactly one"):
        await host_actions.ensure_codex_installed(ctx, install_dir=str(cache))
