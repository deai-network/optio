import asyncio
import os
import sys

import pytest

from optio_opencode.host import LocalHost


FAKE_OPENCODE = os.path.join(os.path.dirname(__file__), "fake_opencode.py")


@pytest.fixture
def local_host(tmp_workdir):
    return LocalHost(
        workdir=tmp_workdir,
        opencode_cmd=[sys.executable, FAKE_OPENCODE],
    )


async def test_setup_workdir_creates_expected_layout(local_host, tmp_workdir):
    await local_host.setup_workdir()
    assert os.path.isdir(os.path.join(tmp_workdir, "deliverables"))
    assert os.path.isfile(os.path.join(tmp_workdir, "optio.log"))


async def test_write_text_writes_utf8(local_host, tmp_workdir):
    await local_host.setup_workdir()
    await local_host.write_text("AGENTS.md", "héllo")
    with open(os.path.join(tmp_workdir, "AGENTS.md"), encoding="utf-8") as fh:
        assert fh.read() == "héllo"


async def test_launch_prints_url_and_reports_port(local_host):
    await local_host.setup_workdir()
    proc = await local_host.launch_opencode(
        password="unused-by-fake",
        ready_timeout_s=5.0,
        extra_args=["--scenario", "sleep_forever"],
    )
    try:
        assert 1024 <= proc.opencode_port < 65536
    finally:
        await local_host.terminate_opencode(proc, aggressive=True)


async def test_launch_times_out_on_no_url():
    # Use /bin/sleep — it never prints a URL.  readiness should time out.
    host = LocalHost(
        workdir="/tmp",
        opencode_cmd=["/bin/sleep", "60"],
    )
    with pytest.raises(TimeoutError):
        await host.launch_opencode(
            password="x", ready_timeout_s=0.5, extra_args=[]
        )


async def test_tail_log_yields_appended_lines(local_host, tmp_workdir):
    await local_host.setup_workdir()

    async def append_later():
        await asyncio.sleep(0.05)
        with open(os.path.join(tmp_workdir, "optio.log"), "a", encoding="utf-8") as fh:
            fh.write("hello\n")
            fh.flush()

    task = asyncio.create_task(append_later())
    collected: list[str] = []

    async def _collect() -> None:
        async for line in local_host.tail_log():
            collected.append(line)
            if collected == ["hello"]:
                break

    await asyncio.wait_for(_collect(), timeout=5.0)
    await task
    assert collected == ["hello"]


async def test_fetch_deliverable_text(local_host, tmp_workdir):
    await local_host.setup_workdir()
    target = os.path.join(tmp_workdir, "deliverables", "a.txt")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write("contents")
    assert await local_host.fetch_deliverable_text(target) == "contents"


async def test_fetch_deliverable_non_utf8_raises(local_host, tmp_workdir):
    await local_host.setup_workdir()
    target = os.path.join(tmp_workdir, "deliverables", "b.bin")
    with open(target, "wb") as fh:
        fh.write(b"\xff\xfe\x00")
    with pytest.raises(UnicodeDecodeError):
        await local_host.fetch_deliverable_text(target)


async def test_cleanup_workdir_removes_directory(local_host, tmp_workdir):
    await local_host.setup_workdir()
    await local_host.cleanup_workdir(aggressive=False)
    assert not os.path.exists(tmp_workdir)
