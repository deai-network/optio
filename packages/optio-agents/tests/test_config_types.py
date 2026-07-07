import pytest
from optio_agents import (AllowedDir, ConversationMode, SeedProvider,
                          SeedUnavailableError, ThinkingVerbosity, ToolVerbosity)

def test_alloweddir_accepts_superset_and_rejects_junk():
    for m in ("ro", "rw", "rox", "rwx"):
        assert AllowedDir("/w", m).mode == m
    with pytest.raises(ValueError):
        AllowedDir("/w", "wx")

def test_aliases_importable_from_top_level():
    # smoke: the Literals/aliases are exported for wrappers to import
    assert SeedProvider is not None and issubclass(SeedUnavailableError, Exception)
