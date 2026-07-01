"""Unit tests for the grok seed manifest (Stage 3, Task 1).

Adapted from optio-opencode's seed-manifest shape (grok, like opencode, needs
no cwd-rekey → consume_transform is None). GROK_HOME is <workdir>/home/.grok;
the engine roots capture/extract at host.workdir + "/" + home_subdir, so the
manifest uses home_subdir="home" with ".grok/" prefixes on the include paths.
"""

from __future__ import annotations

from optio_agents import seeds

from optio_grok.seed_manifest import (
    GROK_CRED_MANIFEST,
    GROK_SEED_MANIFEST,
    GROK_SEED_SUFFIX,
)


def test_seed_manifest_home_and_contents():
    assert isinstance(GROK_SEED_MANIFEST, seeds.SeedManifest)
    # home_subdir is the isolated HOME (engine docstring: "HOME relative to the
    # workdir, e.g. 'home'"); GROK_HOME = <workdir>/home/.grok lives beneath it.
    assert GROK_SEED_MANIFEST.home_subdir == "home"
    assert ".grok/auth.json" in GROK_SEED_MANIFEST.include
    assert ".grok/config.toml" in GROK_SEED_MANIFEST.include


def test_no_consume_transform():
    # grok auth/config are cwd-independent → no rekey (like opencode).
    assert GROK_SEED_MANIFEST.consume_transform is None
    assert GROK_CRED_MANIFEST.consume_transform is None


def test_cred_manifest_is_auth_only():
    assert GROK_CRED_MANIFEST.home_subdir == "home"
    assert GROK_CRED_MANIFEST.include == [".grok/auth.json"]


def test_seed_suffix():
    assert GROK_SEED_SUFFIX == "_grok_seeds"
