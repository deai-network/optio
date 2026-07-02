"""Smoke test for the optio-cursor demo tasks.

Mirrors the claudecode/opencode/grok demos: an always-present
``cursor-seed-setup`` task plus seed-pinned run tasks that appear once a cursor
seed exists. Gating is via ``optio_cursor.list_seeds`` (the real
``{prefix}_cursor_seeds`` collection), so the test drives it by inserting a
fake seed doc there.

Uses a live MongoDB (``MONGO_URL`` / mongodb://localhost:27017). The async
work runs under ``asyncio.run`` so the test is independent of the demo
package's pytest-asyncio configuration.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import types

from motor.motor_asyncio import AsyncIOMotorClient

from optio_cursor.seed_manifest import CURSOR_SEED_SUFFIX

from optio_demo.tasks import get_task_definitions
from optio_demo.tasks.cursor import get_tasks


PREFIX = "test"


class _FakeFw:
    """Minimal stand-in for the optio framework instance the demo factories
    receive as ``services['optio']``. Only ``mongo_store`` (db+prefix) is
    exercised by the cursor factory's ``list_seeds`` gating."""

    def __init__(self, db, prefix: str):
        self._db = db
        self._prefix = prefix

    @property
    def mongo_store(self):
        return types.SimpleNamespace(db=self._db, prefix=self._prefix)


def _services(db):
    return {"db": db, "prefix": PREFIX, "optio": _FakeFw(db, PREFIX)}


def test_get_tasks_is_async_services_factory():
    """The cursor demo is a seed-lifecycle factory: async ``get_tasks(services)``."""
    assert inspect.iscoroutinefunction(get_tasks)
    assert list(inspect.signature(get_tasks).parameters) == ["services"]

    src = inspect.getsource(inspect.getmodule(get_tasks))
    assert 'process_id="cursor-seed-setup"' in src
    assert "create_cursor_task" in src
    # seed-launched demo tasks opt into resume
    assert "supports_resume=True" in src


def test_seed_setup_always_and_pinned_iframe_when_seed_present():
    async def _run():
        client = AsyncIOMotorClient(
            os.environ.get("MONGO_URL", "mongodb://localhost:27017"),
        )
        db_name = f"optio_cursor_demo_test_{os.getpid()}"
        db = client[db_name]
        try:
            # --- no seed: only the seed-setup task, no pinned tasks ---------
            defs = await get_task_definitions(_services(db))
            ids = {t.process_id for t in defs}
            assert "cursor-seed-setup" in ids
            assert not any(
                pid.startswith("cursor-demo-seed-")
                or pid.startswith("cursor-conversation-seed-")
                for pid in ids
            )

            # --- a real cursor seed exists: the iframe pinned task appears --
            res = await db[f"{PREFIX}{CURSOR_SEED_SUFFIX}"].insert_one(
                {"blobId": "fake-blob", "manifestVersion": 1},
            )
            seed_id = str(res.inserted_id)

            defs = await get_task_definitions(_services(db))
            ids = {t.process_id for t in defs}
            assert "cursor-seed-setup" in ids
            assert f"cursor-demo-seed-{seed_id}" in ids
        finally:
            await client.drop_database(db_name)
            client.close()

    asyncio.run(_run())
