import pytest

from sibling_pins import compatible_range, update_pyproject


class TestCompatibleRange:
    def test_pre_1_0(self):
        assert compatible_range("0.1.0") == ">=0.1,<0.2"
        assert compatible_range("0.5.3") == ">=0.5,<0.6"
        assert compatible_range("0.9.99") == ">=0.9,<0.10"

    def test_1_x(self):
        assert compatible_range("1.0.0") == ">=1.0,<2"
        assert compatible_range("1.5.3") == ">=1.5,<2"

    def test_2_x(self):
        assert compatible_range("2.3.4") == ">=2.3,<3"


class TestUpdatePyproject:
    def test_replaces_bare_dep(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_text(
            '[project]\n'
            'name = "host"\n'
            'dependencies = [\n'
            '    "optio-core",\n'
            '    "motor>=3.3.0",\n'
            ']\n'
        )
        changed = update_pyproject(p, "optio-core", "0.2.0")
        assert changed is True
        new = p.read_text()
        assert '"optio-core>=0.2,<0.3"' in new
        assert '"motor>=3.3.0"' in new  # other deps untouched

    def test_replaces_existing_range(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_text(
            'dependencies = [\n'
            '    "optio-core>=0.1,<0.2",\n'
            ']\n'
        )
        update_pyproject(p, "optio-core", "0.3.0")
        new = p.read_text()
        assert '"optio-core>=0.3,<0.4"' in new
        assert '"optio-core>=0.1,<0.2"' not in new

    def test_preserves_extras(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_text(
            'dependencies = [\n'
            '    "optio-core[redis]",\n'
            ']\n'
        )
        update_pyproject(p, "optio-core", "0.2.0")
        new = p.read_text()
        assert '"optio-core[redis]>=0.2,<0.3"' in new

    def test_preserves_extras_with_existing_range(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_text(
            'dependencies = [\n'
            '    "optio-core[redis]>=0.1,<0.2",\n'
            ']\n'
        )
        update_pyproject(p, "optio-core", "0.5.0")
        new = p.read_text()
        assert '"optio-core[redis]>=0.5,<0.6"' in new

    def test_no_match_returns_false(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_text(
            'dependencies = [\n'
            '    "motor>=3.3.0",\n'
            ']\n'
        )
        assert update_pyproject(p, "optio-core", "0.2.0") is False
        # File untouched.
        assert '"motor>=3.3.0"' in p.read_text()

    def test_duplicate_match_errors(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_text(
            'dependencies = [\n'
            '    "optio-core",\n'
            '    "optio-core[redis]",\n'
            ']\n'
        )
        with pytest.raises(SystemExit, match="multiple matches"):
            update_pyproject(p, "optio-core", "0.2.0")

    def test_does_not_match_prefix_of_other_pkg(self, tmp_path):
        """`optio-core` must not match `optio-core-extras` or `optio-corex`."""
        p = tmp_path / "pyproject.toml"
        p.write_text(
            'dependencies = [\n'
            '    "optio-core-extras>=0.1",\n'
            '    "optio-corex",\n'
            ']\n'
        )
        assert update_pyproject(p, "optio-core", "0.2.0") is False
        text = p.read_text()
        assert '"optio-core-extras>=0.1"' in text
        assert '"optio-corex"' in text
