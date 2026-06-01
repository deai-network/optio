"""Tests for the surgical OAuth-redirect rewrite (headless Claude login)."""

from urllib.parse import parse_qs, urlsplit

from optio_claudecode.oauth_redirect import (
    MANUAL_REDIRECT_URL,
    rewrite_oauth_redirect,
)

# A real captured authorize URL (loopback callback), trimmed scope kept intact.
_AUTHORIZE = (
    "https://claude.com/cai/oauth/authorize?code=true"
    "&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code"
    "&redirect_uri=http%3A%2F%2Flocalhost%3A45861%2Fcallback"
    "&scope=org%3Acreate_api_key+user%3Aprofile+user%3Ainference"
    "&code_challenge=N2MCavupq8E6Y4dB4BuK7fMugu6Buo8z3iQwfm9oYf4"
    "&code_challenge_method=S256&state=VqqUEmBJokkKmPPKcJxcc-ItB7_YhxWTQIVnRBd2UAQ"
)


def _q(url: str) -> dict:
    return parse_qs(urlsplit(url).query, keep_blank_values=True)


def test_loopback_authorize_redirect_is_rewritten():
    out = rewrite_oauth_redirect(_AUTHORIZE)
    q = _q(out)
    assert q["redirect_uri"] == [MANUAL_REDIRECT_URL]
    # Host/path and every other param are preserved.
    assert urlsplit(out).netloc == "claude.com"
    assert urlsplit(out).path == "/cai/oauth/authorize"
    assert q["client_id"] == ["9d1c250a-e61b-44d9-88ed-5944d1962f5e"]
    assert q["code"] == ["true"]
    assert q["state"] == ["VqqUEmBJokkKmPPKcJxcc-ItB7_YhxWTQIVnRBd2UAQ"]
    assert q["code_challenge"] == ["N2MCavupq8E6Y4dB4BuK7fMugu6Buo8z3iQwfm9oYf4"]
    assert q["scope"] == ["org:create_api_key user:profile user:inference"]


def test_127_0_0_1_loopback_is_also_rewritten():
    url = _AUTHORIZE.replace("localhost", "127.0.0.1")
    assert _q(rewrite_oauth_redirect(url))["redirect_uri"] == [MANUAL_REDIRECT_URL]


def test_plain_oauth_authorize_path_matches():
    url = _AUTHORIZE.replace("/cai/oauth/authorize", "/oauth/authorize")
    assert _q(rewrite_oauth_redirect(url))["redirect_uri"] == [MANUAL_REDIRECT_URL]


def test_idempotent_already_manual_unchanged():
    once = rewrite_oauth_redirect(_AUTHORIZE)
    twice = rewrite_oauth_redirect(once)
    assert once == twice  # 2nd pass: redirect_uri no longer loopback -> no-op


def test_non_authorize_url_untouched():
    # A docs/other BROWSER url must pass through byte-for-byte.
    url = "https://docs.claude.com/some/page?x=1&redirect_uri=http%3A%2F%2Flocalhost%3A9%2Fcallback"
    assert rewrite_oauth_redirect(url) == url


def test_authorize_without_loopback_redirect_untouched():
    # An authorize URL whose redirect_uri isn't a loopback /callback is left alone.
    url = (
        "https://claude.com/cai/oauth/authorize?client_id=x"
        "&redirect_uri=https%3A%2F%2Fexample.com%2Fcb&state=s"
    )
    assert rewrite_oauth_redirect(url) == url


def test_loopback_but_not_callback_path_untouched():
    # localhost but not the /callback path -> not Claude's login shape -> untouched.
    url = (
        "https://claude.com/cai/oauth/authorize?client_id=x"
        "&redirect_uri=http%3A%2F%2Flocalhost%3A8080%2Fother&state=s"
    )
    assert rewrite_oauth_redirect(url) == url


def test_malformed_url_returned_unchanged():
    assert rewrite_oauth_redirect("not a url ::::") == "not a url ::::"


def test_empty_string():
    assert rewrite_oauth_redirect("") == ""
