"""Config defaults + the opencode seed-manifest surface.

Mirrors optio_claudecode/tests/test_seed_config.py, adapted for opencode:
opencode needs no consume-time rekey (`consume_transform is None`), so there
is no `_rekey_*` transform to exercise. Instead this covers the manifest
shape and the ergonomic `delete_seed` / `list_seeds` / `purge_seed` wrappers
that bind `OPENCODE_SEED_SUFFIX`, round-tripped against the `mongo_db`
fixture (mirror of the optio-agents seed tests).
"""

import inspect

from bson import ObjectId

from optio_agents import seeds

from optio_opencode import OpencodeTaskConfig
from optio_core import MongoStore
from optio_opencode.seed_manifest import (
    OPENCODE_SEED_MANIFEST,
    OPENCODE_SEED_SUFFIX,
    delete_seed,
    list_seeds,
    purge_seed,
)


def test_seed_config_defaults_none():
    cfg = OpencodeTaskConfig(consumer_instructions="hi", fs_isolation=False)
    assert cfg.seed_id is None
    assert cfg.on_seed_saved is None
    assert cfg.auto_start is False


def test_opencode_config_scrub_env_default_none():
    from optio_opencode.types import OpencodeTaskConfig
    assert OpencodeTaskConfig(consumer_instructions="hi", fs_isolation=False).scrub_env is None


def test_manifest_shape():
    assert OPENCODE_SEED_SUFFIX == "_opencode_seeds"
    assert OPENCODE_SEED_MANIFEST.home_subdir == "home"
    assert ".local/share/opencode/auth.json" in OPENCODE_SEED_MANIFEST.include
    # opencode is cwd-independent: no consume-time rekey transform.
    assert OPENCODE_SEED_MANIFEST.consume_transform is None


async def test_wrappers_bind_suffix_roundtrip(mongo_db):
    """delete_seed / list_seeds bind OPENCODE_SEED_SUFFIX (round-trip)."""
    blob_id = ObjectId()
    # Insert via the generic engine into the suffix-bound collection, then
    # confirm the opencode wrappers (which take no `suffix` arg) see it.
    seed_id = await seeds.insert_seed(
        mongo_db, prefix="t", suffix=OPENCODE_SEED_SUFFIX,
        blob_id=blob_id, manifest_version=1,
    )

    store = MongoStore(db=mongo_db, prefix="t")
    listed = await list_seeds(store)
    assert [d["seedId"] for d in listed] == [seed_id]

    removed_blob = await delete_seed(store, seed_id)
    assert removed_blob == blob_id
    assert await list_seeds(store) == []


async def test_delete_seed_tolerates_bad_id(mongo_db):
    assert await delete_seed(MongoStore(db=mongo_db, prefix="t"), "not-hex") is None


def test_purge_seed_wrapper_exists():
    """purge_seed exists per the frozen Shared-contracts surface.

    NOTE: the wrapper binds `seeds.purge_seed`, which the optio-agents seed
    engine does not yet expose; the engine addition lands outside this test's
    file scope. So this asserts the wrapper's presence/shape only (it is an
    async function that binds OPENCODE_SEED_SUFFIX) without invoking it
    through the not-yet-present engine function. A round-trip will be added
    once the engine exposes `purge_seed`.
    """
    assert inspect.iscoroutinefunction(purge_seed)
