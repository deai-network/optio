"""Stage 5: optio-owned, evictable ``agy`` binary cache + real two-tier install.

``ensure_antigravity_installed`` resolves ``agy`` through a cache dir that lives
OUTSIDE the task workdir and never the operator's autoupdating ``~/.gemini``, and
returns a per-task launch symlink into that cache
(``<workdir>/home/.local/bin/agy``):

* cache HIT — ``<cache>/agy`` already executable AND functionally an ``agy``
  (``_is_agy``) → freshness-checked against the updater manifest
  (``_antigravity_update_target``; best-effort, gated on ``install_if_missing``
  and ``check_update``) and Tier-2-refreshed when the manifest is newer, then
  linked into the task path.
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
from types import SimpleNamespace

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


# --- cache-HIT staleness refresh (manifest-version-gated) -------------------


# The REAL probe output (captured 2026-08-13 against
# ~/.cache/optio-antigravity/bin/agy): ``agy --version`` prints exactly the
# bare dotted semver on stdout, exit 0. (``agy version`` is a TUI subcommand —
# bubbletea, needs /dev/tty — and is NOT usable for the probe.)
_REAL_AGY_VERSION_OUTPUT = "1.0.16\n"

# The REAL manifest shape (captured 2026-08-13 from
# .../manifests/linux_amd64.json on the auto-updater host).
_REAL_MANIFEST = {
    "version": "1.1.12",
    "url": (
        "https://storage.googleapis.com/antigravity-public/antigravity-cli/"
        "1.1.12-5877618327814144/linux-x64/cli_linux_x64.tar.gz"
    ),
    "sha512": "c1ee7b8a" + "0" * 120,
}


def test_parse_agy_version_real_output_shape():
    parse = host_actions._parse_agy_version
    assert parse(_REAL_AGY_VERSION_OUTPUT) == "1.0.16"
    # Defensive: a future build wrapping the number in a banner still parses.
    assert parse("agy version 2.3.4 (linux/amd64)") == "2.3.4"
    # No dotted-number token → None (e.g. the fake identity script's output).
    assert parse("agy running") is None
    assert parse("") is None


class _ScriptedHost:
    """Fake host for ``_antigravity_update_target``: dispatches run_command by
    command content (version probe → uname → musl → curl) and records the
    commands so tests can assert the manifest URL that was fetched."""

    def __init__(self, *, version_out: str, curl_exit: int = 0, curl_out: str = ""):
        self._version_out = version_out
        self._curl_exit = curl_exit
        self._curl_out = curl_out
        self.commands: list[str] = []

    async def run_command(self, cmd: str):
        self.commands.append(cmd)
        if "--version" in cmd:
            return SimpleNamespace(exit_code=0, stdout=self._version_out, stderr="")
        if "uname -s" in cmd:
            return SimpleNamespace(exit_code=0, stdout="Linux\n", stderr="")
        if "uname -m" in cmd:
            return SimpleNamespace(exit_code=0, stdout="x86_64\n", stderr="")
        if "musl" in cmd:
            return SimpleNamespace(exit_code=1, stdout="", stderr="")  # glibc
        if cmd.startswith("curl"):
            return SimpleNamespace(
                exit_code=self._curl_exit, stdout=self._curl_out, stderr="down",
            )
        raise AssertionError(f"unexpected command: {cmd!r}")


@pytest.mark.asyncio
async def test_antigravity_update_target_returns_manifest_version_when_newer():
    """Cached 1.0.16 vs manifest 1.1.12 (both the REAL captured shapes) →
    returns the manifest version, fetched from the underscore-slug URL."""
    h = _ScriptedHost(
        version_out=_REAL_AGY_VERSION_OUTPUT, curl_out=json.dumps(_REAL_MANIFEST),
    )
    target = await host_actions._antigravity_update_target(h, "/cache/bin/agy")
    assert target == "1.1.12"
    assert "/cache/bin/agy" in h.commands[0] and "--version" in h.commands[0]
    curl = h.commands[-1]
    assert curl.startswith("curl")
    assert curl.endswith("/manifests/linux_amd64.json")


@pytest.mark.asyncio
async def test_antigravity_update_target_none_when_current():
    """Manifest version equal to (or behind) the cached binary → None."""
    for manifest_version in ("1.0.16", "1.0.2"):
        doc = dict(_REAL_MANIFEST, version=manifest_version)
        h = _ScriptedHost(
            version_out=_REAL_AGY_VERSION_OUTPUT, curl_out=json.dumps(doc),
        )
        assert await host_actions._antigravity_update_target(
            h, "/cache/bin/agy",
        ) is None


@pytest.mark.asyncio
async def test_antigravity_update_target_best_effort_on_failure():
    """Manifest unreachable / malformed → None (a stale-but-working cache must
    still launch); an unparseable version probe → None WITHOUT any fetch."""
    unreachable = _ScriptedHost(version_out=_REAL_AGY_VERSION_OUTPUT, curl_exit=22)
    assert await host_actions._antigravity_update_target(
        unreachable, "/c/agy",
    ) is None

    garbage = _ScriptedHost(version_out=_REAL_AGY_VERSION_OUTPUT, curl_out="not json")
    assert await host_actions._antigravity_update_target(garbage, "/c/agy") is None

    no_version = _ScriptedHost(version_out="agy running\n")
    assert await host_actions._antigravity_update_target(
        no_version, "/c/agy",
    ) is None
    # Version probe failed → the probe stops before uname/curl (no network).
    assert len(no_version.commands) == 1


@pytest.mark.asyncio
async def test_cache_hit_stale_refreshes_via_tier2_install(
    tmp_path: pathlib.Path, monkeypatch,
):
    """A cache HIT whose binary is behind the manifest is refreshed via the
    existing Tier-2 vendor install BEFORE it is linked into the task (the
    confined session sees the cache --rox and can never refresh it itself)."""
    cache = tmp_path / "cache"
    _write_exe(cache / "agy")

    async def _stale(host, cached):  # noqa: ANN001
        return "1.1.12"

    refreshed: dict[str, str] = {}

    async def _fake_install(hook_ctx, host, *, cache_dir, cached):  # noqa: ANN001
        refreshed["cached"] = cached
        _write_exe(pathlib.Path(cached))  # the refreshed agy replaces the stale one

    async def _no_seed(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not run the miss-population path on a hit")

    monkeypatch.setattr(host_actions, "_antigravity_update_target", _stale)
    monkeypatch.setattr(host_actions, "_install_antigravity_into_cache", _fake_install)
    monkeypatch.setattr(host_actions, "_populate_antigravity_cache", _no_seed)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_antigravity_installed(ctx, install_dir=str(cache))
    assert refreshed["cached"] == str(cache / "agy")
    assert result == _task_path(ctx)
    assert os.path.realpath(result) == str((cache / "agy").resolve())


@pytest.mark.asyncio
async def test_cache_hit_current_does_not_refresh(tmp_path: pathlib.Path, monkeypatch):
    cache = tmp_path / "cache"
    _write_exe(cache / "agy")

    async def _current(host, cached):  # noqa: ANN001
        return None

    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not refresh a current cache")

    monkeypatch.setattr(host_actions, "_antigravity_update_target", _current)
    monkeypatch.setattr(host_actions, "_install_antigravity_into_cache", _boom)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_antigravity_installed(ctx, install_dir=str(cache))
    assert result == _task_path(ctx)


@pytest.mark.asyncio
async def test_cache_hit_skips_update_probe_when_check_update_false(
    tmp_path: pathlib.Path, monkeypatch,
):
    """check_update=False (the resume re-link call) re-links the cache WITHOUT
    the network probe: the earlier ensure call on the same resume already
    validated/refreshed the cache — one probe per resume, not two."""
    cache = tmp_path / "cache"
    _write_exe(cache / "agy")

    async def _no_probe(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("update probe must be skipped when check_update=False")

    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not refresh when check_update=False")

    monkeypatch.setattr(host_actions, "_antigravity_update_target", _no_probe)
    monkeypatch.setattr(host_actions, "_install_antigravity_into_cache", _boom)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_antigravity_installed(
        ctx, install_dir=str(cache), check_update=False,
    )
    assert result == _task_path(ctx)
    assert os.path.realpath(result) == str((cache / "agy").resolve())


@pytest.mark.asyncio
async def test_cache_hit_stale_not_refreshed_when_install_disabled(
    tmp_path: pathlib.Path, monkeypatch,
):
    """install_if_missing=False: a HIT links the existing cache and never runs
    the network update-check or an install (offline/pinned workers)."""
    cache = tmp_path / "cache"
    _write_exe(cache / "agy")

    async def _no_check(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("update-check must be skipped when installs are off")

    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not install when install_if_missing=False")

    monkeypatch.setattr(host_actions, "_antigravity_update_target", _no_check)
    monkeypatch.setattr(host_actions, "_install_antigravity_into_cache", _boom)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_antigravity_installed(
        ctx, install_dir=str(cache), install_if_missing=False,
    )
    assert result == _task_path(ctx)


# --- launch environment: isolation + self-update off ------------------------


def test_build_launch_env_isolation_and_path():
    env = host_actions.build_launch_env("/w/task")
    for k, v in host_actions._isolation_env("/w/task").items():
        assert env[k] == v
    # PATH prepends the per-task home/.local/bin ahead of the base PATH.
    assert env["PATH"].startswith("/w/task/home/.local/bin:")


def test_build_launch_env_disables_self_update():
    # The confirmed self-update-disable mechanism (S2) is the binary's own env
    # flag AGY_CLI_DISABLE_AUTO_UPDATE — applied on every launch path via the env
    # SSOT so a managed (pinned) agy never fights our version control.
    env = host_actions.build_launch_env("/w/task")
    assert env["AGY_CLI_DISABLE_AUTO_UPDATE"] == "1"
    # A caller cannot silently clobber it via extra_env PATH handling.
    env2 = host_actions.build_launch_env("/w/task", {"FOO": "bar"})
    assert env2["AGY_CLI_DISABLE_AUTO_UPDATE"] == "1"


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
