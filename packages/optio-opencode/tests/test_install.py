"""Tests for optio_opencode.install — target detection + directory naming."""

import os
import stat
import tempfile

import pytest

from optio_opencode.host import LocalHost
from optio_opencode.install import (
    OpencodeTarget,
    is_supported,
    make_target,
    normalize_arch,
    normalize_os,
)


def test_directory_name_plain_linux_x64():
    t = OpencodeTarget(os="linux", arch="x64")
    assert t.directory_name == "opencode-linux-x64"


def test_directory_name_linux_x64_baseline():
    t = OpencodeTarget(os="linux", arch="x64", baseline=True)
    assert t.directory_name == "opencode-linux-x64-baseline"


def test_directory_name_linux_x64_musl():
    t = OpencodeTarget(os="linux", arch="x64", musl=True)
    assert t.directory_name == "opencode-linux-x64-musl"


def test_directory_name_linux_x64_baseline_musl_order():
    t = OpencodeTarget(os="linux", arch="x64", baseline=True, musl=True)
    # Suffix order matters — matches opencode's build output.
    assert t.directory_name == "opencode-linux-x64-baseline-musl"


def test_directory_name_darwin_arm64():
    t = OpencodeTarget(os="darwin", arch="arm64")
    assert t.directory_name == "opencode-darwin-arm64"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Darwin", "darwin"),
        ("Linux", "linux"),
        ("MINGW64_NT-10.0", "windows"),
        ("MSYS_NT-10.0", "windows"),
        ("CYGWIN_NT-10.0", "windows"),
        (" linux\n", "linux"),
    ],
)
def test_normalize_os(raw, expected):
    assert normalize_os(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("aarch64", "arm64"),
        ("arm64", "arm64"),
        ("x86_64", "x64"),
        ("amd64", "x64"),
        ("x64", "x64"),
        ("X86_64", "x64"),
    ],
)
def test_normalize_arch(raw, expected):
    assert normalize_arch(raw) == expected


def test_is_supported_known_combos():
    assert is_supported("linux", "x64")
    assert is_supported("linux", "arm64")
    assert is_supported("darwin", "x64")
    assert is_supported("darwin", "arm64")
    assert is_supported("windows", "x64")


def test_is_supported_rejects_unknown():
    assert not is_supported("linux", "mips")
    assert not is_supported("windows", "arm64")  # intentional — see _SUPPORTED_COMBOS


def test_make_target_rosetta_flips_darwin_x64_to_arm64():
    t = make_target("darwin", "x64", rosetta=True)
    assert t.arch == "arm64"
    assert t.os == "darwin"


def test_make_target_no_rosetta_on_linux():
    # rosetta=True on Linux should be a no-op.
    t = make_target("linux", "x64", rosetta=True)
    assert t.arch == "x64"


def test_make_target_rejects_unsupported():
    with pytest.raises(ValueError):
        make_target("plan9", "mips")


async def test_local_host_detect_target_matches_platform(tmp_workdir):
    host = LocalHost(workdir=tmp_workdir)
    t = await host.detect_target()
    # The combination must be supported.
    assert is_supported(t.os, t.arch), (t.os, t.arch)
    # Sanity: on CI / dev boxes the directory_name should be parseable back.
    assert t.directory_name.startswith(f"opencode-{t.os}-{t.arch}")


async def test_local_host_install_opencode_binary_sets_opencode_cmd(tmp_workdir):
    host = LocalHost(workdir=tmp_workdir)
    # Create a dummy executable and install it.
    dummy = os.path.join(tmp_workdir, "my-opencode")
    with open(dummy, "w") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    os.chmod(dummy, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
    await host.install_opencode_binary(dummy)
    assert host._opencode_cmd == [dummy]


async def test_local_host_install_opencode_binary_raises_on_missing(tmp_workdir):
    host = LocalHost(workdir=tmp_workdir)
    with pytest.raises(RuntimeError, match="not found"):
        await host.install_opencode_binary("/nonexistent/path/to/opencode")
