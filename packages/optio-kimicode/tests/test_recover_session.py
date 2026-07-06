"""Tests for session._recover_session_id — picking the REAL prior conversation
from the restored kimi session store on resume, not a filesystem-arbitrary one.

The store accumulates, across resume cycles, the real conversation PLUS empty
``session/new`` sessions and resume-notice-only sessions (whose only turn is the
``System: you have been resumed`` notice). The recovery must land on the real
one so ``session/load`` replays actual history. state.json shape keyed on
matches the live kimi store: {title, lastPrompt, updatedAt, createdAt, …}.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from optio_host.host import LocalHost
from optio_kimicode.session import _recover_session_id

pytestmark = pytest.mark.asyncio


async def _host_with_sessions(tmp_path: pathlib.Path, sessions: list[dict]) -> LocalHost:
    host = LocalHost(taskdir=str(tmp_path / "recover"))
    await host.setup_workdir()
    root = pathlib.Path(host.workdir) / "home" / "sessions" / "wd_workdir_deadbeef"
    for s in sessions:
        d = root / f"session_{s['id']}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text(json.dumps({
            "createdAt": s.get("createdAt", s["updatedAt"]),
            "updatedAt": s["updatedAt"],
            "title": s.get("title", ""),
            "isCustomTitle": False,
            "lastPrompt": s.get("lastPrompt", ""),
        }))
    return host


# The exact strings the live store showed for empty resume-notice sessions.
_NOTICE = "System: you have been resumed"


async def test_recovers_real_conversation_over_empties_and_notices(tmp_path):
    # Real one is the OLDEST; the notices and the fresh session/new are NEWER —
    # so a naive "most recent" OR a "find | head" would miss it. We must still
    # return the real one.
    host = await _host_with_sessions(tmp_path, [
        {"id": "aareal00", "title": "Hi, who am i talking to?",
         "lastPrompt": "how many live there?", "updatedAt": "2026-07-06T19:01:14.676Z"},
        {"id": "bbnotice1", "title": _NOTICE, "lastPrompt": _NOTICE,
         "updatedAt": "2026-07-06T19:03:41.789Z"},
        {"id": "ccnotice2", "title": _NOTICE, "lastPrompt": _NOTICE,
         "updatedAt": "2026-07-06T19:56:27.000Z"},
        {"id": "ddfresh0", "title": "", "lastPrompt": "",  # fresh session/new
         "updatedAt": "2026-07-06T20:00:00.000Z"},
    ])
    assert await _recover_session_id(host) == "session_aareal00"


async def test_picks_most_recent_real_even_with_a_System_title(tmp_path):
    # A session STARTED by the resume notice but then CONTINUED by a real user
    # turn has a System: title yet a real lastPrompt — it IS a real conversation.
    # Pick the most-recently-updated real one.
    host = await _host_with_sessions(tmp_path, [
        {"id": "oldreal0", "title": "old chat",
         "lastPrompt": "old question", "updatedAt": "2026-07-06T19:01:00.000Z"},
        {"id": "notice00", "title": _NOTICE, "lastPrompt": _NOTICE,
         "updatedAt": "2026-07-06T20:00:00.000Z"},
        {"id": "newreal0", "title": _NOTICE,  # System title …
         "lastPrompt": "have you forgotten our previous discussion?",  # … real prompt
         "updatedAt": "2026-07-06T19:30:00.000Z"},
    ])
    assert await _recover_session_id(host) == "session_newreal0"


async def test_returns_none_when_only_empty_or_notice_sessions(tmp_path):
    host = await _host_with_sessions(tmp_path, [
        {"id": "notice00", "title": _NOTICE, "lastPrompt": _NOTICE,
         "updatedAt": "2026-07-06T19:03:41.000Z"},
        {"id": "fresh000", "title": "", "lastPrompt": "",
         "updatedAt": "2026-07-06T20:00:00.000Z"},
    ])
    assert await _recover_session_id(host) is None


async def test_returns_none_when_no_sessions(tmp_path):
    host = LocalHost(taskdir=str(tmp_path / "empty"))
    await host.setup_workdir()
    assert await _recover_session_id(host) is None
