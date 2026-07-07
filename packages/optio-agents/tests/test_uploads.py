import pytest
from optio_agents.uploads import materialize, safe_upload_relpath


def test_preserves_original_name_with_spaces_and_unicode():
    assert safe_upload_relpath("My Report (v2).md") == "uploads/My Report (v2).md"
    assert safe_upload_relpath("résumé.pdf") == "uploads/résumé.pdf"


def test_strips_directory_components():
    assert safe_upload_relpath("/etc/passwd") == "uploads/passwd"
    assert safe_upload_relpath("a/b/c.txt") == "uploads/c.txt"


def test_rejects_traversal_and_empty():
    for bad in ["..", ".", "", "   ", "../../x/..", "/"]:
        with pytest.raises(ValueError):
            safe_upload_relpath(bad)


class _FakeHost:
    def __init__(self):
        self.written = {}

    async def put_file_to_host(self, data, path):
        self.written[path] = data


async def test_materialize_writes_and_returns_relpath():
    h = _FakeHost()
    rel = await materialize(h, "/wd", "notes.md", b"hello")
    assert rel == "uploads/notes.md"
    assert h.written["/wd/uploads/notes.md"] == b"hello"


async def test_materialize_fires_on_upload_with_relpath():
    h = _FakeHost()
    seen = []

    async def cb(hook_ctx, path):
        seen.append((hook_ctx, path))

    rel = await materialize(h, "/wd", "a b.txt", b"x", hook_ctx="HC", on_upload=cb)
    assert rel == "uploads/a b.txt"
    assert seen == [("HC", "uploads/a b.txt")]


async def test_materialize_swallows_on_upload_error():
    h = _FakeHost()

    async def cb(hook_ctx, path):
        raise RuntimeError("boom")

    rel = await materialize(h, "/wd", "f.txt", b"x", hook_ctx=None, on_upload=cb)
    assert rel == "uploads/f.txt"  # write succeeded, callback error not fatal
