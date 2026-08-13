import dataclasses

import pytest

from optio_agents.config_types import BlobCryptoConfigMixin


@dataclasses.dataclass(frozen=True)
class _Cfg(BlobCryptoConfigMixin):
    name: str = "x"

    def __post_init__(self):
        self._validate_blob_crypto()


def _enc(b: bytes) -> bytes:
    return b


def _dec(b: bytes) -> bytes:
    return b


def _seed_enc(b: bytes) -> bytes:
    return b


def _seed_dec(b: bytes) -> bytes:
    return b


def test_defaults_are_plaintext():
    c = _Cfg()
    assert c.session_blob_encrypt is None
    assert c.session_blob_decrypt is None
    assert c.seed_blob_encrypt is None
    assert c.seed_blob_decrypt is None


def test_asymmetric_session_pair_raises():
    with pytest.raises(ValueError, match="_Cfg: session_blob_encrypt"):
        _Cfg(session_blob_encrypt=_enc)
    with pytest.raises(ValueError, match="_Cfg: session_blob_encrypt"):
        _Cfg(session_blob_decrypt=_dec)


def test_asymmetric_seed_pair_raises():
    with pytest.raises(ValueError, match="_Cfg: seed_blob_encrypt"):
        _Cfg(seed_blob_encrypt=_seed_enc)
    with pytest.raises(ValueError, match="_Cfg: seed_blob_encrypt"):
        _Cfg(seed_blob_decrypt=_seed_dec)


def test_both_pairs_set_is_ok():
    c = _Cfg(
        session_blob_encrypt=_enc,
        session_blob_decrypt=_dec,
        seed_blob_encrypt=_seed_enc,
        seed_blob_decrypt=_seed_dec,
    )
    assert c.session_blob_encrypt is _enc
    assert c.seed_blob_encrypt is _seed_enc


def test_seed_accessors_prefer_the_seed_pair():
    c = _Cfg(
        session_blob_encrypt=_enc,
        session_blob_decrypt=_dec,
        seed_blob_encrypt=_seed_enc,
        seed_blob_decrypt=_seed_dec,
    )
    assert c.seed_encrypt is _seed_enc
    assert c.seed_decrypt is _seed_dec


def test_seed_accessors_fall_back_to_the_session_pair():
    c = _Cfg(session_blob_encrypt=_enc, session_blob_decrypt=_dec)
    assert c.seed_encrypt is _enc
    assert c.seed_decrypt is _dec


def test_seed_accessors_none_when_neither_pair_set():
    c = _Cfg()
    assert c.seed_encrypt is None
    assert c.seed_decrypt is None
