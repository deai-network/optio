"""Uniformity guard: the shared config surface must be identical across all 7
engines. Every ``AgentTaskConfig`` union member inherits the blob-crypto and
claustrum field sets from the ``optio_agents.config_types`` mixins, so the
guard is structural (subclass checks) plus behavioral (validation + fallback
behave the same on each concrete config class)."""
from typing import get_args

import pytest

from optio_agents import BlobCryptoConfigMixin
from optio_agents.config_types import ClaustrumConfigMixin

import optio_agents_all as aa
from optio_agents_all.factory import _REGISTRY

CONFIG_TYPES = get_args(aa.AgentTaskConfig)


def _make(cls, **kw):
    """Minimal valid config of any engine (claustrum opted out)."""
    return cls(consumer_instructions="x", fs_isolation=False, **kw)


# Distinct sentinel transforms so identity assertions can tell the pairs apart.
def _session_enc(data: bytes) -> bytes:
    return data


def _session_dec(data: bytes) -> bytes:
    return data


def _seed_enc(data: bytes) -> bytes:
    return data


def _seed_dec(data: bytes) -> bytes:
    return data


@pytest.mark.parametrize("cls", CONFIG_TYPES, ids=lambda c: c.__name__)
def test_union_members_inherit_shared_mixins(cls):
    assert issubclass(cls, BlobCryptoConfigMixin)
    assert issubclass(cls, ClaustrumConfigMixin)


@pytest.mark.parametrize("cls", CONFIG_TYPES, ids=lambda c: c.__name__)
def test_asymmetric_seed_pair_raises_on_every_engine(cls):
    with pytest.raises(ValueError, match="seed_blob_encrypt"):
        _make(cls, seed_blob_encrypt=_seed_enc)
    with pytest.raises(ValueError, match="seed_blob_encrypt"):
        _make(cls, seed_blob_decrypt=_seed_dec)


@pytest.mark.parametrize("cls", CONFIG_TYPES, ids=lambda c: c.__name__)
def test_seed_accessor_fallback_uniform(cls):
    # Seed pair set → accessors return the seed transforms, not the session ones.
    both = _make(
        cls,
        session_blob_encrypt=_session_enc,
        session_blob_decrypt=_session_dec,
        seed_blob_encrypt=_seed_enc,
        seed_blob_decrypt=_seed_dec,
    )
    assert both.seed_encrypt is _seed_enc
    assert both.seed_decrypt is _seed_dec

    # Seed pair unset → falls back to the session pair (single-key callers).
    session_only = _make(
        cls, session_blob_encrypt=_session_enc, session_blob_decrypt=_session_dec
    )
    assert session_only.seed_encrypt is _session_enc
    assert session_only.seed_decrypt is _session_dec

    # Neither pair → plaintext (None) on both accessors.
    plain = _make(cls)
    assert plain.seed_encrypt is None
    assert plain.seed_decrypt is None


@pytest.mark.parametrize("cls", CONFIG_TYPES, ids=lambda c: c.__name__)
def test_create_task_passes_config_through_untouched(cls, monkeypatch):
    # The dispatcher must hand the config OBJECT through verbatim (identity),
    # so the crypto channel needs no entry-point code of its own.
    cfg = _make(cls, seed_blob_encrypt=_seed_enc, seed_blob_decrypt=_seed_dec)
    seen = {}
    monkeypatch.setitem(
        _REGISTRY,
        cfg.agent_type,
        lambda p, n, c, description=None, metadata=None: seen.setdefault("cfg", c),
    )
    aa.create_task("pid", "nm", cfg)
    assert seen["cfg"] is cfg
