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
        # Confirm git tag was created (annotated form).
        assert any(c[:3] == ["git", "tag", "-a"] and "fake-py-v0.1.0" in c for c in commands)
        # BUMP=none with no sibling pin changes: no commit (nothing to commit).
        assert not any(c[:2] == ["git", "commit"] for c in commands)

    def test_dist_wipe_handles_nested_subdirs(self, tmp_path: Path):
        """Regression: dist-wipe must recursively remove subdirectories
        (e.g. optio-contracts ships dist/schemas/). The original loop-and-unlink
        impl failed with IsADirectoryError."""
        packages = tmp_path / "packages"
        packages.mkdir()
        pkg = packages / "fake-py"
        pkg.mkdir()
        (pkg / "pyproject.toml").write_text(
            '[project]\nname = "fake-py"\nversion = "0.1.0"\n'
            'description = "x"\nreadme = "README.md"\ndependencies = []\n'
        )
        (pkg / "README.md").write_text("# fake-py\n")
        # Pre-create a dist with nested subdirs (the failure mode).
        d = pkg / "dist"
        d.mkdir()
        (d / "old.whl").write_text("")
        (d / "schemas").mkdir()
        (d / "schemas" / "thing.json").write_text("")

        commands = []
        def fake_run(cmd, **kw):
            commands.append(cmd)
            stdout = ""
            if cmd == ["git", "diff", "--quiet"] or cmd == ["git", "diff", "--cached", "--quiet"]:
                return MagicMock(returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
                stdout = "main\n"
            if cmd == ["git", "rev-parse", "HEAD"] or cmd == ["git", "rev-parse", "@{u}"]:
                stdout = "abc\n"
            return MagicMock(returncode=0, stdout=stdout)

        def fake_check_call(cmd, **kw):
            commands.append(cmd)
            if len(cmd) >= 3 and cmd[1:3] == ["-m", "build"]:
                cwd_dir = Path(kw.get("cwd", "."))
                d2 = cwd_dir / "dist"
                d2.mkdir(exist_ok=True)
                (d2 / "fake_py-0.1.0-py3-none-any.whl").write_text("")

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

        # After wipe + fake build, dist should contain only the new artifact.
        contents = sorted(p.name for p in (pkg / "dist").iterdir())
        assert contents == ["fake_py-0.1.0-py3-none-any.whl"]

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


class TestReleaseWire:
    def test_releases_both_packages_with_same_version(self, tmp_path: Path):
        """`release_wire` must bump optio-contracts (TS) and optio-core (Py)
        to the same new version, update Python sibling pins, and create
        two tags in a single commit."""
        packages = tmp_path / "packages"
        packages.mkdir()
        # optio-contracts (TS)
        contracts = packages / "optio-contracts"
        contracts.mkdir()
        (contracts / "package.json").write_text(
            '{\n  "name": "optio-contracts",\n  "version": "0.1.0"\n}\n'
        )
        # optio-core (Python)
        core = packages / "optio-core"
        core.mkdir()
        (core / "pyproject.toml").write_text(
            '[project]\nname = "optio-core"\nversion = "0.1.0"\n'
            'description = "x"\nreadme = "README.md"\ndependencies = []\n'
        )
        (core / "README.md").write_text("# optio-core\n")
        # optio-host (Python sibling pinning optio-core)
        host = packages / "optio-host"
        host.mkdir()
        (host / "pyproject.toml").write_text(
            '[project]\nname = "optio-host"\nversion = "0.1.0"\n'
            'description = "x"\nreadme = "README.md"\n'
            'dependencies = [\n    "optio-core",\n]\n'
        )
        (host / "README.md").write_text("# optio-host\n")

        commands = []
        def fake_run(cmd, **kw):
            commands.append(cmd)
            stdout = ""
            if cmd == ["git", "diff", "--quiet"] or cmd == ["git", "diff", "--cached", "--quiet"]:
                return MagicMock(returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
                stdout = "main\n"
            if cmd == ["git", "rev-parse", "HEAD"] or cmd == ["git", "rev-parse", "@{u}"]:
                stdout = "abc\n"
            return MagicMock(returncode=0, stdout=stdout)

        def fake_check_call(cmd, **kw):
            commands.append(cmd)
            if len(cmd) >= 3 and cmd[1:3] == ["-m", "build"]:
                cwd_dir = Path(kw.get("cwd", "."))
                d = cwd_dir / "dist"
                d.mkdir(exist_ok=True)
                (d / "optio_core-0.2.0-py3-none-any.whl").write_text("")
                (d / "optio_core-0.2.0.tar.gz").write_text("")

        from run import release_wire  # late import; symbol added in this task

        with patch("run.npm_latest", return_value=None), \
             patch("run.pypi_latest", return_value=None), \
             patch("run.subprocess.run", side_effect=fake_run), \
             patch("run.subprocess.check_call", side_effect=fake_check_call):
            release_wire(
                repo_root=tmp_path,
                bump="minor",
                skip_tests=True,
                skip_fetch=True,
                skip_publish=True,
                skip_push=True,
            )

        # Both source versions bumped to 0.2.0.
        assert '"version": "0.2.0"' in (contracts / "package.json").read_text()
        assert 'version = "0.2.0"' in (core / "pyproject.toml").read_text()
        # Sibling pin in optio-host updated.
        assert '"optio-core>=0.2,<0.3"' in (host / "pyproject.toml").read_text()
        # Two tags issued (annotated form: git tag -a <name> -m <msg>).
        tag_cmds = [c for c in commands if c[:3] == ["git", "tag", "-a"]]
        tag_names = [c[3] for c in tag_cmds]
        assert "optio-contracts-v0.2.0" in tag_names
        assert "optio-core-v0.2.0" in tag_names
        # Single commit covering both packages + sibling (BUMP=minor changed version).
        commit_cmds = [c for c in commands if c[:2] == ["git", "commit"]]
        assert len(commit_cmds) == 1
        assert "release(wire): 0.2.0" in commit_cmds[0][-1]


class TestReleaseAll:
    def test_no_pending_returns_message(self, tmp_path: Path):
        """When every source version matches what's published, refuse."""
        packages = tmp_path / "packages"
        packages.mkdir()
        for name, file_, body in [
            ("optio-contracts", "package.json", '{"name":"optio-contracts","version":"0.1.0"}\n'),
            ("optio-core", "pyproject.toml", '[project]\nname = "optio-core"\nversion = "0.1.0"\ndependencies=[]\n'),
        ]:
            d = packages / name
            d.mkdir()
            (d / file_).write_text(body)

        from run import release_all

        with patch("run.npm_latest", return_value="0.1.0"), \
             patch("run.pypi_latest", return_value="0.1.0"), \
             patch("run.TS_PUBLISHABLE", ["optio-contracts"]), \
             patch("run.PY_PUBLISHABLE", ["optio-core"]):
            with pytest.raises(SystemExit, match="nothing pending"):
                release_all(repo_root=tmp_path, skip_tests=True, skip_fetch=True,
                            skip_publish=True, skip_push=True)


class TestResume:
    def test_resumes_from_publish_when_tag_and_dist_exist_but_registry_lags(
        self, tmp_path: Path
    ):
        """If git tag exists, dist artifact exists, and registry doesn't have
        the version yet — resume from the publish step."""
        packages = tmp_path / "packages"
        packages.mkdir()
        pkg = packages / "fake-py"
        pkg.mkdir()
        (pkg / "pyproject.toml").write_text(
            '[project]\nname = "fake-py"\nversion = "0.2.0"\n'
            'description = "x"\nreadme = "README.md"\ndependencies = []\n'
        )
        (pkg / "README.md").write_text("# fake-py\n")
        (pkg / "dist").mkdir()
        (pkg / "dist" / "fake_py-0.2.0-py3-none-any.whl").write_text("")
        (pkg / "dist" / "fake_py-0.2.0.tar.gz").write_text("")

        from run import resume

        commands = []
        def fake_run(cmd, **kw):
            if cmd == ["git", "tag", "-l", "fake-py-v0.2.0"]:
                return MagicMock(returncode=0, stdout="fake-py-v0.2.0\n")
            return MagicMock(returncode=0, stdout="")

        with patch("run.pypi_latest", return_value="0.1.0"), \
             patch("run.subprocess.run", side_effect=fake_run), \
             patch("run.subprocess.check_call", side_effect=lambda *a, **kw: commands.append(a[0])):
            resume(repo_root=tmp_path, pkg_name="fake-py", skip_publish=False, skip_push=True)

        # Should have invoked twine upload.
        assert any(len(c) >= 3 and c[1:3] == ["-m", "twine"] and "upload" in c for c in commands)

    def test_refuses_when_state_ambiguous(self, tmp_path: Path):
        """No tag, no dist, version matches registry: nothing to resume."""
        packages = tmp_path / "packages"
        packages.mkdir()
        pkg = packages / "fake-py"
        pkg.mkdir()
        (pkg / "pyproject.toml").write_text(
            '[project]\nname = "fake-py"\nversion = "0.1.0"\n'
            'description = "x"\nreadme = "README.md"\ndependencies = []\n'
        )
        (pkg / "README.md").write_text("# fake-py\n")

        from run import resume

        def fake_run(cmd, **kw):
            return MagicMock(returncode=0, stdout="")

        with patch("run.pypi_latest", return_value=None), \
             patch("run.subprocess.run", side_effect=fake_run):
            with pytest.raises(SystemExit, match="nothing to resume"):
                resume(repo_root=tmp_path, pkg_name="fake-py", skip_publish=True, skip_push=True)
