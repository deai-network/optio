"""Antigravity-specific actions over a generic Host.

Free functions; each takes a Host or HookContext and uses only generic
primitives (run_command, resolve_host_home, etc.). No isinstance branches
(save the local-vs-remote bind in :func:`build_host`).

Adapted from optio-grok's ``host_actions``. Stage 0 is the iframe/ttyd surface
only: resolve ``agy`` → install ``ttyd`` → launch ``agy`` inside a detached
tmux session under ttyd → drive the optio.log protocol → teardown. The binary
cache/download (Stage 5), the ACP/conversation launch (agy has none), resume
bookkeeping (Stage 2), seeds (Stage 3/4), and fs-isolation (Stage 8) land in
their own stages.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX
from optio_agents import claustrum
from optio_agents import tmux_input as _tmux_input
from optio_host.host import proc_wait

if TYPE_CHECKING:
    from optio_agents import HookContextProtocol
    from optio_host import Host
    from optio_host.host import ProcessHandle

_LOG = logging.getLogger(__name__)


# ttyd's ready banner takes a few forms across versions:
#   * 1.7.x with lws logging:  "N:  Listening on port: 33449"
#   * older builds:            "Listening on port 7681"
#   * some forks log a URL:    "[INFO] listening on http://127.0.0.1:7681/"
_TTYD_READY_RE = re.compile(
    r"(?:port[\s:]+(\d+))|(?:http://[^\s]+?:(\d+)(?:/|\s|$))"
)


# Settle (seconds) between pasting a message into the agy TUI and sending
# Enter. Without it the Enter is glued to the paste and agy treats the CR as a
# newline inside the input box instead of a submit (see send_text_to_agy). A
# shell-literal string (used in a `sleep` invocation).
_SUBMIT_SETTLE_S = "1.0"

# Pinned ttyd version. Update with care; the URL pattern below is
# tsl0922/ttyd's release-asset convention as of 1.7.x.
_TTYD_VERSION = "1.7.7"
_TTYD_RELEASE_BASE = (
    f"https://github.com/tsl0922/ttyd/releases/download/{_TTYD_VERSION}"
)

# ttyd installs into the worker home's ``.local/bin``.
_DEFAULT_INSTALL_SUBDIR = ".local/bin"

# The optio-owned agy binary cache lives on the WORKER, outside every task
# workdir and never the operator's autoupdating ``~/.gemini``/``~/.local/bin``.
# Default: ``${XDG_CACHE_HOME:-$HOME/.cache}/optio-antigravity/bin``;
# ``ANTIGRAVITY_CACHE_DIR`` overrides. Resolved via a shell echo so RemoteHost
# gets the remote location, and so the cache stays shared + evictable (never
# snapshotted — it lives outside the workdir the resume tar captures).
_ANTIGRAVITY_CACHE_DIR_SHELL_DEFAULT = (
    "${ANTIGRAVITY_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/optio-antigravity/bin}"
)

# Antigravity's two-tier install source (Global Constraints / design §3). The
# public install.sh downloads a per-platform manifest from the auto-updater
# Cloud Run host, SHA512-verifies, and extracts a single Go binary named
# ``antigravity`` which it installs as ``agy``. optio reproduces that logic
# directly (manifest -> tarball -> sha512 -> extract) into the persistent cache.
_ANTIGRAVITY_INSTALL_URL = "https://antigravity.google/cli/install.sh"
_ANTIGRAVITY_UPDATER_BASE = (
    "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app"
)
# The executable's name INSIDE the tarball (installed under the ``agy`` alias).
_ANTIGRAVITY_TARBALL_BINARY = "antigravity"


async def _resolve_install_dir(host: "Host", install_dir: str | None) -> str:
    """Return ``install_dir`` if given, else ``<host_home>/.local/bin`` (ttyd)."""
    if install_dir is not None:
        return install_dir
    host_home = await host.resolve_host_home()
    return f"{host_home}/{_DEFAULT_INSTALL_SUBDIR}"


# --- agy resolution (Stage 0 stub: no binary cache/download; that is Stage 5) ---


async def resolve_agy(
    host: "Host",
    *,
    install_dir: str | None = None,
    install_if_missing: bool = True,
) -> str:
    """Host-based ``agy`` binary resolution (no HookContext).

    Resolved from ``<install_dir>/agy`` when ``install_dir`` is given, otherwise
    via ``command -v agy`` in a login shell (so worker-profile PATH additions
    apply, e.g. ``~/.local/bin``). Raises when the binary is absent. Stage 0 has
    no auto-install — the two-tier binary cache lands in Stage 5.
    """
    if install_dir is not None:
        candidate = f"{install_dir.rstrip('/')}/agy"
        probe = await host.run_command(
            f"[ -x {shlex.quote(candidate)} ] && echo OK || true"
        )
        if "OK" in (probe.stdout or ""):
            return candidate
        raise RuntimeError(
            f"agy not present at {candidate!r} on host "
            f"(agy_install_dir={install_dir!r})."
        )

    result = await host.run_command("bash -lc 'command -v agy'")
    path = (result.stdout or "").strip()
    if result.exit_code == 0 and path:
        return path

    if not install_if_missing:
        raise RuntimeError(
            "agy not found on host and install_if_missing=False; nothing to do."
        )
    raise RuntimeError(
        "agy not found on the worker (looked via 'command -v agy'). Stage 0 has "
        "no auto-install (the binary cache is Stage 5) — install agy manually "
        "(e.g. ~/.local/bin/agy) or pass agy_install_dir."
    )


async def _resolve_antigravity_cache_dir(host: "Host", override: str | None) -> str:
    """Resolve the optio-owned agy binary-cache dir as an absolute worker path.

    ``override`` (``config.agy_install_dir``) wins. Otherwise the worker's real
    env decides via a shell echo: ``ANTIGRAVITY_CACHE_DIR`` else
    ``${XDG_CACHE_HOME:-$HOME/.cache}/optio-antigravity/bin`` — resolved on the
    host so RemoteHost gets the remote location. Mirrors grok's
    ``_resolve_grok_cache_dir``. The result lives OUTSIDE any task workdir, so
    the binary it points at is never captured by the resume snapshot."""
    if override is not None:
        return override.rstrip("/")
    r = await host.run_command(f'printf %s "{_ANTIGRAVITY_CACHE_DIR_SHELL_DEFAULT}"')
    path = (r.stdout or "").strip()
    if r.exit_code != 0 or not path:
        raise RuntimeError(
            f"failed to resolve agy cache dir on host "
            f"(exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
        )
    return path.rstrip("/")


async def _is_agy(host: "Host", path: str) -> bool:
    """True iff the binary at ``path`` is functionally the Antigravity ``agy``.

    A cache HIT (or a Tier-1 host binary) is adopted only after this functional
    identity gate, so a name-colliding or poisoned binary is rejected rather than
    launched (design §3: "verify identity functionally, not ``--version``
    alone"). ``agy --help`` is local (no auth, no network), exits promptly, and
    prints a banner naming the tool; a wrong binary neither matches nor is an
    ``agy``. Bounded with ``timeout`` so a binary that HANGS on the probe is a
    clean False rather than a hang. stderr is merged so version-dependent banner
    routing does not matter.

    TODO(S2): the real-binary spike may prefer a stronger probe (e.g. ``agy
    models`` returning the sign-in error string vs. "unknown command"); revisit
    once the real ``agy`` help/subcommand output is captured."""
    probe = await host.run_command(
        f"timeout 10 {shlex.quote(path)} --help 2>&1 || true"
    )
    blob = ((probe.stdout or "") + (probe.stderr or "")).lower()
    return "antigravity" in blob or "agy" in blob


def _manifest_url(platform: str) -> str:
    """The per-platform manifest URL on the auto-updater host."""
    return f"{_ANTIGRAVITY_UPDATER_BASE}/manifests/{platform}.json"


def _platform_slug(uname_s: str, uname_m: str, *, is_musl: bool) -> str:
    """Manifest platform slug for a ``uname -s`` / ``uname -m`` pair.

    Mirrors the auto-updater's ``install.sh`` EXACTLY (verified against the live
    manifest host): ``<os>_<arch>`` with an UNDERSCORE (not the Go ``GOOS-GOARCH``
    hyphen), and a ``_musl`` suffix on Linux against musl libc. e.g.
    ``linux_amd64`` / ``linux_arm64_musl`` / ``darwin_arm64``. A wrong separator
    404s the manifest fetch."""
    os_name = {"linux": "linux", "darwin": "darwin"}.get(uname_s.strip().lower())
    arch = {
        "x86_64": "amd64", "amd64": "amd64",
        "aarch64": "arm64", "arm64": "arm64",
    }.get(uname_m.strip())
    if os_name is None or arch is None:
        raise RuntimeError(
            f"unsupported host platform for agy auto-install "
            f"(uname -s={uname_s!r}, uname -m={uname_m!r})."
        )
    if os_name == "linux" and is_musl:
        return f"linux_{arch}_musl"
    return f"{os_name}_{arch}"


async def _host_is_musl(host: "Host") -> bool:
    """Detect a musl-libc Linux host, mirroring install.sh's probe."""
    r = await host.run_command(
        "[ -f /lib/libc.musl-x86_64.so.1 ] || "
        "[ -f /lib/libc.musl-aarch64.so.1 ] || "
        "ldd /bin/ls 2>&1 | grep -q musl"
    )
    return r.exit_code == 0


