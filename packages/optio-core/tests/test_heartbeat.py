"""Heartbeat publishing — covers what test_integration.py used to."""

import asyncio
import time

import pytest
from redis.asyncio import Redis

from optio_core.lifecycle import Optio


@pytest.mark.asyncio
async def test_heartbeat_key_set_during_run(mongo_db, redis_url):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="hbtest", redis_url=redis_url)

    run_task = asyncio.create_task(fw.run())
    try:
        # Heartbeat loop ticks every 5s. Poll for the key rather than
        # sleeping a fixed margin — under CPU starvation the first tick can
        # land well past 6s of wall-clock, so wait on the observable write.
        redis = Redis.from_url(redis_url)
        try:
            key = f"{mongo_db.name}/hbtest:heartbeat"
            deadline = time.monotonic() + 60.0
            value = None
            while time.monotonic() < deadline:
                value = await redis.get(key)
                if value is not None:
                    break
                await asyncio.sleep(0.1)
            assert value is not None, (
                f"Heartbeat key {key!r} not set; expected the heartbeat loop "
                f"to have written it by now"
            )
        finally:
            await redis.aclose()
    finally:
        await fw.shutdown()
        try:
            await run_task
        except asyncio.CancelledError:
            pass
