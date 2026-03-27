"""Tests for Redis Stream consumer."""

import asyncio
import json
import pytest
import pytest_asyncio
from redis.asyncio import Redis

from optio.consumer import CommandConsumer


@pytest_asyncio.fixture
async def redis_client(redis_url):
    client = Redis.from_url(redis_url)
    yield client
    await client.aclose()


async def test_consumer_processes_command(redis_client):
    stream = f"test_cmd_{id(asyncio.get_event_loop())}"
    received = []

    try:
        await redis_client.delete(stream)
    except Exception:
        pass

    consumer = CommandConsumer(redis_client, stream)
    consumer.on("launch", lambda payload: received.append(payload))
    await consumer.setup()

    await redis_client.xadd(
        stream, {"type": "launch", "payload": json.dumps({"processId": "test_1"})},
    )

    task = asyncio.create_task(consumer.run())
    await asyncio.sleep(1.5)
    consumer.stop()
    await task

    assert len(received) == 1
    assert received[0]["processId"] == "test_1"


async def test_consumer_handles_unknown_command(redis_client):
    stream = f"test_cmd_unk_{id(asyncio.get_event_loop())}"

    try:
        await redis_client.delete(stream)
    except Exception:
        pass

    consumer = CommandConsumer(redis_client, stream)
    await consumer.setup()

    await redis_client.xadd(stream, {"type": "bogus", "payload": "{}"})

    task = asyncio.create_task(consumer.run())
    await asyncio.sleep(1.5)
    consumer.stop()
    await task
    # No crash = success
