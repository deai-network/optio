"""Create TTL index on `expireAt` for all *_processes collections.

The TTL feature ($set expireAt on terminal-state transitions) needs a
Mongo TTL index to actually evict expired records. expireAfterSeconds=0
means Mongo treats the field as the absolute expiry time (records whose
`expireAt` is past `now()` are evicted by Mongo's background TTL monitor,
which sweeps every 60s in real Mongo).

Idempotency: PyMongo's `create_index` is idempotent on identical
key+options; repeated invocations with the same parameters are no-ops.
A conflict (different options for the same name) raises — desired:
loud failure means the TTL contract was changed without a follow-up
migration.
"""

from optio_core.migrations import fw_migrations


@fw_migrations.register(
    "create_expire_at_ttl_index",
    depends_on=["backfill_has_saved_state"],
)
async def create_expire_at_ttl_index(db):
    collection_names = await db.list_collection_names()
    process_collections = [n for n in collection_names if n.endswith("_processes")]
    for coll_name in process_collections:
        await db[coll_name].create_index(
            [("expireAt", 1)],
            expireAfterSeconds=0,
            name="expireAt_ttl",
        )
