"""Optio demo application — exercises all optio-core features."""

import asyncio
import os
import logging

from motor.motor_asyncio import AsyncIOMotorClient
from optio_core.lifecycle import Optio

from optio_demo.tasks import get_task_definitions

logging.basicConfig(level=logging.INFO)


async def main():
    mongo_url = os.environ.get("MONGODB_URL", "mongodb://localhost:27017/optio-demo")
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    prefix = os.environ.get("OPTIO_PREFIX", "optio")

    db_name = mongo_url.rsplit("/", 1)[-1]
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    fw = Optio()
    await fw.init(
        mongo_db=db,
        prefix=prefix,
        redis_url=redis_url,
        services={"optio": fw},
        get_task_definitions=get_task_definitions,
    )

    await fw.run()


if __name__ == "__main__":
    asyncio.run(main())
