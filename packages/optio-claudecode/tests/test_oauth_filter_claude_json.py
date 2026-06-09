"""seed_signature must ignore the relocated .claude/.claude.json (volatile:
timestamps/userID differ between good seeds), like it already ignores the old
root .claude.json."""
import io
import tarfile

from optio_claudecode import oauth


def _targz(names: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in names.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_relocated_claude_json_is_filtered_out():
    blob = _targz({
        ".claude/.claude.json": b"{}",
        ".claude/settings.json": b'{"a": 1}',
        ".claude/agents/x.md": b"hello",
    })
    sig = oauth.seed_signature(blob)
    assert ".claude/.claude.json" not in sig["members"]
    assert ".claude.json" not in sig["members"]
    assert ".claude/agents/x.md" in sig["members"]
    assert sig["settingsKeys"] == ["a"]  # settings.json still parsed
