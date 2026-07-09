"""analyze_account unit tests — the kimicode account analyzer over the committed
live-seed fixture (``fixtures/kimi_coding_usages.json``).

The one read-only ``GET https://api.kimi.com/coding/v1/usages`` is stubbed by
monkeypatching the ``_fetch`` seam (mirrors cursor/codex), so these tests run
with zero network. They pin the load-bearing quirks the research nailed down:
string counts (``"100"``), ``used`` derived from ``limit - remaining`` in the
``limits[]`` rows, the ``resetTime`` camelCase µs timestamps, the
``TIME_UNIT_MINUTE`` enum → ``"5h"`` label synthesis, per-window ``model=None``,
and — because kimi exposes neither name nor email — a ``None`` summary even
though plan + account_id ARE populated.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone

import pytest

from optio_agents.account import EMPTY, AccountInfo
from optio_kimicode import account

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "kimi_coding_usages.json"


def _fixture() -> dict:
    return json.loads(_FIXTURE.read_text())


async def test_maps_fixture_into_account_info(monkeypatch):
    body = _fixture()

    async def _fake_fetch(token):
        assert token == "TOK"
        return body

    monkeypatch.setattr(account, "_fetch", _fake_fetch)

    info = await account.analyze_account("TOK")

    # Identity: account_id + plan present, name/email absent → summary is the
    # plan alone (friendlier than the opaque account id in the pool UI).
    assert isinstance(info, AccountInfo)
    assert info.account_id == "EXAMPLEEXAMPLEEXAMPLE0"
    assert info.plan == "Basic"                      # LEVEL_BASIC humanized
    assert info.name is None
    assert info.email is None
    assert info.summary == "Plan: Basic"             # no identity → plan-only summary

    # Two windows: the top-level account quota + the one 5h limit row.
    assert len(info.windows) == 2
    by_label = {w.label: w for w in info.windows}

    quota = by_label["Account quota"]
    assert quota.pct == pytest.approx(2.0)           # used 2 / limit 100
    assert quota.model is None
    assert quota.resets_at == datetime(
        2026, 7, 11, 20, 30, 55, 151098, tzinfo=timezone.utc
    )

    five_h = by_label["5h"]                           # 300 TIME_UNIT_MINUTE → "5h"
    assert five_h.pct == pytest.approx(0.0)           # used = limit(100) - remaining(100)
    assert five_h.model is None
    assert five_h.resets_at == datetime(
        2026, 7, 9, 10, 30, 55, 151098, tzinfo=timezone.utc
    )

    # Full payload parked in raw.
    assert info.raw == {"usage": body}


async def test_fetch_raises_is_fail_soft_empty(monkeypatch):
    async def _boom(token):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(account, "_fetch", _boom)

    # Fail-soft: never raises, degrades to EMPTY.
    info = await account.analyze_account("TOK")
    assert info == EMPTY


async def test_fetch_returns_none_is_empty(monkeypatch):
    async def _none(token):
        return None

    monkeypatch.setattr(account, "_fetch", _none)
    assert await account.analyze_account("TOK") == EMPTY


async def test_blank_token_is_empty_no_fetch(monkeypatch):
    def _must_not_call(token):
        raise AssertionError("must not fetch on a blank token")

    monkeypatch.setattr(account, "_fetch", _must_not_call)
    assert await account.analyze_account("") == EMPTY
    assert await account.analyze_account(None) == EMPTY  # type: ignore[arg-type]


# --- resolve_capture_account (live-host creds read) -------------------------


class _FakeHost:
    def __init__(self, workdir: str, payload: bytes | Exception):
        self.workdir = workdir
        self._payload = payload
        self.requested: list[str] = []

    async def fetch_bytes_from_host(self, path: str) -> bytes:
        self.requested.append(path)
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


async def test_resolve_capture_account_reads_token_and_delegates(monkeypatch):
    creds = {"access_token": "LIVE_TOKEN", "refresh_token": "R"}
    host = _FakeHost("/work/task", json.dumps(creds).encode("utf-8"))

    seen = {}

    async def _fake_analyze(token):
        seen["token"] = token
        return AccountInfo(account_id="abc", plan="Basic")

    monkeypatch.setattr(account, "analyze_account", _fake_analyze)

    info = await account.resolve_capture_account(host)
    assert seen["token"] == "LIVE_TOKEN"
    assert info.account_id == "abc"
    # Reads the isolated-HOME creds path under the workdir.
    assert host.requested == ["/work/task/home/credentials/kimi-code.json"]


async def test_resolve_capture_account_missing_creds_is_empty(monkeypatch):
    host = _FakeHost("/work/task", FileNotFoundError("nope"))

    async def _fake_analyze(token):  # pragma: no cover - must not be reached
        raise AssertionError("must not analyze without creds")

    monkeypatch.setattr(account, "analyze_account", _fake_analyze)
    assert await account.resolve_capture_account(host) == EMPTY


async def test_resolve_capture_account_no_token_is_empty(monkeypatch):
    host = _FakeHost("/work/task", json.dumps({"refresh_token": "R"}).encode("utf-8"))
    assert await account.resolve_capture_account(host) == EMPTY
