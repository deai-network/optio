"""Tests for RemoteHost resume-related actions (now in host_actions).

Skipped automatically when the docker-compose.sshd.yml SSH harness is unavailable
(i.e. when Docker is not on PATH).

The SSH container runs opencode-shim.sh, which is:

    #!/bin/sh
    exec python3 /usr/local/bin/fake_opencode.py "$@" --scenario happy

fake_opencode.py handles `import` and `export` subcommands before it reaches
any scenario logic, so those round-trips work over SSH without any shim
modifications.  The `--env-dump` hook used in the LocalHost env-propagation
test cannot be wired through the SSH launch path.
"""

from __future__ import annotations

import io
import json
import tarfile

import pytest
import pytest_asyncio

from optio_host.host import RemoteHost
from optio_opencode import host_actions
from optio_opencode.types import SSHConfig


from optio_host.testing import have_docker

# The isolation-safe ``sshd`` fixture lives in conftest.py.
pytestmark = pytest.mark.skipif(
    not have_docker(), reason="Docker not available"
)


@pytest_asyncio.fixture
async def remote_host(sshd):
    """A connected RemoteHost with a stable, fresh taskdir."""
    taskdir = f"/tmp/optio-test-resume-{id(object())}"
    host = RemoteHost(
        ssh_config=SSHConfig(
            host=sshd["host"],
            user=sshd["user"],
            key_path=sshd["key_path"],
            port=sshd["port"],
        ),
        taskdir=taskdir,
    )
    await host.connect()
    await host.setup_workdir()
    yield host
    await host.cleanup_taskdir(aggressive=False)
    await host.disconnect()


# TODO: substitution mechanism needs rework post-host-split — see the
# corresponding skipped test in test_host_resume.py.
@pytest.mark.skip(
    reason="substitution mechanism needs rework post-host-split"
)
async def test_remote_launch_env_is_propagated(remote_host: RemoteHost):
    pass


async def test_remote_opencode_export_then_import_roundtrip(remote_host: RemoteHost):
    """export then import via the SSH fake roundtrips correctly.

    The opencode-shim.sh passes all args to fake_opencode.py, which handles
    `import` and `export` subcommands synchronously before any scenario logic.
    """
    db_path = f"{remote_host.workdir}/opencode.db"
    seed = json.dumps({"id": "sess-remote-42", "messages": []}).encode("utf-8")

    # The shim is on PATH inside the container as `opencode`.
    await host_actions.opencode_import(
        remote_host, db_path, seed, opencode_executable="opencode",
    )

    out = await host_actions.opencode_export(
        remote_host, db_path, "sess-remote-42", opencode_executable="opencode",
    )
    decoded = json.loads(out.decode("utf-8"))
    assert decoded["id"] == "sess-remote-42"


async def test_remote_archive_workdir_yields_chunks(remote_host: RemoteHost):
    """archive_workdir streams at least one non-empty chunk of tar data."""
    await remote_host.write_text("AGENTS.md", "# instructions\n")
    await remote_host.write_text("data.txt", "payload\n")

    chunks = []
    async for chunk in remote_host.archive_workdir(exclude=None):
        chunks.append(chunk)

    assert len(b"".join(chunks)) > 0, "expected at least some tar bytes"


async def test_remote_archive_over_the_cap_leaves_out_the_listed_paths(
    remote_host: RemoteHost, monkeypatch,
):
    """Over the capture cap, the archive succeeds on the sshd image's BusyBox
    tar and leaves out exactly what regenerable.txt lists, taken literally."""
    monkeypatch.setenv("OPTIO_WORKDIR_CAPTURE_MAX_MB", "1")
    probe = await remote_host._conn.run("tar --version 2>&1 | head -1", check=False)
    assert "busybox" in (probe.stdout or "").lower(), probe.stdout
    setup = await remote_host._conn.run(
        f"cd {remote_host.workdir} && mkdir -p big pkg/big 'build[1]' build1 && "
        "head -c 2097152 /dev/zero > big/blob.bin && echo k > keep.txt && "
        "echo p > pkg/big/p.txt && echo b > 'build[1]/b' && echo c > build1/c && "
        "printf 'big\\nbuild[1]\\n' > regenerable.txt",
        check=False,
    )
    assert setup.exit_status == 0, setup.stderr
    notes: list[tuple[str, str]] = []

    blob = b"".join([
        chunk async for chunk in remote_host.archive_workdir(
            exclude=[".git"], on_note=lambda note, level: notes.append((note, level)),
        )
    ])

    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        files = {m.name.removeprefix("./") for m in tf.getmembers() if m.isfile()}
    assert files == {"keep.txt", "regenerable.txt", "pkg/big/p.txt", "build1/c"}
    assert notes == [
        ("workdir 2 MB (cap 1 MB): left out 2 paths listed in regenerable.txt", "info"),
    ]


async def test_remote_restore_workdir_empties_then_extracts(sshd):
    """archive from one workdir, restore into a second, verify content and staleness."""
    ssh = SSHConfig(
        host=sshd["host"],
        user=sshd["user"],
        key_path=sshd["key_path"],
        port=sshd["port"],
    )

    src_taskdir = f"/tmp/optio-test-resume-src-{id(object())}"
    dst_taskdir = f"/tmp/optio-test-resume-dst-{id(object())}"

    src = RemoteHost(ssh_config=ssh, taskdir=src_taskdir)
    dst = RemoteHost(ssh_config=ssh, taskdir=dst_taskdir)

    await src.connect()
    await dst.connect()

    try:
        await src.setup_workdir()
        await src.write_text("AGENTS.md", "# instructions\n")
        await src.write_text("data.txt", "payload\n")

        # Setup dst with a stale file that restore should remove.
        await dst.setup_workdir()
        await dst.write_text("stale.txt", "zzz\n")

        # Archive src → chunks.
        chunks = []
        async for chunk in src.archive_workdir(exclude=None):
            chunks.append(chunk)

        async def replay():
            for c in chunks:
                yield c

        await dst.restore_workdir(replay())

        # Verify stale file is gone and src content is present.
        r_stale = await dst._conn.run(
            f"test -f {dst.workdir}/stale.txt && echo yes || echo no",
            check=False,
        )
        assert (r_stale.stdout or "").strip() == "no", "stale.txt should have been removed"

        r_agents = await dst._conn.run(
            f"cat {dst.workdir}/AGENTS.md",
            check=False,
        )
        assert (r_agents.stdout or "").strip() == "# instructions"

    finally:
        await src.cleanup_taskdir(aggressive=False)
        await dst.cleanup_taskdir(aggressive=False)
        await src.disconnect()
        await dst.disconnect()


async def test_remote_remove_file_is_idempotent(remote_host: RemoteHost):
    """remove_file does not raise when the file is absent (rm -f semantics)."""
    absent = f"{remote_host.workdir}/no-such-file.txt"
    # Should not raise.
    await remote_host.remove_file(absent)

    # Also verify it removes an existing file.
    await remote_host.write_text("to-remove.txt", "bye\n")
    await remote_host.remove_file(f"{remote_host.workdir}/to-remove.txt")
    r = await remote_host._conn.run(
        f"test -f {remote_host.workdir}/to-remove.txt && echo yes || echo no",
        check=False,
    )
    assert (r.stdout or "").strip() == "no"
