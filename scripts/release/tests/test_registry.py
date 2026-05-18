import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from registry import npm_latest, pypi_latest


def _fake_urlopen(response_obj):
    class _Ctx:
        def __enter__(self):
            return response_obj
        def __exit__(self, *exc):
            return False
    return _Ctx()


def _http_error(code: int):
    return urllib.error.HTTPError(
        url="x", code=code, msg="x", hdrs=None, fp=io.BytesIO(b"")
    )


class TestNpmLatest:
    def test_returns_version(self):
        body = io.BytesIO(json.dumps({"version": "1.2.3"}).encode())
        with patch("registry.urllib.request.urlopen", return_value=_fake_urlopen(body)):
            assert npm_latest("optio-ui") == "1.2.3"

    def test_404_returns_none(self):
        def raise_404(*a, **kw):
            raise _http_error(404)
        with patch("registry.urllib.request.urlopen", side_effect=raise_404):
            assert npm_latest("nonexistent-pkg") is None

    def test_other_http_error_propagates(self):
        def raise_500(*a, **kw):
            raise _http_error(500)
        with patch("registry.urllib.request.urlopen", side_effect=raise_500):
            with pytest.raises(urllib.error.HTTPError):
                npm_latest("optio-ui")


class TestPypiLatest:
    def test_returns_version(self):
        body = io.BytesIO(json.dumps({"info": {"version": "0.4.2"}}).encode())
        with patch("registry.urllib.request.urlopen", return_value=_fake_urlopen(body)):
            assert pypi_latest("optio-core") == "0.4.2"

    def test_404_returns_none(self):
        def raise_404(*a, **kw):
            raise _http_error(404)
        with patch("registry.urllib.request.urlopen", side_effect=raise_404):
            assert pypi_latest("nonexistent-pkg") is None
