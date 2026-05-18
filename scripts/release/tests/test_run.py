"""Tests for the release orchestrator. Subprocess calls are mocked.

These tests exercise the orchestration logic — argument parsing, preflight
sequencing, sibling pin updates, command construction — without actually
invoking git, pnpm, twine, or the network.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch, call, MagicMock

import pytest


from run import (  # noqa: E402
    PackageInfo,
    discover_package,
    preflight,
    release_per_package,
)


@pytest.fixture
def py_pkg(tmp_path: Path) -> Path:
    pkg = tmp_path / "packages" / "fake-pkg"
    pkg.mkdir(parents=True)
    (pkg / "pyproject.toml").write_text(
        '[project]\n'
        'name = "fake-pkg"\n'
        'version = "0.1.0"\n'
        'description = "x"\n'
    )
    (pkg / "README.md").write_text("# fake-pkg\n")
    return pkg


@pytest.fixture
def ts_pkg(tmp_path: Path) -> Path:
    pkg = tmp_path / "packages" / "fake-ts"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text(
        '{\n  "name": "fake-ts",\n  "version": "0.1.0"\n}\n'
    )
    return pkg


class TestDiscoverPackage:
    def test_python_package(self, py_pkg: Path):
        info = discover_package(py_pkg.parent.parent, "fake-pkg")
        assert info.kind == "python"
        assert info.dist_name == "fake-pkg"
        assert info.current_version == "0.1.0"

    def test_ts_package(self, ts_pkg: Path):
        info = discover_package(ts_pkg.parent.parent, "fake-ts")
        assert info.kind == "ts"
        assert info.dist_name == "fake-ts"
        assert info.current_version == "0.1.0"

    def test_missing(self, tmp_path: Path):
        (tmp_path / "packages").mkdir()
        with pytest.raises(SystemExit, match="not found"):
            discover_package(tmp_path, "missing")


class TestPreflight:
    def test_passes_clean_main_uptodate(self):
        def fake_run(cmd, **kw):
            if cmd == ["git", "diff", "--quiet"]:
                return MagicMock(returncode=0)
            if cmd == ["git", "diff", "--cached", "--quiet"]:
                return MagicMock(returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
                return MagicMock(returncode=0, stdout="main\n")
            if cmd[:2] == ["git", "fetch"]:
                return MagicMock(returncode=0)
            if cmd == ["git", "rev-parse", "HEAD"]:
                return MagicMock(returncode=0, stdout="abc\n")
            if cmd == ["git", "rev-parse", "@{u}"]:
                return MagicMock(returncode=0, stdout="abc\n")
            raise AssertionError(f"unexpected cmd: {cmd}")
        with patch("run.subprocess.run", side_effect=fake_run):
            # Should not raise.
            preflight(skip_tests=True, skip_fetch=False)

    def test_fails_when_tree_dirty(self):
        def fake_run(cmd, **kw):
            if cmd == ["git", "diff", "--quiet"]:
                return MagicMock(returncode=1)
            return MagicMock(returncode=0, stdout="")
        with patch("run.subprocess.run", side_effect=fake_run):
            with pytest.raises(SystemExit, match="working tree is dirty"):
                preflight(skip_tests=True, skip_fetch=True)

    def test_fails_when_not_on_main(self):
        def fake_run(cmd, **kw):
            if cmd == ["git", "diff", "--quiet"]:
                return MagicMock(returncode=0)
            if cmd == ["git", "diff", "--cached", "--quiet"]:
                return MagicMock(returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
                return MagicMock(returncode=0, stdout="feature\n")
            return MagicMock(returncode=0, stdout="")
        with patch("run.subprocess.run", side_effect=fake_run):
            with pytest.raises(SystemExit, match="not on main"):
                preflight(skip_tests=True, skip_fetch=True)

    def test_fails_when_behind_origin(self):
        def fake_run(cmd, **kw):
            if cmd == ["git", "diff", "--quiet"]:
                return MagicMock(returncode=0)
            if cmd == ["git", "diff", "--cached", "--quiet"]:
                return MagicMock(returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
                return MagicMock(returncode=0, stdout="main\n")
            if cmd == ["git", "rev-parse", "HEAD"]:
                return MagicMock(returncode=0, stdout="abc\n")
            if cmd == ["git", "rev-parse", "@{u}"]:
                return MagicMock(returncode=0, stdout="def\n")
            return MagicMock(returncode=0, stdout="")
        with patch("run.subprocess.run", side_effect=fake_run):
            with pytest.raises(SystemExit, match="not up to date"):
                preflight(skip_tests=True, skip_fetch=True)


class TestReleasePerPackage:
    """Higher-level orchestration test: confirms the sequence of calls
    made by `release_per_package` for a Python sibling-pinning case.

    We mock all subprocess calls and registry queries so the test runs
    without any network or git state.
    """

    def test_python_first_release_bump_none(self, tmp_path: Path, monkeypatch):
        # Set up a fake repo
        packages = tmp_path / "packages"
        packages.mkdir()
        pkg = packages / "fake-py"
        pkg.mkdir()
        (pkg / "pyproject.toml").write_text(
            '[project]\nname = "fake-py"\nversion = "0.1.0"\n'
            'description = "x"\nreadme = "README.md"\n'
            'dependencies = []\n'
        )
        (pkg / "README.md").write_text("# fake-py\n")

        commands = []
        def fake_run(cmd, **kw):
            commands.append(cmd)
            stdout = ""
            if cmd == ["git", "diff", "--quiet"]:
                return MagicMock(returncode=0)
            if cmd == ["git", "diff", "--cached", "--quiet"]:
                return MagicMock(returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
                stdout = "main\n"
            if cmd == ["git", "rev-parse", "HEAD"]:
                stdout = "abc\n"
            if cmd == ["git", "rev-parse", "@{u}"]:
                stdout = "abc\n"
            return MagicMock(returncode=0, stdout=stdout)

        def fake_check_call(cmd, **kw):
            commands.append(cmd)
            # Simulate `python -m build` producing an artifact under cwd/dist
            if len(cmd) >= 3 and cmd[1:3] == ["-m", "build"]:
                cwd_dir = Path(kw.get("cwd", "."))
                d = cwd_dir / "dist"
                d.mkdir(exist_ok=True)
                (d / "fake_py-0.1.0-py3-none-any.whl").write_text("")
                (d / "fake_py-0.1.0.tar.gz").write_text("")

        with patch("run.pypi_latest", return_value=None), \
             patch("run.subprocess.run", side_effect=fake_run), \
             patch("run.subprocess.check_call", side_effect=fake_check_call):
            release_per_package(
                repo_root=tmp_path,
                pkg_name="fake-py",
                bump="none",
                skip_tests=True,
                skip_fetch=True,
                skip_publish=True,
                skip_push=True,
            )

        # Confirm the build command was issued (any python executable + -m build).
        assert any(len(c) >= 3 and c[1:3] == ["-m", "build"] for c in commands)
        # Confirm git tag was created.
        assert any(c[:2] == ["git", "tag"] and "fake-py-v0.1.0" in c for c in commands)
        # Confirm git commit was created.
        assert any(c[:2] == ["git", "commit"] for c in commands)

    def test_rejects_bump_none_when_already_published(self, tmp_path: Path):
        packages = tmp_path / "packages"
        packages.mkdir()
        pkg = packages / "fake-py"
        pkg.mkdir()
        (pkg / "pyproject.toml").write_text(
            '[project]\nname = "fake-py"\nversion = "0.1.0"\n'
            'description = "x"\nreadme = "README.md"\ndependencies = []\n'
        )
        (pkg / "README.md").write_text("# fake-py\n")

        def fake_run(cmd, **kw):
            if cmd == ["git", "diff", "--quiet"]:
                return MagicMock(returncode=0)
            if cmd == ["git", "diff", "--cached", "--quiet"]:
                return MagicMock(returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
                return MagicMock(returncode=0, stdout="main\n")
            if cmd == ["git", "rev-parse", "HEAD"]:
                return MagicMock(returncode=0, stdout="abc\n")
            if cmd == ["git", "rev-parse", "@{u}"]:
                return MagicMock(returncode=0, stdout="abc\n")
            return MagicMock(returncode=0, stdout="")

        with patch("run.pypi_latest", return_value="0.1.0"), \
             patch("run.subprocess.run", side_effect=fake_run):
            with pytest.raises(SystemExit, match="BUMP=none .* already published"):
                release_per_package(
                    repo_root=tmp_path,
                    pkg_name="fake-py",
                    bump="none",
                    skip_tests=True,
                    skip_fetch=True,
                    skip_publish=True,
                    skip_push=True,
                )
