"""Unit tests for the cursor seed manifest (Stage 3, Task 1).

Adapted from optio-grok's seed-manifest shape (cursor, like grok/opencode,
needs no cwd-rekey → consume_transform is None). Cursor's logged-in identity
lives at <workdir>/home/.config/cursor/auth.json (our launch env sets
XDG_CONFIG_HOME=<workdir>/home/.config); the engine roots capture/extract at
host.workdir + "/" + home_subdir, so the manifest uses home_subdir="home"
with include paths relative to it.
"""

from __future__ import annotations

from optio_agents import seeds

from optio_cursor.seed_manifest import (
    CURSOR_CRED_MANIFEST,
    CURSOR_SEED_MANIFEST,
    CURSOR_SEED_SUFFIX,
)


def test_seed_manifest_home_and_contents():
    assert isinstance(CURSOR_SEED_MANIFEST, seeds.SeedManifest)
    # home_subdir is the isolated HOME (engine docstring: "HOME relative to
    # the workdir, e.g. 'home'"); the cursor auth file lives beneath it at
    # .config/cursor/auth.json.
    assert CURSOR_SEED_MANIFEST.home_subdir == "home"
    assert ".config/cursor/auth.json" in CURSOR_SEED_MANIFEST.include
    assert ".cursor/cli-config.json" in CURSOR_SEED_MANIFEST.include


def test_no_consume_transform():
    # cursor auth/config are cwd-independent → no rekey (like grok/opencode).
    assert CURSOR_SEED_MANIFEST.consume_transform is None
    assert CURSOR_CRED_MANIFEST.consume_transform is None


def test_cred_manifest_is_auth_only():
    assert CURSOR_CRED_MANIFEST.home_subdir == "home"
    assert CURSOR_CRED_MANIFEST.include == [".config/cursor/auth.json"]


def test_seed_suffix():
    assert CURSOR_SEED_SUFFIX == "_cursor_seeds"