async def _antigravity_platform(host: "Host") -> str:
    """Return the updater manifest's platform slug for the host's OS/arch."""
    r_os = await host.run_command("uname -s")
    r_arch = await host.run_command("uname -m")
    if r_os.exit_code != 0 or r_arch.exit_code != 0:
        raise RuntimeError(
            f"uname failed on host (os exit {r_os.exit_code}, arch exit "
            f"{r_arch.exit_code}) — cannot resolve the agy manifest platform."
        )
    is_musl = await _host_is_musl(host)
    return _platform_slug(r_os.stdout, r_arch.stdout, is_musl=is_musl)


async def _install_antigravity_into_cache(
    hook_ctx: "HookContextProtocol",
    host: "Host",
    *,
    cache_dir: str,
    cached: str,
) -> None:
    """Tier-2 vendor install: fetch the platform manifest, download the tarball,
    SHA512-verify it, extract the ``antigravity`` binary into ``<cached>``.

    Reproduces ``install.sh``'s logic directly (manifest → tarball → sha512 →
    extract) rather than piping a remote script to a shell, so the pinned binary
    is verified and lands in the persistent cache OUTSIDE any workdir. Fetches go
    through ``hook_ctx.download_file`` (a child download task with byte-progress
    in the dashboard). Cleans up the tempdir in ``finally``; raises (leaving no
    ``<cached>``) on any manifest/checksum/extract failure."""
    platform = await _antigravity_platform(host)
    manifest_url = _manifest_url(platform)

    r = await host.run_command("mktemp -d -t optio-antigravity-XXXXXX")
    if r.exit_code != 0:
        raise RuntimeError(
            f"mktemp -d failed (exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
        )
    tmpdir = r.stdout.strip()
    try:
        hook_ctx.report_progress(None, "Fetching Antigravity manifest…")
        manifest_dest = f"{tmpdir}/manifest.json"
        await hook_ctx.download_file(manifest_url, manifest_dest)
        raw = (await host.fetch_bytes_from_host(manifest_dest)).decode("utf-8")
        try:
            manifest = json.loads(raw)
            version = manifest["version"]
            url = manifest["url"]
            expected_sha = str(manifest["sha512"]).strip().lower()
        except (ValueError, KeyError) as exc:
            raise RuntimeError(
                f"agy manifest at {manifest_url!r} is malformed "
                f"({exc!r}): {raw[:200]!r}"
            ) from exc

        hook_ctx.report_progress(None, f"Downloading Antigravity {version}…")
        tarball = f"{tmpdir}/antigravity.tar.gz"
        await hook_ctx.download_file(url, tarball)

        # SHA512-verify the downloaded tarball against the manifest before trust.
        sha_r = await host.run_command(f"sha512sum {shlex.quote(tarball)}")
        if sha_r.exit_code != 0:
            raise RuntimeError(
                f"sha512sum failed (exit {sha_r.exit_code}): "
                f"{(sha_r.stderr or '').strip()[:200]}"
            )
        actual_sha = (sha_r.stdout or "").split()[0].strip().lower()
        if actual_sha != expected_sha:
            raise RuntimeError(
                f"agy tarball SHA512 mismatch (manifest {expected_sha!r} != "
                f"downloaded {actual_sha!r}) — refusing to install {url!r}."
            )

        # Extract and locate the ``antigravity`` binary (installed as ``agy``).
        ex = await host.run_command(
            f"tar -xzf {shlex.quote(tarball)} -C {shlex.quote(tmpdir)}"
        )
        if ex.exit_code != 0:
            raise RuntimeError(
                f"extracting agy tarball failed (exit {ex.exit_code}): "
                f"{(ex.stderr or '').strip()[:200]}"
            )
        find = await host.run_command(
            f"find {shlex.quote(tmpdir)} -type f -name "
            f"{shlex.quote(_ANTIGRAVITY_TARBALL_BINARY)} | head -1"
        )
        src = (find.stdout or "").strip()
        if not src:
            raise RuntimeError(
                f"agy tarball {url!r} did not contain a "
                f"{_ANTIGRAVITY_TARBALL_BINARY!r} binary."
            )
        mk = await host.run_command(f"mkdir -p {shlex.quote(cache_dir)}")
        if mk.exit_code != 0:
            raise RuntimeError(
                f"mkdir -p {cache_dir!r} failed (exit {mk.exit_code}): "
                f"{(mk.stderr or '').strip()[:200]}"
            )
        mv = await host.run_command(
            f"mv -f {shlex.quote(src)} {shlex.quote(cached)} && "
            f"chmod +x {shlex.quote(cached)}"
        )
        if mv.exit_code != 0:
            raise RuntimeError(
                f"installing agy into cache ({src!r} -> {cached!r}) failed "
                f"(exit {mv.exit_code}): {(mv.stderr or '').strip()[:200]}"
            )
        if not await _is_agy(host, cached):
            await host.run_command(f"rm -f {shlex.quote(cached)}")
            raise RuntimeError(
                f"agy Tier-2 install completed but {cached!r} failed the "
                f"functional identity check (not an agy). Cache left empty."
            )
        _LOG.info(
            "ensure_antigravity_installed: Tier-2 vendor-installed %s (%s)",
            cached, version,
        )
    finally:
        # Best-effort cleanup; don't mask a primary exception.
        await host.run_command(f"rm -rf {shlex.quote(tmpdir)}")


