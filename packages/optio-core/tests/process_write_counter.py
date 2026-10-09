"""Count the writes made to a prefix's process collection.

`CountingDb(db, prefix)` stands in for the motor database: `db[f"{prefix}_processes"]`
records every insert, update and delete in `.writes` and passes it on; every
other collection, and every read, goes straight to the real database.
Spec: docs/2026-10-09-fewer-process-writes-design.md
"""

from dataclasses import dataclass
from typing import Any

from bson import ObjectId


@dataclass
class Write:
    op: str  # "insert" | "update" | "delete"
    oid: ObjectId | None  # the inserted _id, or the filter's _id
    update: dict | None = None  # the update document of an "update"


class _CountingCollection:
    def __init__(self, coll: Any, writes: list[Write]) -> None:
        self._coll = coll
        self._writes = writes

    def __getattr__(self, name: str) -> Any:
        return getattr(self._coll, name)

    async def insert_one(self, doc: dict, *args: Any, **kwargs: Any) -> Any:
        result = await self._coll.insert_one(doc, *args, **kwargs)
        self._writes.append(Write("insert", result.inserted_id))
        return result

    async def update_one(self, filter: dict, update: Any, *args: Any, **kwargs: Any) -> Any:
        self._writes.append(Write("update", filter.get("_id"), update))
        return await self._coll.update_one(filter, update, *args, **kwargs)

    async def update_many(self, filter: dict, update: Any, *args: Any, **kwargs: Any) -> Any:
        self._writes.append(Write("update", filter.get("_id"), update))
        return await self._coll.update_many(filter, update, *args, **kwargs)

    async def delete_one(self, filter: dict, *args: Any, **kwargs: Any) -> Any:
        self._writes.append(Write("delete", filter.get("_id")))
        return await self._coll.delete_one(filter, *args, **kwargs)

    async def delete_many(self, filter: dict, *args: Any, **kwargs: Any) -> Any:
        self._writes.append(Write("delete", filter.get("_id")))
        return await self._coll.delete_many(filter, *args, **kwargs)


class CountingDb:
    def __init__(self, db: Any, prefix: str) -> None:
        self._db = db
        self._name = f"{prefix}_processes"
        self.writes: list[Write] = []

    def __getitem__(self, name: str) -> Any:
        coll = self._db[name]
        return _CountingCollection(coll, self.writes) if name == self._name else coll

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)

    def updates_on(self, oid: ObjectId) -> list[dict]:
        """The update documents written to `oid`, in order."""
        return [w.update for w in self.writes if w.op == "update" and w.oid == oid]
