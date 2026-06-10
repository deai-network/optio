"""Session-blob transform: projects-dir rekey + optional transcript truncation.

Spec: docs/2026-06-10-claudecode-session-restore-design.md §4

Pure in-memory tar.gz → tar.gz functions; no host or Mongo dependency.
"""
from __future__ import annotations

import io
import json
import tarfile

_PROJECTS_PREFIX = "home/.claude/projects/"


def slugify_workdir(workdir: str) -> str:
    """Claude Code's projects-dir name for a session cwd.

    Empirical rule ('/' and '.' both map to '-'), confirmed on two
    interactive transcript samples; headless confirmation is
    live-verification item §7.4 of the spec.
    """
    return workdir.rstrip("/").replace("/", "-").replace(".", "-")


def _norm(name: str) -> str:
    return name[2:] if name.startswith("./") else name


def rebase_session_blob(
    plain_tar: bytes, *, new_workdir: str, until_uuid: str | None = None,
) -> bytes:
    """Rekey a home/.claude session blob to ``new_workdir``'s projects slug
    and optionally truncate its newest transcript after ``until_uuid``.

    Raises ValueError when the blob has no transcript, or when
    ``until_uuid`` is not found in the newest transcript.
    """
    new_slug = slugify_workdir(new_workdir)
    src = tarfile.open(fileobj=io.BytesIO(plain_tar), mode="r:*")
    members = src.getmembers()

    transcripts = [
        m for m in members
        if m.isfile()
        and _norm(m.name).startswith(_PROJECTS_PREFIX)
        and _norm(m.name).endswith(".jsonl")
    ]
    if not transcripts:
        raise ValueError("session blob contains no transcript (*.jsonl)")
    target = max(transcripts, key=lambda m: m.mtime)

    new_payloads: dict[str, bytes] = {}
    if until_uuid is not None:
        new_payloads[target.name] = _truncate(
            src.extractfile(target).read(), until_uuid, _norm(target.name),
        )

    def rekeyed(name: str) -> str:
        norm = _norm(name)
        if not norm.startswith(_PROJECTS_PREFIX):
            return norm
        rest = norm[len(_PROJECTS_PREFIX):]
        parts = rest.split("/", 1)
        parts[0] = new_slug
        return _PROJECTS_PREFIX + "/".join(parts)

    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as dst:
        for m in members:
            m2 = tarfile.TarInfo(rekeyed(m.name))
            m2.mtime = m.mtime
            m2.mode = m.mode
            m2.type = m.type
            m2.linkname = m.linkname
            m2.uid, m2.gid = m.uid, m.gid
            m2.uname, m2.gname = m.uname, m.gname
            if m.isfile():
                payload = new_payloads.get(m.name)
                if payload is None:
                    payload = src.extractfile(m).read()
                m2.size = len(payload)
                dst.addfile(m2, io.BytesIO(payload))
            else:
                dst.addfile(m2)
    return out.getvalue()


def _truncate(raw: bytes, until_uuid: str, display_name: str) -> bytes:
    """Prefix-cut a transcript after the line bearing ``until_uuid``, then
    repair kept ``leafUuid`` pointers that reference dropped entries."""
    lines = raw.decode("utf-8", errors="replace").splitlines()
    kept: list[str] = []
    kept_uuids: set[str] = set()
    found = False
    for line in lines:
        kept.append(line)
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        u = entry.get("uuid")
        if u:
            kept_uuids.add(u)
        if u == until_uuid:
            found = True
            break
    if not found:
        raise ValueError(
            f"session_restore_until uuid {until_uuid!r} not found in "
            f"newest transcript {display_name!r}"
        )
    repaired: list[str] = []
    for line in kept:
        try:
            entry = json.loads(line)
        except ValueError:
            repaired.append(line)
            continue
        leaf = entry.get("leafUuid")
        if leaf and leaf not in kept_uuids:
            entry["leafUuid"] = until_uuid
            repaired.append(json.dumps(entry, separators=(",", ":")))
        else:
            repaired.append(line)
    return ("\n".join(repaired) + "\n").encode()