async def _populate_antigravity_cache(
    hook_ctx: "HookContextProtocol",
    host: "Host",
    *,
    cache_dir: str,
    cached: str,
) -> None:
    """Fill an empty/poisoned cache: prefer seeding from a pre-existing host
    ``agy`` (Tier-1, fast, no download); fall back to the Tier-2 vendor install
    (manifest+tarball) when the worker has none. Leaves a functional
    ``<cache_dir>/agy`` on success; raises otherwise."""
    # Tier-1 — an agy already on the worker (login-shell PATH), functionally
    # validated so a name-colliding binary is never seeded.
    source: "str | None"
    try:
        source = await resolve_agy(host, install_dir=None, install_if_missing=False)
    except RuntimeError:
        source = None
    if source is not None and not await _is_agy(host, source):
        source = None

    if source is None:
        await _install_antigravity_into_cache(
            hook_ctx, host, cache_dir=cache_dir, cached=cached,
        )
        return

    hook_ctx.report_progress(None, "Seeding Antigravity cache…")
    # ``-L`` dereferences: a symlinked host agy becomes a real, stable copy in
    # the cache (independent of the host binary the operator may autoupdate).
    r = await host.run_command(
        f"mkdir -p {shlex.quote(cache_dir)} && "
        f"cp -L {shlex.quote(source)} {shlex.quote(cached)} && "
        f"chmod +x {shlex.quote(cached)}"
    )
    if r.exit_code != 0:
        raise RuntimeError(
            f"seeding agy cache (cp {source!r} -> {cached!r}) failed "
            f"(exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
        )
    if not await _is_agy(host, cached):
        raise RuntimeError(
            f"agy cache seed from {source!r} produced a non-functional "
            f"{cached!r} (identity check failed)."
        )
    _LOG.info("ensure_antigravity_installed: Tier-1 seeded from host %s", source)


async def _link_antigravity_into_task(host: "Host", cached: str) -> str:
    """Symlink the cached agy binary into the task's isolated home launch dir.

    The cache lives OUTSIDE the workdir (persists across task teardown); the
    launch path ``<workdir>/home/.local/bin/agy`` is a per-task symlink to it,
    ahead on the launch PATH (:func:`build_launch_env`). Returns that task path.
    ``ln -sfn`` is idempotent — a resume re-call just refreshes the symlink on the
    restored tree. Mirrors grok's ``_link_grok_into_task``."""
    workdir = host.workdir.rstrip("/")
    bin_dir = f"{workdir}/home/.local/bin"
    task_agy = f"{bin_dir}/agy"
    r = await host.run_command(
        f"mkdir -p {shlex.quote(bin_dir)} && "
        f"ln -sfn {shlex.quote(cached)} {shlex.quote(task_agy)}"
    )
    if r.exit_code != 0:
        raise RuntimeError(
            f"linking agy into the task path ({task_agy!r} -> {cached!r}) "
            f"failed (exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
        )
    return task_agy


async def ensure_antigravity_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
    progress_label: str = "Preparing Antigravity…",
) -> str:
    """Provision ``agy`` for this task from the optio-owned binary cache and
    return its per-task launch path.

    The cache dir (:func:`_resolve_antigravity_cache_dir`) lives on the worker
    OUTSIDE any task workdir and never the operator's autoupdating ``~/.gemini``
    — so it stays shared, evictable, and unsnapshotted (it survives task
    teardown). The cache is the single stable home of the binary; the value
    RETURNED is the per-task launch symlink ``<workdir>/home/.local/bin/agy``,
    ahead on the launch PATH (mirrors grok's ``home/.local/bin/grok``).

    Cache population (only on a miss/poison, and only when ``install_if_missing``):

    - **Tier-1 seed** — an ``agy`` already on the worker (login-shell PATH) that
      passes the functional identity gate is copied (deref) into the cache.
    - **Tier-2 vendor install** — otherwise fetch the platform manifest + tarball
      from the auto-updater host, SHA512-verify, and extract the ``antigravity``
      binary into the cache.

    A cache HIT is adopted only after :func:`_is_agy` (functional identity), so a
    poisoned/name-colliding cached binary is invalidated and repopulated. Uses
    only generic Host primitives. Idempotent on a re-call (hit → it just re-links
    the task path), which is how resume re-establishes the launch symlink after
    ``restore_workdir`` wipes it. Raises only when the cache needs populating AND
    ``install_if_missing=False``.
    """
    host = hook_ctx._host
    hook_ctx.report_progress(None, progress_label)

    cache_dir = await _resolve_antigravity_cache_dir(host, install_dir)
    cached = f"{cache_dir}/agy"

    probe = await host.run_command(
        f"[ -x {shlex.quote(cached)} ] && echo OK || true"
    )
    executable = "OK" in (probe.stdout or "")
    if executable and await _is_agy(host, cached):
        _LOG.info("ensure_antigravity_installed: cache HIT (%s)", cached)
    elif not install_if_missing:
        raise RuntimeError(
            f"agy not present (or not functional) in cache at {cached!r} and "
            f"install_if_missing=False; nothing to do."
        )
    else:
        if executable:
            # Poisoned: an executable that is NOT an agy — evict before refill.
            _LOG.warning(
                "ensure_antigravity_installed: poisoned cache at %s "
                "(fails identity check) — invalidating", cached,
            )
            await host.run_command(f"rm -f {shlex.quote(cached)}")
        await _populate_antigravity_cache(
            hook_ctx, host, cache_dir=cache_dir, cached=cached,
        )

    return await _link_antigravity_into_task(host, cached)


