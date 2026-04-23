"""Architecture detection + binary resolution for our patched opencode fork.

Until the iframe-embeddability fixes are upstreamed, optio-opencode needs
to install a patched opencode binary rather than the one the upstream
installer fetches.  This module handles:

1. Detecting the target host's OS / arch / libc / CPU capability triple.
2. Mapping that to the directory names opencode's build process emits
   (e.g. ``opencode-linux-x64-baseline-musl``).

The detection logic is ported from the upstream installer script
(``install`` at the root of the opencode repo) so our variant-selection
matches upstream's.

The actual upload / install-on-host mechanics live in ``host.py`` (each
Host implementation handles its own filesystem access).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OpencodeTarget:
    """Opencode build target triple — matches opencode's build output layout."""

    os: str  # 'linux' | 'darwin' | 'windows'
    arch: str  # 'x64' | 'arm64'
    baseline: bool = False  # x64 without AVX2
    musl: bool = False  # Linux with musl libc (Alpine etc.)

    @property
    def directory_name(self) -> str:
        """Subdirectory name under the binary-dir root.

        Matches opencode's build output (see
        ``packages/opencode/script/build.ts``): ``opencode-<os>-<arch>``
        with optional ``-baseline`` and ``-musl`` suffixes, in that order.
        """
        name = f"opencode-{self.os}-{self.arch}"
        if self.baseline:
            name += "-baseline"
        if self.musl:
            name += "-musl"
        return name


_SUPPORTED_COMBOS = {
    ("linux", "x64"),
    ("linux", "arm64"),
    ("darwin", "x64"),
    ("darwin", "arm64"),
    ("windows", "x64"),
}


def normalize_os(raw: str) -> str:
    """``uname -s`` / platform string → opencode's os token."""
    lower = raw.strip().lower()
    if lower.startswith("darwin"):
        return "darwin"
    if lower.startswith("linux"):
        return "linux"
    for prefix in ("mingw", "msys", "cygwin", "windows"):
        if lower.startswith(prefix):
            return "windows"
    return lower


def normalize_arch(raw: str) -> str:
    """``uname -m`` / platform string → opencode's arch token."""
    m = raw.strip().lower()
    if m in ("aarch64", "arm64"):
        return "arm64"
    if m in ("x86_64", "x64", "amd64"):
        return "x64"
    return m


def is_supported(os_: str, arch: str) -> bool:
    return (os_, arch) in _SUPPORTED_COMBOS


def make_target(
    os_: str,
    arch: str,
    *,
    rosetta: bool = False,
    musl: bool = False,
    baseline: bool = False,
) -> OpencodeTarget:
    """Compose an :class:`OpencodeTarget` from raw platform facts.

    ``rosetta=True`` on ``darwin`` x64 flips the arch to ``arm64`` to match
    upstream's behavior of preferring native Apple Silicon binaries when
    the user's Terminal is running under Rosetta.
    """
    if os_ == "darwin" and arch == "x64" and rosetta:
        arch = "arm64"
    if not is_supported(os_, arch):
        raise ValueError(f"unsupported OS/arch combination: {os_}/{arch}")
    # musl and baseline only mean anything on Linux x64 / x64 generally.
    return OpencodeTarget(os=os_, arch=arch, baseline=baseline, musl=musl)
