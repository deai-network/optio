import json
import re
from pathlib import Path

import pytest

from bump import compute_new_version, read_version, write_version


class TestComputeNewVersion:
    def test_patch_bump(self):
        assert compute_new_version("0.1.0", "patch") == "0.1.1"
        assert compute_new_version("1.5.3", "patch") == "1.5.4"

    def test_minor_bump(self):
        assert compute_new_version("0.1.0", "minor") == "0.2.0"
        assert compute_new_version("1.5.3", "minor") == "1.6.0"

    def test_minor_bump_resets_patch(self):
        assert compute_new_version("0.1.7", "minor") == "0.2.0"

    def test_major_bump_on_1x(self):
        assert compute_new_version("1.5.3", "major") == "2.0.0"

    def test_major_bump_rejected_on_pre_1_0(self):
        with pytest.raises(SystemExit, match=r"pre-1\.0.*BUMP=minor.*BUMP=promote-to-1\.0"):
            compute_new_version("0.1.0", "major")

    def test_promote_to_1_0(self):
        assert compute_new_version("0.5.3", "promote-to-1.0") == "1.0.0"

    def test_promote_to_1_0_rejected_on_1x(self):
        with pytest.raises(SystemExit, match="already 1.x"):
            compute_new_version("1.0.0", "promote-to-1.0")

    def test_none_keeps_version(self):
        assert compute_new_version("0.1.0", "none") == "0.1.0"

    def test_unknown_bump(self):
        with pytest.raises(SystemExit, match="unknown BUMP"):
            compute_new_version("0.1.0", "wibble")

    def test_unsupported_version_format(self):
        with pytest.raises(SystemExit, match="unsupported version format"):
            compute_new_version("0.1.0-rc.1", "patch")


class TestReadWriteVersion:
    def test_read_pyproject(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "pyproject.toml").write_text(
            '[project]\nname = "p"\nversion = "0.3.4"\n'
        )
        path, ver = read_version(pkg)
        assert path == pkg / "pyproject.toml"
        assert ver == "0.3.4"

    def test_read_package_json(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "package.json").write_text(
            json.dumps({"name": "p", "version": "1.2.3"}, indent=2)
        )
        path, ver = read_version(pkg)
        assert path == pkg / "package.json"
        assert ver == "1.2.3"

    def test_read_missing(self, tmp_path):
        with pytest.raises(SystemExit, match="no pyproject.toml or package.json"):
            read_version(tmp_path)

    def test_write_pyproject_preserves_rest(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        original = (
            '[project]\n'
            'name = "p"\n'
            'version = "0.1.0"\n'
            'description = "thing"\n'
        )
        (pkg / "pyproject.toml").write_text(original)
        write_version(pkg / "pyproject.toml", "0.2.0")
        new = (pkg / "pyproject.toml").read_text()
        assert 'version = "0.2.0"' in new
        assert 'description = "thing"' in new
        assert 'name = "p"' in new

    def test_write_package_json_preserves_rest(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        original = json.dumps(
            {"name": "p", "version": "0.1.0", "description": "thing"},
            indent=2,
        )
        (pkg / "package.json").write_text(original + "\n")
        write_version(pkg / "package.json", "0.2.0")
        new = (pkg / "package.json").read_text()
        assert '"version": "0.2.0"' in new
        assert '"description": "thing"' in new
        # File should still be valid JSON
        json.loads(new)