def build_host(ssh, taskdir: str) -> "Host":
    """ssh_config + taskdir -> LocalHost/RemoteHost. Shared with engine-free
    callers (verify) — mirrors grok/opencode's host_actions.build_host. The
    Local-vs-Remote bind is the one permitted isinstance-shaped branch."""
    from optio_host.host import LocalHost, RemoteHost

    if ssh is None:
        os.makedirs(taskdir, exist_ok=True)
        host: "Host" = LocalHost(taskdir=taskdir)
        os.makedirs(host.workdir, exist_ok=True)
        return host
    return RemoteHost(ssh_config=ssh, taskdir=taskdir)


def _isolation_env(workdir: str) -> dict[str, str]:
    """Single source of truth for a task's HOME/XDG agent identity.

    Every agy launch derives its environment from this map so isolation is
    identical across launch paths. All keys are rooted at ``<workdir>/home``:

    - ``HOME`` — agy's own state (its ``~/.gemini`` tree: transcript, artifacts,
      ``antigravity-cli/settings.json``) lands in the per-task home.
    - ``XDG_CONFIG_HOME`` / ``XDG_DATA_HOME`` / ``XDG_CACHE_HOME`` — pin the XDG
      base dirs into the task home so no XDG-respecting tool reaches the
      operator's ``~/.config`` / ``~/.cache``.

    PATH is intentionally NOT included: it is layered by the caller (launch adds
    ``<home>/.local/bin`` ahead of the worker PATH)."""
    home = f"{workdir.rstrip('/')}/home"
    return {
        "HOME": home,
        "XDG_CONFIG_HOME": f"{home}/.config",
        "XDG_DATA_HOME": f"{home}/.local/share",
        "XDG_CACHE_HOME": f"{home}/.cache",
    }


# --- ttyd install (copied verbatim from optio-grok) -------------------------


async def _ttyd_present(host: "Host", ttyd_path: str) -> bool:
    cmd = f"[ -x {shlex.quote(ttyd_path)} ] && {shlex.quote(ttyd_path)} --version"
    result = await host.run_command(cmd)
    # ttyd writes its version banner to stdout OR stderr depending on
    # version — accept either.
    blob = (result.stdout or "") + (result.stderr or "")
    return result.exit_code == 0 and "ttyd" in blob.lower()


async def _detect_ttyd_asset_name(host: "Host") -> str:
    """Return the upstream release-asset filename for the host's arch/OS.

    Raises RuntimeError on unsupported (OS, arch) combinations.
    """
    r_arch = await host.run_command("uname -m")
    if r_arch.exit_code != 0:
        raise RuntimeError(
            f"uname -m failed on host (exit {r_arch.exit_code}): "
            f"{r_arch.stderr.strip()[:200]}"
        )
    arch = r_arch.stdout.strip()
    r_os = await host.run_command("uname -s")
    if r_os.exit_code != 0:
        raise RuntimeError(
            f"uname -s failed on host (exit {r_os.exit_code}): "
            f"{r_os.stderr.strip()[:200]}"
        )
    os_name = r_os.stdout.strip()
    if os_name != "Linux":
        raise RuntimeError(
            f"unsupported host OS {os_name!r} for ttyd auto-install "
            f"(v1 supports Linux only; macOS support requires uploading "
            f"a Darwin binary or pre-installing ttyd manually)."
        )
    if arch not in {"x86_64", "aarch64", "armv7l"}:
        raise RuntimeError(
            f"unsupported host arch {arch!r} for ttyd auto-install. "
            f"See https://github.com/tsl0922/ttyd/releases for available "
            f"prebuilt assets."
        )
    return f"ttyd.{arch}"


async def ensure_ttyd_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
) -> str:
    """Ensure ``ttyd`` is present on the host behind ``hook_ctx``.

    When missing and ``install_if_missing=True``, downloads the appropriate
    static prebuilt asset from ``tsl0922/ttyd`` GitHub Releases via
    ``hook_ctx.download_file`` (so byte-progress shows in the dashboard).

    Returns the absolute path of the ``ttyd`` binary on the host.
    """
    host = hook_ctx._host
    resolved_install_dir = await _resolve_install_dir(host, install_dir)
    ttyd_path = f"{resolved_install_dir}/ttyd"

    hook_ctx.report_progress(None, "Checking ttyd installation…")
    if await _ttyd_present(host, ttyd_path):
        return ttyd_path

    if not install_if_missing:
        raise RuntimeError(
            f"ttyd not present at {ttyd_path!r} on host and "
            f"install_ttyd_if_missing=False; nothing to do."
        )

    hook_ctx.report_progress(None, "Detecting ttyd release asset…")
    asset = await _detect_ttyd_asset_name(host)
    url = f"{_TTYD_RELEASE_BASE}/{asset}"

    r = await host.run_command(f"mkdir -p {shlex.quote(resolved_install_dir)}")
    if r.exit_code != 0:
        raise RuntimeError(
            f"mkdir -p {resolved_install_dir!r} failed (exit {r.exit_code}): "
            f"{r.stderr.strip()[:200]}"
        )

    hook_ctx.report_progress(None, f"Downloading ttyd ({asset})…")
    await hook_ctx.download_file(url, ttyd_path)

    r = await host.run_command(f"chmod +x {shlex.quote(ttyd_path)}")
    if r.exit_code != 0:
        raise RuntimeError(
            f"chmod +x {ttyd_path!r} failed (exit {r.exit_code}): "
            f"{r.stderr.strip()[:200]}"
        )

    if not await _ttyd_present(host, ttyd_path):
        raise RuntimeError(
            f"ttyd install completed but {ttyd_path!r} is still not "
            f"executable on the host. Check the downloaded asset and "
            f"chmod result."
        )
    return ttyd_path


# --- launch env + DONE/ERROR wrapper ---------------------------------------


