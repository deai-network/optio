"""Tests for Redis-free mode."""

import asyncio

import pytest
from feldwebel.lifecycle import Feldwebel


@pytest.mark.asyncio
async def test_init_without_redis(mongo_db):
    """Init succeeds without redis_url."""
    fw = Feldwebel()
    await fw.init(mongo_db=mongo_db, prefix="test_no_redis")

    assert fw._config is not None
    assert fw._config.redis_url is None
    assert fw._redis is None
    assert fw._consumer is None
    assert fw._executor is not None
    assert fw._scheduler is not None


@pytest.mark.asyncio
async def test_run_and_shutdown_without_redis(mongo_db):
    """run() blocks until shutdown() is called, without Redis."""
    fw = Feldwebel()
    await fw.init(mongo_db=mongo_db, prefix="test_no_redis_run")

    shutdown_called = False

    async def shutdown_after_delay():
        nonlocal shutdown_called
        await asyncio.sleep(0.2)
        shutdown_called = True
        await fw.shutdown()

    asyncio.create_task(shutdown_after_delay())
    await fw.run()

    assert shutdown_called
