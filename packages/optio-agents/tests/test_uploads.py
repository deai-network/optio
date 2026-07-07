import pytest
from optio_agents.uploads import safe_upload_relpath


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