def build_launch_env(
    workdir: str, extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Full environment for an agy launch: the per-task isolation identity
    (:func:`_isolation_env`) + ``PATH`` (the per-task ``home/.local/bin``
    prepended ahead of the worker PATH) + caller extras.

    Single source of truth for the launch environment across every launch path
    so isolation is identical. ``PATH`` is layered here (not in the isolation
    SSOT) so the per-task ``agy`` symlink resolves first. A caller ``extra_env``
    ``PATH`` becomes the BASE the per-task bin is prepended to; all other extras
    override the isolation defaults.

    Self-update disable is applied here as ``AGY_CLI_DISABLE_AUTO_UPDATE=1`` — the
    agy binary's own env flag (confirmed by probing the real binary), so a managed
    (Tier-1/Tier-2 pinned) agy never fights our version control nor stalls a launch
    on a background update probe. Layered into the env SSOT so it applies
    uniformly across every launch path (iframe/tmux + conversation/PTY).
    :func:`disable_agy_self_update` additionally writes the ``settings.json``
    ``AutoUpdate:false`` key as belt-and-suspenders. A caller ``extra_env`` MAY
    override the flag (last-wins) if it ever needs the updater on."""
    iso = _isolation_env(workdir.rstrip("/"))
    home_local_bin = f"{iso['HOME']}/.local/bin"
    extra = dict(extra_env or {})
    base_path = extra.pop("PATH", None) or os.environ.get(
        "PATH", "/usr/local/bin:/usr/bin:/bin",
    )
    return {
        **iso,
        "PATH": f"{home_local_bin}:{base_path}",
        # agy's own no-auto-update flag — the confirmed S2 mechanism.
        "AGY_CLI_DISABLE_AUTO_UPDATE": "1",
        **extra,
    }


async def disable_agy_self_update(host: "Host", workdir: str) -> None:
    """Best-effort disable of agy's background self-update for this task.

    Writes ``AutoUpdate:false`` into the task's isolated
    ``<workdir>/home/.gemini/antigravity-cli/settings.json`` as a parsed-JSON
    mutation (never a blind append — config-injection discipline): the existing
    document is loaded, the key set, and the whole document re-serialized so
    other settings (model, color scheme, trusted paths) are preserved. A managed
    wrapper pins the binary itself (Tier-1/Tier-2 cache), so a background
    self-update would fight our version control and can stall a launch on a
    network probe.

    A missing settings file is created; a corrupt one is reset to a minimal
    ``{AutoUpdate:false}`` (agy rewrites its own settings on start anyway).
    Host-primitive only (uniform Local/Remote). Called on EVERY launch path.

    S2 resolved: the PRIMARY disable is the ``AGY_CLI_DISABLE_AUTO_UPDATE=1`` env
    flag layered into :func:`build_launch_env` (the agy binary's own no-auto-update
    switch, confirmed by probing the real binary). This settings write is a
    secondary belt-and-suspenders and is kept for defence in depth."""
    home = f"{workdir.rstrip('/')}/home"
    settings_dir = f"{home}/.gemini/antigravity-cli"
    settings = f"{settings_dir}/settings.json"

    try:
        raw = (await host.fetch_bytes_from_host(settings)).decode("utf-8")
    except FileNotFoundError:
        raw = ""
    doc: dict[str, object] = {}
    if raw.strip():
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                doc = loaded
        except ValueError:
            _LOG.warning(
                "disable_agy_self_update: settings.json at %s is not valid JSON "
                "— resetting to {AutoUpdate:false}", settings,
            )
    doc["AutoUpdate"] = False  # TODO(S2): confirm this suppresses the updater.

    payload = json.dumps(doc, indent=2)
    r = await host.run_command(
        f"mkdir -p {shlex.quote(settings_dir)} && "
        f"printf '%s' {shlex.quote(payload)} > {shlex.quote(settings)}"
    )
    if r.exit_code != 0:
        raise RuntimeError(
            f"writing agy settings.json (AutoUpdate:false) failed "
            f"(exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
        )


def _build_agy_shell_command(
    *,
    agy_path: str,
    workdir: str,
    extra_env: dict[str, str] | None,
    agy_flags: list[str],
    claustrum_wrap: list[str] | None = None,
) -> tuple[list[str], str]:
    """Return (env_assignments, shell_command).

    ``env_assignments`` is the list of ``KEY=VALUE`` strings (HOME, PATH, XDG_*,
    extras) from :func:`build_launch_env` — the launch-env SSOT. ``shell_command``
    is the full ``env <assignments> bash -c <payload>`` string that runs agy under
    HOME-isolation and appends DONE/ERROR to optio.log when agy exits. Consumed by
    build_tmux_session_argv (agy runs inside the detached tmux session, not as a
    direct ttyd child).

    ``claustrum_wrap`` (Stage 8): when fs-isolation is on, the claustrum argv
    prefix from :func:`_build_claustrum_wrap` is prepended AHEAD of the agy
    invocation, so ``bash -c`` execs claustrum, which applies the Landlock
    allowlist then execve's agy — agy + every tool subprocess it spawns inherit
    the confinement. ``rc=$?`` still captures agy's real exit code (claustrum
    exits with its child's). None when fs-isolation is off (agy runs unconfined).
    """
    workdir_clean = workdir.rstrip("/")
    env_map = build_launch_env(workdir_clean, extra_env)
    env_assignments: list[str] = [f"{k}={v}" for k, v in env_map.items()]

    agy_argv = " ".join(
        shlex.quote(c) for c in [*(claustrum_wrap or []), agy_path, *agy_flags]
    )
    log_path = f"{workdir_clean}/optio.log"

    bash_payload = (
        f"cd {shlex.quote(workdir_clean)} && {agy_argv}; rc=$?; "
        f'if [ "$rc" = 0 ]; then echo DONE >> {shlex.quote(log_path)}; '
        f"else printf 'ERROR: agy exited %s\\n' \"$rc\" >> {shlex.quote(log_path)}; fi"
    )
    shell_command = "env " + " ".join(
        shlex.quote(x) for x in [*env_assignments, "bash", "-c", bash_payload]
    )
    return env_assignments, shell_command


# --- flags -----------------------------------------------------------------


# agy's native permission surface is binary: normal prompting or
# ``--dangerously-skip-permissions`` (auto-approve every tool). The generic
# callers pass the claudecode-style ``bypassPermissions``; the config validates
# it and this map resolves the alias to agy's real flag.
_PERMISSION_MODE_ALIASES = {"bypassPermissions": "dangerously-skip-permissions"}


def build_agy_flags(
    *,
    permission_mode: str | None,
    model: str | None,
    resuming: bool = False,
) -> list[str]:
    """Translate AntigravityTaskConfig knobs to an argv list.

    ``permission_mode`` accepts agy's ``default`` / ``dangerously-skip-permissions``
    plus the claudecode-style ``bypassPermissions`` alias; only the skip flag has
    an argv effect (``default`` emits nothing — agy prompts by default and has no
    ``--permission-mode`` option). ``--model`` passes the model through.
    ``--continue`` is appended when ``resuming`` (Stage 2; always False in
    Stage 0). Validation of ``permission_mode`` lives in
    ``AntigravityTaskConfig.__post_init__``.
    """
    out: list[str] = []
    resolved_perm = _PERMISSION_MODE_ALIASES.get(permission_mode, permission_mode)
    if resolved_perm == "dangerously-skip-permissions":
        out += ["--dangerously-skip-permissions"]
    if model:
        out += ["--model", model]
    if resuming:
        out += ["--continue"]
    return out


# Positional prompt appended to the agy launch when ``auto_start`` is set —
# kicks the agent off without the operator typing anything.
AUTO_START_PROMPT = "Read AGENTS.md and execute the task it describes"


def build_auto_start_args(
    *, auto_start: bool, resuming: bool = False, prompt: str = AUTO_START_PROMPT,
) -> list[str]:
    """Trailing positional prompt for an auto-start FRESH launch.

    Returns ``[prompt]`` when ``auto_start`` and not ``resuming``; empty
    otherwise. On resume the session is continued with ``--continue`` and no
    positional is appended: re-issuing the kickoff prompt would start a new task
    instead of resuming the existing conversation.
    """
    return [prompt] if (auto_start and not resuming) else []


def build_resume_notice_args(*, resuming: bool) -> list[str]:
    """Trailing positional that notifies a resumed agy TUI session.

    Returns ``[f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}"]`` on resume (agy
    continues with ``--continue``, so a trailing positional is processed as a new
    turn in the continued conversation — mirrors claudecode's
    ``claude --continue '<text>'`` and grok's ``grok -c '<text>'``). Empty on a
    fresh launch. This is the PUSH half of resume awareness — it makes the agent
    notice the resume promptly; ``resume.log`` remains the pull-based source of
    truth. In iframe mode ``host_protocol`` is always on, so the optio.log
    keyword docs teach the ``System:`` convention and no host_protocol gate is
    needed here. Mutually exclusive with :func:`build_auto_start_args`
    (auto_start only fires on a FRESH launch).
    """
    return [f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}"] if resuming else []


# --- tmux / ttyd machinery (adapted verbatim from optio-grok) ---------------


def build_tmux_session_argv(
    *,
    tmux_path: str,
    agy_path: str,
    workdir: str,
    socket_path: str,
    session_name: str,
    extra_env: dict[str, str] | None,
    agy_flags: list[str],
    claustrum_wrap: list[str] | None = None,
) -> list[str]:
    """Argv for the detached ``tmux new-session`` that starts agy.

    tmux runs its command argument via ``/bin/sh -c``, so the env + agy wrapper
    is a single trailing shell-string element. The private socket
    (``-S socket_path``) isolates this task's tmux server. ``-x/-y`` give the
    detached pane a sane initial size before any viewer attaches.

    ``claustrum_wrap`` (Stage 8) confines the agy invocation inside the tmux
    pane; tmux + ttyd themselves stay unconfined (infrastructure). None → agy
    runs unconfined.
    """
    _, shell_command = _build_agy_shell_command(
        agy_path=agy_path,
        workdir=workdir,
        extra_env=extra_env,
        agy_flags=agy_flags,
        claustrum_wrap=claustrum_wrap,
    )
    return [
        tmux_path, "-S", socket_path, "new-session", "-d",
        "-s", session_name, "-x", "200", "-y", "50",
        shell_command,
    ]


def build_ttyd_attach_argv(
    *,
    ttyd_path: str,
    tmux_path: str,
    socket_path: str,
    session_name: str,
    bind_iface: str,
    port: int,
) -> list[str]:
    """Argv for ttyd attaching viewers to the live tmux session.

    ttyd does not run agy — it runs ``tmux attach``. ``-t disableLeaveAlert=true``
    turns off ttyd's web-client ``beforeunload`` prompt (with tmux persistence,
    leaving the page only detaches a viewer; the session keeps running).
    """
    return [
        ttyd_path, "-W",
        "-i", bind_iface,
        "-p", str(port),
        "-t", "disableLeaveAlert=true",
        "-T", "xterm-256color",
        "--",
        tmux_path, "-S", socket_path, "attach", "-t", session_name,
    ]


def _tmux_socket_path(host: "Host") -> str:
    """Short, bounded, per-task tmux socket path under ``/tmp``.

    The socket must NOT live under ``host.workdir``: a deep ``$HOME`` plus a
    long processId can push ``${workdir}/tmux.sock`` past the Linux ``sun_path``
    limit (108 bytes). ``sha256(workdir)`` keys the socket per task
    (deterministic across the task's calls, collision-safe); ``/tmp`` always
    exists so no mkdir is needed."""
    import hashlib

    digest = hashlib.sha256(host.workdir.encode("utf-8")).hexdigest()[:16]
    return f"/tmp/optio-ag-{digest}.sock"


async def _require_tmux(host: "Host") -> str:
    """Return the absolute path to tmux on the host, or raise a clear error.

    agy runs inside a detached tmux session (so the agent survives viewer
    disconnects); tmux is a worker prerequisite. Resolved via a login shell so
    PATH additions from the worker profile apply. No auto-install: a missing
    tmux fails fast with an actionable message.
    """
    result = await host.run_command("bash -lc 'command -v tmux'")
    path = (result.stdout or "").strip()
    if result.exit_code != 0 or not path:
        raise RuntimeError(
            "tmux is required on the worker for optio-antigravity (agy runs "
            "inside a detached tmux session). Install tmux (e.g. apt-get "
            "install tmux) or add it to the worker/container image."
        )
    return path


async def _launch_detached_checked(
    host: "Host", cmd: str, *, env_remove: list[str] | None, what: str,
) -> list[str]:
    """Launch a detached command, drain its (stderr-merged) stdout, then check
    the exit code. Non-zero raises ``RuntimeError`` carrying the output.

    ``launch_subprocess`` returns a streaming handle with no ``exit_code``, so
    the code is recovered via ``proc_wait``. Silently swallowing it is what
    turned tmux's clear "File name too long" into a misleading downstream error.
    """
    handle = await host.launch_subprocess(cmd, env_remove=env_remove)
    out: list[str] = []
    async for raw in handle.stdout:
        out.append(
            raw.decode("utf-8", errors="replace")
            if isinstance(raw, bytes) else str(raw)
        )
    code = await proc_wait(handle)
    if code != 0:
        raise RuntimeError(f"{what} failed (exit {code}): {''.join(out).strip()[:500]}")
    return out


async def launch_ttyd_with_agy(
    host: "Host",
    *,
    ttyd_path: str,
    agy_path: str,
    bind_iface: str,
    extra_env: dict[str, str] | None,
    agy_flags: list[str],
    ready_timeout_s: float = 30.0,
    env_remove: list[str] | None = None,
    session_name: str = "optio",
    claustrum_wrap: list[str] | None = None,
) -> "tuple[ProcessHandle, int, str, str]":
    """Start agy in a detached tmux session, then ttyd attaching to it.

    Returns ``(ttyd_handle, port, socket_path, session_name)``. agy runs in the
    tmux session independent of ttyd; the caller awaits tmux-session liveness
    for completion and tears down BOTH the tmux session and ttyd.

    ``claustrum_wrap`` (Stage 8) confines the agy invocation inside the tmux
    pane (fail-closed fs-isolation); None → agy runs unconfined.
    """
    tmux_path = await _require_tmux(host)
    socket_path = _tmux_socket_path(host)

    # 1) Start agy detached in tmux. The env scrub (env_remove) must apply here
    #    so the tmux server — which holds agy — does not inherit scrubbed vars.
    session_argv = build_tmux_session_argv(
        tmux_path=tmux_path,
        agy_path=agy_path,
        workdir=host.workdir,
        socket_path=socket_path,
        session_name=session_name,
        extra_env=extra_env,
        agy_flags=agy_flags,
        claustrum_wrap=claustrum_wrap,
    )
    session_cmd = " ".join(shlex.quote(a) for a in session_argv)
    await _launch_detached_checked(
        host, session_cmd, env_remove=env_remove, what="tmux new-session",
    )

    # 2) Start ttyd attaching to the live session.
    ttyd_argv = build_ttyd_attach_argv(
        ttyd_path=ttyd_path,
        tmux_path=tmux_path,
        socket_path=socket_path,
        session_name=session_name,
        bind_iface=bind_iface,
        port=0,
    )
    command = " ".join(shlex.quote(a) for a in ttyd_argv)
    handle = await host.launch_subprocess(command)

    async def _read_port() -> int:
        async for raw in handle.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip() if isinstance(raw, bytes) else str(raw).rstrip()
            m = _TTYD_READY_RE.search(line)
            if m:
                port_str = m.group(1) or m.group(2)
                return int(port_str)
        raise RuntimeError("ttyd exited before printing a listening URL")

    try:
        port = await asyncio.wait_for(_read_port(), timeout=ready_timeout_s)
    except asyncio.TimeoutError:
        await host.terminate_subprocess(handle, aggressive=True)
        await _kill_tmux_session(host, tmux_path, socket_path, session_name)
        raise TimeoutError(
            f"ttyd did not print a listening URL within {ready_timeout_s}s"
        )
    except BaseException:
        await host.terminate_subprocess(handle, aggressive=True)
        await _kill_tmux_session(host, tmux_path, socket_path, session_name)
        raise
    return handle, port, socket_path, session_name


async def _kill_tmux_session(
    host: "Host", tmux_path: str, socket_path: str, session_name: str,
) -> None:
    """Best-effort kill of the per-task tmux session (stops agy)."""
    try:
        await host.run_command(
            f"{shlex.quote(tmux_path)} -S {shlex.quote(socket_path)} "
            f"kill-session -t {shlex.quote(session_name)}"
        )
    except Exception:  # noqa: BLE001
        _LOG.exception("tmux kill-session failed (socket=%s)", socket_path)


def _agy_pgrep_pattern(agy_path: str) -> str:
    """Anchored pgrep/pkill pattern matching ONLY the real agy.

    The real agy execs with the path as the FIRST token of its cmdline
    (argv[0]), whereas the tmux server and the bash/env wrappers carry the same
    path only as a LATER argument. ``^`` excludes them; only a process whose
    cmdline starts with the path matches. ``[a]gy`` keeps pgrep/pkill's own
    cmdline from self-matching.
    """
    body = agy_path[:-3] + "[a]gy" if agy_path.endswith("agy") else agy_path
    return "^" + body


def _socket_pkill_pattern(socket_path: str) -> str:
    """Anchored pkill -f pattern matching the orphan ttyd that carries
    ``socket_path`` in its cmdline (``ttyd ... -- tmux -S <socket> attach``).

    The ``ttyd`` binary token is bracket-escaped (``[t]tyd``) so pkill's own
    argv does not self-match. The full ``socket_path`` is kept verbatim so the
    match is scoped to this task's private socket."""
    if not socket_path:
        return socket_path
    return f"[t]tyd.*{socket_path}"


async def _kill_ttyd_by_socket(host: "Host", socket_path: str) -> None:
    """Reap a detached orphan ttyd that has no tracked launch handle.

    Best-effort: pkill exits non-zero when nothing matches."""
    pattern = _socket_pkill_pattern(socket_path)
    await host.run_command(f"pkill -KILL -f {shlex.quote(pattern)} || true")


async def kill_agy_processes(
    host: "Host", agy_path: str, *, signal: str = "KILL",
) -> None:
    """Kill the per-task agy via an anchored host-side ``pkill``.

    agy ignores the tmux pane SIGHUP. Best-effort: pkill exits non-zero when
    nothing matches."""
    pattern = _agy_pgrep_pattern(agy_path)
    await host.run_command(f"pkill -{signal} -f {shlex.quote(pattern)} || true")


async def await_agy_gone(
    host: "Host", agy_path: str, *, timeout_s: float = 15.0, poll_s: float = 1.0,
) -> bool:
    """Block (polling once per ``poll_s``) until no process matching the per-task
    ``agy_path`` remains. Bounded by ``timeout_s`` (logs a warning and returns
    False on timeout). Returns True once agy is gone."""
    pattern = _agy_pgrep_pattern(agy_path)
    waited = 0.0
    while True:
        r = await host.run_command(f"pgrep -f {shlex.quote(pattern)} || true")
        if not (r.stdout or "").strip():
            return True
        if waited >= timeout_s:
            _LOG.warning(
                "await_agy_gone: agy still running after %.0fs (path=%s); "
                "proceeding anyway", timeout_s, agy_path,
            )
            return False
        await asyncio.sleep(poll_s)
        waited += poll_s


async def teardown_session_tree(
    host: "Host",
    *,
    tmux_path: str,
    tmux_socket: str,
    tmux_session: str,
    agy_path: str,
    ttyd_handle: "ProcessHandle | None" = None,
    aggressive: bool,
) -> None:
    """Kill a full agy session tree (ttyd + tmux + agy).

    Four best-effort steps, each isolated so one failure does not abort the
    rest: (1) ttyd via the tracked handle or an anchored socket pkill;
    (2) ``kill-session`` SIGHUPs the tmux pane; (3) ``kill_agy_processes``
    (agy ignores the pane SIGHUP); (4) ``await_agy_gone`` waits for quiescence.
    """
    if ttyd_handle is not None:
        try:
            await host.terminate_subprocess(ttyd_handle, aggressive=aggressive)
        except Exception:
            _LOG.exception("terminate_subprocess (ttyd) failed")
    else:
        try:
            await _kill_ttyd_by_socket(host, tmux_socket)
        except Exception:
            _LOG.exception("orphan ttyd reap failed (socket=%s)", tmux_socket)

    try:
        await _kill_tmux_session(host, tmux_path, tmux_socket, tmux_session)
    except Exception:
        _LOG.exception("tmux session teardown failed")

    try:
        await kill_agy_processes(host, agy_path)
    except Exception:
        _LOG.exception("kill_agy_processes failed")

    try:
        await await_agy_gone(host, agy_path)
    except Exception:
        _LOG.exception("await_agy_gone failed; proceeding")


async def tmux_session_alive(
    host: "Host", tmux_path: str, socket_path: str, session_name: str,
) -> bool:
    """True while the agy-bearing tmux session exists."""
    r = await host.run_command(
        f"{shlex.quote(tmux_path)} -S {shlex.quote(socket_path)} "
        f"has-session -t {shlex.quote(session_name)}"
    )
    return r.exit_code == 0


async def send_text_to_agy(
    host: "Host", tmux_path: str, tmux_socket: str, tmux_session: str, text: str,
) -> None:
    """Fake-type a message into the agy TUI and submit it.

    Thin wrapper over the shared
    :func:`optio_agents.tmux_input.send_text_to_tmux`, pinned to agy's buffer name
    (``optio-feedback``) and settle (``_SUBMIT_SETTLE_S``)."""
    await _tmux_input.send_text_to_tmux(
        host, tmux_path, tmux_socket, tmux_session, text,
        buffer="optio-feedback", submit_settle=_SUBMIT_SETTLE_S,
    )


async def send_key_to_agy(
    host: "Host", tmux_path: str, tmux_socket: str, tmux_session: str, key: str,
) -> None:
    """Send a single navigation keystroke into the agy TUI (iframe-input empty-box
    TUI nav). Thin wrapper over :func:`optio_agents.tmux_input.send_key_to_tmux`."""
    await _tmux_input.send_key_to_tmux(host, tmux_path, tmux_socket, tmux_session, key)


# --- resume bookkeeping (adapted from optio-grok/opencode) ------------------


async def _rotate_optio_log(host: "Host") -> None:
    """Append the restored optio.log to optio.log.old, then truncate it.

    Preserves historical log content across consecutive resumes while ensuring
    the tail driver only sees fresh lines from the resumed run (a stale DONE/
    ERROR carried in the restored log would otherwise be replayed and end the
    session immediately).
    """
    workdir = host.workdir.rstrip("/")
    log_abs = f"{workdir}/optio.log"
    old_abs = f"{workdir}/optio.log.old"
    try:
        current = (await host.fetch_bytes_from_host(log_abs)).decode("utf-8")
    except FileNotFoundError:
        current = ""
    if not current:
        await host.write_text("optio.log", "")
        return
    try:
        existing_old = (await host.fetch_bytes_from_host(old_abs)).decode("utf-8")
    except FileNotFoundError:
        existing_old = ""
    await host.write_text("optio.log.old", existing_old + current)
    await host.write_text("optio.log", "")


async def _append_resume_log_entry(
    host: "Host", *, refreshed: list[str] | None = None,
) -> None:
    """Append one line to ``<workdir>/resume.log``.

    Line format: ``<ISO 8601 UTC timestamp>[ REFRESHED:<comma-separated names>]``.
    The first line is the original launch; each later line marks a resume. The
    caller gates this on ``config.supports_resume``.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} REFRESHED:{','.join(refreshed)}" if refreshed else ts
    target = f"{host.workdir.rstrip('/')}/resume.log"
    result = await host.run_command(
        f"echo {shlex.quote(line)} >> {shlex.quote(target)}"
    )
    if result.exit_code != 0:
        raise RuntimeError(
            f"failed to append to resume.log: exit {result.exit_code}: "
            f"{result.stderr!r}"
        )


# --- Stage 8: claustrum filesystem isolation --------------------------------
#
# Ported from optio_kimicode.host_actions (claudecode → kimicode → antigravity).
# claustrum is a standalone Landlock sandbox CLI: it applies a fs allowlist to
# itself, then execve's the wrapped target, so agy + every tool subprocess it
# spawns inherit the confinement. Default-on, fail-closed (provisioning raises
# rather than launching unconfined), local and remote (Host primitives only).
#
# Design §8: agy has a NATIVE ``--sandbox`` too, but it is unverifiable without a
# real Google login and is a fail-OPEN "terminal restriction", so claustrum is
# the enforced kernel jail here; combining with ``--sandbox`` is a future opt-in.


async def ensure_claustrum_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_dir: str | None = None,
) -> str:
    """Ensure a claustrum binary (pinned tag, host arch) is on the host.

    Thin wrapper-specific shim over :func:`optio_agents.claustrum.ensure_claustrum_installed`
    (the shared provisioner: detect arch, cross-compile on the engine cached by
    (tag, arch), place on the target host, and FUNCTIONALLY validate). This layer
    only resolves the two wrapper-specific paths:

    - the TARGET-host cache dir (:func:`_resolve_antigravity_cache_dir`), beside
      the agy binary cache and outside every task workdir; and
    - the ENGINE-local build cache root ``~/.cache/optio-antigravity`` (a
      parameter of the shared function, never hardcoded inside it, so tests
      isolate it — a test build must never touch the operator's real cache).

    Returns the claustrum path on the target host. Any failure RAISES
    (fail-closed): an fs-isolated session never launches unconfined.
    """
    host = hook_ctx._host
    cache_dir = await _resolve_antigravity_cache_dir(host, install_dir)
    return await claustrum.ensure_claustrum_installed(
        host,
        cache_dir=cache_dir,
        engine_cache_dir=os.path.expanduser("~/.cache/optio-antigravity"),
        report_progress=hook_ctx.report_progress,
    )


async def _build_claustrum_wrap(
    host: "Host", config: "AntigravityTaskConfig", claustrum_path: str | None,
) -> list[str] | None:
    """claustrum argv prefix for an fs-isolated launch, or None when
    ``fs_isolation`` is off. Shared by the iframe (ttyd/tmux) and conversation
    (``agy -p`` under a PTY) launch paths. Host-type agnostic (workdir + generic
    primitives only), so the wrap is identical local and remote."""
    if not config.fs_isolation:
        return None
    from . import fs_allowlist

    cache_dir = await _resolve_antigravity_cache_dir(host, config.agy_install_dir)
    # ``~/`` caller extras expand against the REAL host home (the agy process
    # runs under an isolated $HOME, and grants reach claustrum verbatim).
    host_home = (
        await host.resolve_host_home() if config.extra_allowed_dirs else None
    )
    grants = fs_allowlist.build_grant_flags(
        workdir=host.workdir,
        agy_cache_dir=cache_dir,
        extra_allowed_dirs=config.extra_allowed_dirs,
        host_home=host_home,
    )
    return [claustrum_path, "--best-effort", "--abi-min", "1", *grants, "--"]


async def claustrum_newer_tag() -> str | None:
    """Return the newest claustrum tag if it is newer than the pinned one, else None.

    Engine-side egress only. Best-effort: network failure returns None.
    """
    try:
        p = await asyncio.create_subprocess_exec(
            "git", "ls-remote", "--tags", "--refs", claustrum.CLAUSTRUM_REPO,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await p.communicate()
        if p.returncode != 0:
            return None
    except Exception:  # noqa: BLE001
        return None
    tags = []
    for line in out.decode().splitlines():
        ref = line.rsplit("/", 1)[-1].strip()
        if ref.startswith("v"):
            tags.append(ref)

    def key(t: str) -> tuple:
        return tuple(int(x) for x in t.lstrip("v").split(".") if x.isdigit())

    if not tags:
        return None
    newest = max(tags, key=key)
    return newest if key(newest) > key(claustrum.CLAUSTRUM_PINNED_TAG) else None
