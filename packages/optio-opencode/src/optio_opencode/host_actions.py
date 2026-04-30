"""Opencode-specific actions over a generic Host.

Each function takes ``Host`` (from optio_host) as the first argument and
uses only generic primitives (``run_command``, ``put_file_to_host``,
``write_text``, etc.) plus opencode-shaped state-passing.

Per the optio-host split spec, opencode actions become free functions
rather than Host methods so optio-host's Host interface stays generic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from optio_opencode.install import (
    OpencodeTarget,
    make_target,
    normalize_arch,
    normalize_os,
)

if TYPE_CHECKING:
    from optio_host.host import Host


async def detect_target(host: "Host") -> OpencodeTarget:
    """Detect the opencode build target (os/arch/musl/baseline) for ``host``.

    Uniform local + remote implementation via ``host.run_command`` —
    shells out to ``uname -s``, ``uname -m``, /etc/alpine-release test,
    ``ldd --version``, /proc/cpuinfo, and macOS ``sysctl`` probes. Mirrors
    opencode's upstream installer logic so the resulting triple maps
    directly to a build-output subdirectory name.
    """
    uname_s = (await host.run_command("uname -s")).stdout
    uname_m = (await host.run_command("uname -m")).stdout
    os_ = normalize_os(uname_s)
    arch = normalize_arch(uname_m)

    musl = False
    rosetta = False
    baseline = False

    if os_ == "linux":
        alpine = await host.run_command("test -f /etc/alpine-release")
        if alpine.exit_code == 0:
            musl = True
        else:
            ldd = await host.run_command("ldd --version 2>&1 || true")
            if "musl" in ldd.stdout.lower():
                musl = True
        if arch == "x64":
            cpuinfo = await host.run_command(
                "cat /proc/cpuinfo 2>/dev/null || true",
            )
            haystack = " " + cpuinfo.stdout.lower() + " "
            if cpuinfo.exit_code != 0 or " avx2 " not in haystack:
                baseline = True
    elif os_ == "darwin":
        if arch == "x64":
            ros = await host.run_command(
                "sysctl -n sysctl.proc_translated 2>/dev/null || echo 0",
            )
            rosetta = ros.stdout.strip() == "1"
            avx2 = await host.run_command(
                "sysctl -n hw.optional.avx2_0 2>/dev/null || echo 0",
            )
            baseline = avx2.stdout.strip() != "1"

    return make_target(os_, arch, rosetta=rosetta, musl=musl, baseline=baseline)
