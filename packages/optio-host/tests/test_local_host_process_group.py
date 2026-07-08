"""terminate_subprocess must reap the whole process group, not just the
tracked pid.

Mirrors the optio-claudecode orphan bug: the launched `/bin/sh` forks a
child (in our real case ttyd) that survives a single-pid SIGKILL and is
reparented to init. With start_new_session=True at launch + killpg at
terminate, the child dies with its group.
"""

import asyncio
import os

import pytest

from optio_host.host import LocalHost


@pytest.fixture
def localhost(tmp_path):
    taskdir = tmp_path / "task"
    taskdir.mkdir()
    (taskdir / "workdir").mkdir()
    return LocalHost(taskdir=str(taskdir))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


async def _read_child_pid(handle) -> int:
    """First stdout line is 'CHILD <pid>'."""
    # Generous hang-ceiling: we wait for the EVENT (first line), not a
    # duration. 60s only bounds a true hang so this survives CPU starvation.
    line = await asyncio.wait_for(handle.stdout.__anext__(), timeout=60.0)
    text = line.decode().strip()
    assert text.startswith("CHILD "), f"unexpected first line: {text!r}"
    return int(text.split()[1])


@pytest.mark.parametrize("aggressive", [True, False])
async def test_terminate_reaps_child_in_process_group(localhost, aggressive):
    # sh backgrounds a long-lived child (same process group), prints its
    # pid, then `wait`s so sh stays alive until terminated.
    handle = await localhost.launch_subprocess(
        "sleep 300 & echo CHILD $!; wait",
    )
    child_pid = await _read_child_pid(handle)
    assert _alive(child_pid), "child should be running before terminate"

    await localhost.terminate_subprocess(handle, aggressive=aggressive)

    # The backgrounded child must be gone — terminate kills the group.
    # Poll for the observable event (child no longer alive) under a generous
    # hang-ceiling; the loop bounds a true hang, not the expected latency.
    deadline = asyncio.get_event_loop().time() + 60.0
    while _alive(child_pid) and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
    assert not _alive(child_pid), (
        f"child pid {child_pid} survived terminate_subprocess "
        f"(aggressive={aggressive}) — process group not reaped"
    )
