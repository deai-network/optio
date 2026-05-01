import asyncio
import os
import sys

import pytest

from optio_host.host import LocalHost
from optio_opencode import host_actions


FAKE_OPENCODE = os.path.join(os.path.dirname(__file__), "fake_opencode.py")
FAKE_EXEC = f"{sys.executable} {FAKE_OPENCODE}"


@pytest.fixture
def local_host(tmp_workdir):
    # tmp_workdir is the per-test pytest tmp directory; we use it as the
    # host's taskdir. The host then derives workdir = taskdir/workdir.
    return LocalHost(taskdir=tmp_workdir)


async def test_setup_workdir_creates_workdir(local_host):
    await local_host.setup_workdir()
    assert os.path.isdir(local_host.workdir)
    # As of the optio-host split, setup_workdir mkdirs the workdir only.
    # The protocol-specific deliverables/ + optio.log are owned by the
    # protocol session driver in optio_host.protocol.session.


@pytest.mark.asyncio
async def test_setup_workdir_sets_taskdir_and_workdir_mode_0o700(local_host):
    """taskdir + workdir must be operator-private (0o700) so that opencode.db
    (transcript) and workdir/.env are not readable by other UNIX users."""
    await local_host.setup_workdir()
    import os
    assert (os.stat(local_host.taskdir).st_mode & 0o777) == 0o700
    assert (os.stat(local_host.workdir).st_mode & 0o777) == 0o700


async def test_write_text_writes_utf8(local_host):
    await local_host.setup_workdir()
    await local_host.write_text("AGENTS.md", "héllo")
    with open(os.path.join(local_host.workdir, "AGENTS.md"), encoding="utf-8") as fh:
        assert fh.read() == "héllo"


async def test_launch_prints_url_and_reports_port(local_host):
    await local_host.setup_workdir()
    handle, port = await host_actions.launch_opencode(
        local_host,
        password="unused-by-fake",
        ready_timeout_s=5.0,
        opencode_executable=f"{FAKE_EXEC} --scenario sleep_forever",
    )
    try:
        assert 1024 <= port < 65536
    finally:
        await local_host.terminate_subprocess(handle, aggressive=True)


@pytest.mark.skip(reason="launch_opencode now hardcodes opencode-web cmd assembly; "
                  "arbitrary-executable substitution rework pending")
async def test_launch_times_out_on_no_url(tmp_path):
    pass


async def test_tail_file_yields_appended_lines(local_host):
    await local_host.setup_workdir()
    log_path = os.path.join(local_host.workdir, "optio.log")
    # setup_workdir no longer creates optio.log; the protocol driver does.
    # The test exercises tail_file directly, so create the file ourselves.
    open(log_path, "a", encoding="utf-8").close()

    async def append_later():
        await asyncio.sleep(0.05)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write("hello\n")
            fh.flush()

    task = asyncio.create_task(append_later())
    collected: list[str] = []

    async def _collect() -> None:
        async for line in local_host.tail_file(log_path):
            collected.append(line)
            if collected == ["hello"]:
                break

    await asyncio.wait_for(_collect(), timeout=5.0)
    await task
    assert collected == ["hello"]


async def test_fetch_deliverable_text(local_host):
    from optio_host.protocol.session import fetch_deliverable_text
    await local_host.setup_workdir()
    os.makedirs(os.path.join(local_host.workdir, "deliverables"), exist_ok=True)
    target = os.path.join(local_host.workdir, "deliverables", "a.txt")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write("contents")
    assert await fetch_deliverable_text(local_host, target) == "contents"


async def test_fetch_deliverable_non_utf8_raises(local_host):
    from optio_host.protocol.session import fetch_deliverable_text
    await local_host.setup_workdir()
    os.makedirs(os.path.join(local_host.workdir, "deliverables"), exist_ok=True)
    target = os.path.join(local_host.workdir, "deliverables", "b.bin")
    with open(target, "wb") as fh:
        fh.write(b"\xff\xfe\x00")
    with pytest.raises(UnicodeDecodeError):
        await fetch_deliverable_text(local_host, target)


async def test_cleanup_taskdir_removes_directory(local_host, tmp_workdir):
    await local_host.setup_workdir()
    # tmp_workdir is the host's taskdir; cleanup_taskdir wipes the whole thing.
    await local_host.cleanup_taskdir(aggressive=False)
    assert not os.path.exists(tmp_workdir)
