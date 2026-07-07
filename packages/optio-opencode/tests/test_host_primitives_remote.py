"""Integration tests for RemoteHost new primitives (Docker-gated)."""

from __future__ import annotations

import asyncio
import shlex

import pytest
import pytest_asyncio

from optio_host.host import RemoteHost
from optio_opencode.types import SSHConfig


from optio_host.testing import have_docker

# The isolation-safe ``sshd`` fixture lives in conftest.py (shared by all
# remote modules, session-scoped, per-worker compose project + ephemeral port).
pytestmark = pytest.mark.skipif(not have_docker(), reason="Docker not available")


@pytest_asyncio.fixture
async def remote_host(sshd):
    """A connected RemoteHost with a stable, fresh taskdir."""
    import uuid as _uuid
    h = RemoteHost(
        ssh_config=SSHConfig(
            host=sshd["host"],
            user=sshd["user"],
            key_path=sshd["key_path"],
            port=sshd["port"],
        ),
        taskdir=f"/tmp/optio-host-test-{_uuid.uuid4().hex[:12]}",
    )
    await h.connect()
    await h.setup_workdir()
    try:
        yield h
    finally:
        try:
            await h.cleanup_taskdir(aggressive=True)
        except Exception:
            pass
        await h.disconnect()


async def test_remote_run_command_captures_stdout(remote_host):
    result = await remote_host.run_command("echo hello")
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello"
    assert result.stderr == ""


async def test_remote_run_command_captures_stderr_and_exit(remote_host):
    result = await remote_host.run_command("echo oops 1>&2; exit 9")
    assert result.exit_code == 9
    assert "oops" in result.stderr


async def test_remote_run_command_default_cwd_is_workdir(remote_host):
    result = await remote_host.run_command("pwd")
    assert result.stdout.strip() == remote_host.workdir


async def test_remote_run_command_cwd_override(remote_host):
    result = await remote_host.run_command("pwd", cwd="/tmp")
    assert result.stdout.strip() == "/tmp"


async def test_remote_run_command_env(remote_host):
    result = await remote_host.run_command(
        'echo "$X"', env={"X": "yes"},
    )
    assert result.stdout.strip() == "yes"


async def test_remote_put_file_path_source(remote_host, tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"path-source-content")
    target = remote_host.workdir + "/data/out.bin"
    await remote_host.put_file_to_host(str(src), target)
    out = await remote_host.run_command(f"cat {target}")
    assert out.stdout == "path-source-content"


async def test_remote_put_file_bytes_source(remote_host):
    target = remote_host.workdir + "/x.bin"
    await remote_host.put_file_to_host(b"bytes-content", target)
    out = await remote_host.run_command(f"cat {target}")
    assert out.stdout == "bytes-content"


async def test_remote_put_file_iterator_source(remote_host):
    target = remote_host.workdir + "/y.bin"

    async def chunks():
        yield b"part1-"
        yield b"part2"

    await remote_host.put_file_to_host(chunks(), target)
    out = await remote_host.run_command(f"cat {target}")
    assert out.stdout == "part1-part2"


async def test_remote_put_file_atomic_no_tmp(remote_host):
    target = remote_host.workdir + "/atomic.bin"
    await remote_host.put_file_to_host(b"ok", target)
    ls = await remote_host.run_command(f"ls {remote_host.workdir}")
    assert ".tmp" not in ls.stdout


async def test_remote_put_file_skip_if_unchanged_target_missing(remote_host):
    target = remote_host.workdir + "/skip-missing.bin"
    await remote_host.put_file_to_host(
        b"first", target, skip_if_unchanged=True,
    )
    out = await remote_host.run_command(f"cat {target}")
    assert out.stdout == "first"


async def test_remote_put_file_skip_if_unchanged_matches(remote_host):
    target = remote_host.workdir + "/skip-match.bin"
    await remote_host.put_file_to_host(b"same", target)
    mtime1 = (await remote_host.run_command(f"stat -c %Y {target}")).stdout.strip()
    # Wait briefly to make any mtime change observable.
    await asyncio.sleep(1.1)
    await remote_host.put_file_to_host(
        b"same", target, skip_if_unchanged=True,
    )
    mtime2 = (await remote_host.run_command(f"stat -c %Y {target}")).stdout.strip()
    assert mtime1 == mtime2  # untouched


async def test_remote_put_file_skip_if_unchanged_differs(remote_host):
    target = remote_host.workdir + "/skip-diff.bin"
    await remote_host.put_file_to_host(b"OLD", target)
    await remote_host.put_file_to_host(
        b"NEW", target, skip_if_unchanged=True,
    )
    out = await remote_host.run_command(f"cat {target}")
    assert out.stdout == "NEW"


async def test_remote_put_file_iterator_skip_requires_expected_sha(remote_host):
    target = remote_host.workdir + "/iter-skip.bin"

    async def chunks():
        yield b"abc"

    with pytest.raises(ValueError, match="expected_sha256"):
        await remote_host.put_file_to_host(
            chunks(), target, skip_if_unchanged=True,
        )


async def test_remote_fetch_bytes_reads_full(remote_host):
    target = remote_host.workdir + "/rd.bin"
    await remote_host.run_command(
        f"printf 'remote-content' > {target}",
    )
    data = await remote_host.fetch_bytes_from_host(target)
    assert data == b"remote-content"


async def test_remote_fetch_bytes_missing_raises_filenotfound(remote_host):
    with pytest.raises(FileNotFoundError):
        await remote_host.fetch_bytes_from_host(
            remote_host.workdir + "/no_such",
        )


async def test_remote_resolve_host_home_resolves_and_caches(remote_host):
    home1 = await remote_host.resolve_host_home()
    assert home1.startswith("/")  # absolute
    # The harness uses linuxserver/openssh-server which sets $HOME=/config
    # for the optiotest user (not /home/optiotest).
    assert home1 == "/config"
    # Second call uses cache; just verifies same return value.
    home2 = await remote_host.resolve_host_home()
    assert home2 == home1


@pytest.mark.asyncio
async def test_remote_setup_workdir_sets_taskdir_and_workdir_mode_0o700(remote_host: RemoteHost):
    """Same invariant as the local test, asserted via stat over SSH."""
    await remote_host.setup_workdir()
    qt = shlex.quote(remote_host.taskdir)
    qw = shlex.quote(remote_host.workdir)
    res_t = await remote_host._conn.run(f"stat -c %a {qt}", check=True)
    res_w = await remote_host._conn.run(f"stat -c %a {qw}", check=True)
    assert res_t.stdout.strip() == "700", f"taskdir mode is {res_t.stdout!r}"
    assert res_w.stdout.strip() == "700", f"workdir mode is {res_w.stdout!r}"


@pytest.mark.asyncio
async def test_remote_setup_workdir_wipes_stale_workdir_but_keeps_taskdir(remote_host: RemoteHost):
    """Clean-start invariant asserted over SSH (mirrors the local test)."""
    await remote_host.setup_workdir()
    qt = shlex.quote(remote_host.taskdir)
    qw = shlex.quote(remote_host.workdir)
    await remote_host._conn.run(
        f"touch {qw}/stale.txt {qt}/opencode.db", check=True,
    )
    await remote_host.setup_workdir()
    stale = await remote_host._conn.run(f"test -e {qw}/stale.txt; echo $?")
    keep = await remote_host._conn.run(f"test -e {qt}/opencode.db; echo $?")
    assert stale.stdout.strip() == "1", "workdir was not wiped"
    assert keep.stdout.strip() == "0", "taskdir state was lost"
