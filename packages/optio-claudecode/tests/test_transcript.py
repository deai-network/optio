"""rebase_session_blob: projects-dir rekey + transcript truncation.

Spec: docs/2026-06-10-claudecode-session-restore-design.md §4
"""
from __future__ import annotations

import io
import json
import tarfile

import pytest

from optio_claudecode.transcript import rebase_session_blob, slugify_workdir


def _entry(uuid: str, parent: str | None = None, typ: str = "user", **extra) -> str:
    e = {
        "uuid": uuid, "parentUuid": parent, "isSidechain": False,
        "sessionId": "sess-1", "type": typ, **extra,
    }
    return json.dumps(e)


def _bookkeeping(leaf: str) -> str:
    return json.dumps({"type": "last-prompt", "leafUuid": leaf, "sessionId": "sess-1"})


# A realistic mini-transcript: bookkeeping, three turns, one sidechain entry.
LINES = [
    _bookkeeping("u3"),
    json.dumps({"type": "mode", "mode": "normal", "sessionId": "sess-1"}),
    _entry("u1", None, "user"),
    _entry("s1", "u1", "assistant", isSidechain=True),
    _entry("u2", "u1", "assistant"),
    _entry("u3", "u2", "user"),
]
TRANSCRIPT = "\n".join(LINES) + "\n"

OLD_SLUG = "-old-workdir"
NEW_WORKDIR = "/data/optio/tasks/conv-7/work.dir"


def _blob(files: dict[str, tuple[bytes, int]]) -> bytes:
    """Build a tar.gz blob: name -> (content, mtime)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, (content, mtime) in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(content)
            ti.mtime = mtime
            t.addfile(ti, io.BytesIO(content))
    return buf.getvalue()


def _default_blob() -> bytes:
    return _blob({
        "home/.claude/.credentials.json": (b'{"k":"v"}', 100),
        f"home/.claude/projects/{OLD_SLUG}/aaa.jsonl": (TRANSCRIPT.encode(), 200),
    })


def _read(blob: bytes) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as t:
        for m in t.getmembers():
            if m.isfile():
                out[m.name] = t.extractfile(m).read()
    return out


def test_slugify_workdir():
    assert slugify_workdir("/home/user/deai/optio") == "-home-user-deai-optio"
    assert slugify_workdir("/a/.claude/wt/") == "-a--claude-wt"


def test_rekey_only_moves_transcript_and_keeps_content():
    result = _read(rebase_session_blob(_default_blob(), new_workdir=NEW_WORKDIR))
    new_name = f"home/.claude/projects/{slugify_workdir(NEW_WORKDIR)}/aaa.jsonl"
    assert new_name in result
    assert result[new_name].decode() == TRANSCRIPT
    assert result["home/.claude/.credentials.json"] == b'{"k":"v"}'
    assert not any(OLD_SLUG in n for n in result)


def test_truncate_mid_file_keeps_prefix():
    result = _read(rebase_session_blob(
        _default_blob(), new_workdir=NEW_WORKDIR, until_uuid="u2",
    ))
    new_name = f"home/.claude/projects/{slugify_workdir(NEW_WORKDIR)}/aaa.jsonl"
    text = result[new_name].decode()
    assert '"u2"' in text and '"u3"' not in text.replace('"leafUuid": "u3"', "")
    # u3's entry line is gone entirely:
    assert _entry("u3", "u2", "user") not in text


def test_truncate_rewrites_dangling_leafuuid():
    result = _read(rebase_session_blob(
        _default_blob(), new_workdir=NEW_WORKDIR, until_uuid="u2",
    ))
    new_name = f"home/.claude/projects/{slugify_workdir(NEW_WORKDIR)}/aaa.jsonl"
    lines = result[new_name].decode().splitlines()
    lp = json.loads(lines[0])
    assert lp["type"] == "last-prompt"
    assert lp["leafUuid"] == "u2"  # was u3, now dropped → rewritten to boundary


def test_truncate_at_last_entry_is_noop_cut():
    result = _read(rebase_session_blob(
        _default_blob(), new_workdir=NEW_WORKDIR, until_uuid="u3",
    ))
    new_name = f"home/.claude/projects/{slugify_workdir(NEW_WORKDIR)}/aaa.jsonl"
    assert result[new_name].decode() == TRANSCRIPT  # nothing dropped, leaf intact


def test_unknown_uuid_raises():
    with pytest.raises(ValueError, match="not found"):
        rebase_session_blob(
            _default_blob(), new_workdir=NEW_WORKDIR, until_uuid="nope",
        )


def test_no_transcript_raises():
    blob = _blob({"home/.claude/.credentials.json": (b"{}", 100)})
    with pytest.raises(ValueError, match="no transcript"):
        rebase_session_blob(blob, new_workdir=NEW_WORKDIR)


def test_newest_of_several_transcripts_is_truncated():
    older = "\n".join([_entry("o1")]) + "\n"
    blob = _blob({
        f"home/.claude/projects/{OLD_SLUG}/old.jsonl": (older.encode(), 100),
        f"home/.claude/projects/{OLD_SLUG}/new.jsonl": (TRANSCRIPT.encode(), 200),
    })
    result = _read(rebase_session_blob(
        blob, new_workdir=NEW_WORKDIR, until_uuid="u2",
    ))
    slug = slugify_workdir(NEW_WORKDIR)
    assert result[f"home/.claude/projects/{slug}/old.jsonl"].decode() == older
    assert '"u3"' not in result[f"home/.claude/projects/{slug}/new.jsonl"].decode()
