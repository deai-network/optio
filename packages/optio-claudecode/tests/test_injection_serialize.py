"""serialized() prevents two injection bursts from overlapping."""
import asyncio

from optio_claudecode.input_listener import serialized


async def test_serialized_no_interleave():
    lock = asyncio.Lock()
    active = 0
    max_active = 0

    async def raw_send(text):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)  # force overlap if unlocked
        active -= 1

    send = serialized(lock, raw_send)
    await asyncio.gather(*(send(f"m{i}") for i in range(5)))
    assert max_active == 1  # never two bursts at once
