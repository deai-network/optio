"""Shared claustrum (Landlock fs-sandbox) provisioning for optio agent wrappers.

claustrum is a standalone Landlock sandbox CLI: it applies a filesystem allowlist
to itself, then ``execve``'s the wrapped target, so the agent and every tool
subprocess it spawns inherit the confinement. This module is the single source of
truth for **provisioning** the binary — cross-compile it on the engine, place it on
the (possibly remote) target host, and VALIDATE it. The per-wrapper **grant set**
(which dirs, and any netns/pasta hop) stays in each wrapper's own
``_build_claustrum_wrap``.

Fail-closed: any provisioning failure RAISES, so an fs-isolated session never
launches unconfined.

**Why the validation is a functional probe, not ``--version``.** A bare
``<claustrum> --version`` check cannot distinguish a real claustrum from a
shell-script stub — ``/bin/sh --version`` also exits 0. A stub that passes that
check then silently no-ops the launch: the agent is wrapped as
``/bin/sh --best-effort … -- <agent> …``, ``/bin/sh`` chokes on the flags, the
agent never execs, and the surface fails with a bare "exited before ready" and NO
output. :func:`claustrum_works` instead has claustrum actually wrap+exec a sentinel
echo, which only a functioning claustrum passes. :func:`is_elf` additionally guards
the engine build cache so a non-binary placeholder is rebuilt, never shipped.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import tempfile
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from optio_host import Host

CLAUSTRUM_REPO = "https://github.com/deai-network/claustrum"
CLAUSTRUM_PINNED_TAG = "v0.1.1"

# uname -m -> Go GOARCH.
_GOARCH_BY_UNAME = {"x86_64": "amd64", "aarch64": "arm64"}

# Sentinel echoed through a claustrum wrap by claustrum_works. Distinctive so it
# cannot collide with incidental output.
_PROBE_SENTINEL = "__optio_claustrum_ok__"


async def detect_goarch(host: "Host") -> str:
    """Map the host's ``uname -m`` to a Go GOARCH (Linux only). Raises on a
    non-Linux host or an unsupported architecture."""
    r_os = await host.run_command("uname -s")
    if r_os.exit_code != 0 or (r_os.stdout or "").strip() != "Linux":
        raise RuntimeError(
            f"claustrum requires a Linux host (uname -s={(r_os.stdout or '').strip()!r})."
        )
    r = await host.run_command("uname -m")
    if r.exit_code != 0:
        raise RuntimeError(
            f"uname -m failed (exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
        )
    arch = (r.stdout or "").strip()
    goarch = _GOARCH_BY_UNAME.get(arch)
    if goarch is None:
        raise RuntimeError(
            f"unsupported host arch {arch!r} for claustrum (supported: "
            f"{sorted(_GOARCH_BY_UNAME)})."
        )
    return goarch


async def build_claustrum_on_engine(goarch: str, tag: str, dest: str) -> None:
    """Clone claustrum at ``tag`` and cross-compile a static binary to ``dest``.

    Runs ON THE ENGINE (where git + the Go toolchain live), never on an ssh
    target. ``dest`` is an engine-local path.
    """
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="claustrum-src-") as src:
        clone = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", "--branch", tag, CLAUSTRUM_REPO, src,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await clone.communicate()
        if clone.returncode != 0:
            raise RuntimeError(f"git clone claustrum {tag} failed: {out.decode()[:400]}")
        env = {**os.environ, "CGO_ENABLED": "0", "GOOS": "linux", "GOARCH": goarch}
        build = await asyncio.create_subprocess_exec(
            "go", "build", "-trimpath", "-ldflags", "-s -w", "-o", dest, ".",
            cwd=src, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await build.communicate()
        if build.returncode != 0:
            raise RuntimeError(f"go build claustrum ({goarch}) failed: {out.decode()[:400]}")


def is_elf(path: str) -> bool:
    """True iff the engine-local file starts with the ELF magic — a real compiled
    binary, not a ``#!/bin/sh`` stub. Guards the engine build cache so a poisoned or
    placeholder file is rebuilt rather than shipped to the target host."""
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"\x7fELF"
    except OSError:
        return False


async def claustrum_works(host: "Host", path: str) -> bool:
    """True iff ``path`` is a FUNCTIONING claustrum, proven by having it wrap and
    exec a trivial target and pass the sentinel through.

    A shell-script stub (which a bare ``--version`` cannot catch) chokes on the
    claustrum flags and never echoes the sentinel; so does any non-claustrum
    binary. ``--best-effort`` keeps the probe runnable where the kernel cannot
    enforce Landlock."""
    probe = await host.run_command(
        f"{shlex.quote(path)} --best-effort --abi-min 1 "
        f"--rox /usr --rox /bin --rox /lib --rox /lib64 "
        f"-- /bin/echo {_PROBE_SENTINEL} 2>/dev/null || true"
    )
    return _PROBE_SENTINEL in (probe.stdout or "")


async def ensure_claustrum_installed(
    host: "Host",
    *,
    cache_dir: str,
    engine_cache_dir: str,
    tag: str = CLAUSTRUM_PINNED_TAG,
    report_progress: Optional[Callable[[Optional[float], str], None]] = None,
) -> str:
    """Ensure a functioning claustrum binary (``tag``, host arch) is on the target
    host, and return its path. Fail-closed — any failure RAISES.

    - ``cache_dir``: the TARGET-host cache dir (resolved by the caller; the binary
      lands at ``<cache_dir>/claustrum/<tag>/<goarch>/claustrum``, beside the agent
      binary cache, outside every task workdir).
    - ``engine_cache_dir``: the ENGINE-local cache root for the cross-compiled
      build (e.g. ``~/.cache/optio-<agent>``). A PARAMETER (not hardcoded) so tests
      isolate it — a test build must never touch the operator's real cache.
    - ``report_progress``: optional ``(pct, message)`` callback for the UI.

    Validation is functional (:func:`claustrum_works`), not ``--version``, at both
    the cache-hit and post-place checks; the engine build cache is ELF-guarded
    (:func:`is_elf`). A stray stub is therefore rebuilt, and a non-functioning
    binary is refused rather than silently no-op'ing the launch.
    """
    goarch = await detect_goarch(host)
    target_path = f"{cache_dir}/claustrum/{tag}/{goarch}/claustrum"

    # Already on the target host AND actually functions as claustrum?
    probe = await host.run_command(
        f"test -x {shlex.quote(target_path)} && echo present || true"
    )
    if "present" in (probe.stdout or "") and await claustrum_works(host, target_path):
        return target_path

    if report_progress is not None:
        report_progress(None, "Preparing claustrum (filesystem isolation)…")

    engine_cache = f"{engine_cache_dir.rstrip('/')}/claustrum/{tag}/{goarch}/claustrum"
    # Rebuild if the engine cache is missing OR not a real compiled binary.
    if not os.path.exists(engine_cache) or not is_elf(engine_cache):
        await build_claustrum_on_engine(goarch, tag, engine_cache)

    await host.put_file_to_host(engine_cache, target_path)
    r = await host.run_command(f"chmod +x {shlex.quote(target_path)}")
    if r.exit_code != 0:
        raise RuntimeError(
            f"chmod +x claustrum failed (exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
        )
    # Fail-closed: verify the PLACED binary actually functions as claustrum.
    if not await claustrum_works(host, target_path):
        raise RuntimeError(
            f"claustrum placed at {target_path!r} is not a functioning claustrum "
            f"(failed a wrapped-exec probe — a stub or wrong binary would silently "
            f"no-op the launch). Refusing to launch unconfined."
        )
    return target_path
