"""Unit tests for the codex seed manifest (Stage 3, Task 1).

Adapted from optio-grok's seed-manifest shape (codex, like grok/opencode,
needs no cwd-rekey → consume_transform is None; the cwd-dependent workdir
pre-trust is a deliberate post-merge edit in _prepare, NOT a transform).
CODEX_HOME is <workdir>/home/.codex; the engine roots capture/extract at
host.workdir + "/" + home_subdir, so the manifest uses home_subdir="home"
with ".codex/" prefixes on the include paths.
"""

from __future__ import annotations

from optio_agents import seeds

from optio_codex.seed_manifest import (
    CODEX_CRED_MANIFEST,
    CODEX_SEED_MANIFEST,
    CODEX_SEED_SUFFIX,
)


def test_seed_manifest_home_and_contents():
    assert isinstance(CODEX_SEED_MANIFEST, seeds.SeedManifest)
    assert CODEX_SEED_MANIFEST.home_subdir == "home"
    assert ".codex/auth.json" in CODEX_SEED_MANIFEST.include
    assert ".codex/config.toml" in CODEX_SEED_MANIFEST.include


def test_seed_manifest_never_carries_junk():
    """The include list is an allowlist — the 286MB packages/ cache, sqlite
    session index, sessions/, logs etc. must never be members (the design
    doc's exclude-always list is enforced by NOT including them)."""
    assert set(CODEX_SEED_MANIFEST.include) == {
        ".codex/auth.json", ".codex/config.toml",
    }


def test_no_consume_transform():
    # codex auth/config are cwd-independent → no rekey. The workdir trust
    # entry is cwd-dependent but handled as a post-merge edit in _prepare.
    assert CODEX_SEED_MANIFEST.consume_transform is None
    assert CODEX_CRED_MANIFEST.consume_transform is None


def test_cred_manifest_is_auth_only():
    assert CODEX_CRED_MANIFEST.home_subdir == "home"
    assert CODEX_CRED_MANIFEST.include == [".codex/auth.json"]


def test_seed_suffix():
    assert CODEX_SEED_SUFFIX == "_codex_seeds"


def test_crud_wrappers_exported():
    from optio_codex import delete_seed, list_seeds, purge_seed  # noqa: F401
    from optio_codex.seed_manifest import (  # noqa: F401
        delete_seed as _d, list_seeds as _l, purge_seed as _p,
    )
