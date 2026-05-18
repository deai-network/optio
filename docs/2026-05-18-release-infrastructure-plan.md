# Release Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the eight publishable packages in the optio monorepo releasable to npm.org and PyPI via simple `make release-<pkg> BUMP=<level>` commands, with full preflight, sibling-pin auto-update, and wire-locked behavior for `optio-contracts` + `optio-core`.

**Architecture:** Three layers. (1) Pure Python helpers under `scripts/release/` (`bump.py`, `sibling_pins.py`, `registry.py`) — TDD with stdlib-only deps. (2) A Python orchestrator `scripts/release/run.py` with subcommands (`per-package`, `wire`, `all`, `resume`) that calls the helpers and shells out to git, pnpm, twine. (3) Makefile targets that delegate to the orchestrator one-liners. Metadata backfill (descriptions, repository, README cross-link, Trove classifiers) happens before any orchestration is wired up so the packages are publishable on day one.

**Tech Stack:** Python 3.11+ stdlib (`tomllib`, `urllib.request`, `subprocess`, `json`, `re`), pytest, GNU Make, pnpm 11, twine, `python -m build`. No new third-party dependencies.

**Spec:** `docs/2026-05-18-release-infrastructure-design.md` (commit `d906b70`).

---

## File Structure

### New files
- `scripts/release/__init__.py` — empty marker so the directory is importable as a package
- `scripts/release/bump.py` — semver bump computation (pure)
- `scripts/release/sibling_pins.py` — Python sibling pin updater
- `scripts/release/registry.py` — npm + PyPI latest-version queries
- `scripts/release/run.py` — orchestrator with subcommands `preflight`, `per-package`, `wire`, `all`, `resume`
- `scripts/release/tests/__init__.py` — empty
- `scripts/release/tests/conftest.py` — `sys.path` shim so tests can import the helpers
- `scripts/release/tests/test_bump.py`
- `scripts/release/tests/test_sibling_pins.py`
- `scripts/release/tests/test_registry.py`
- `scripts/release/tests/test_run.py` — integration-style tests for the orchestrator with mocked subprocess
- `packages/optio-host/README.md`
- `packages/optio-opencode/README.md`

### Modified files
- `packages/optio-contracts/package.json` — backfill metadata + remove `"private"`
- `packages/optio-ui/package.json` — backfill metadata + remove `"private"` + replace `link:` deps
- `packages/optio-api/package.json` — backfill metadata + remove `"private"`
- `packages/optio-dashboard/package.json` — backfill metadata + remove `"private"`
- `packages/optio-core/pyproject.toml` — add `readme`, `authors`, `[project.urls]`, classifiers
- `packages/optio-host/pyproject.toml` — same as above, plus tighten `optio-core` pin
- `packages/optio-opencode/pyproject.toml` — same as above, plus tighten `optio-core` + `optio-host` pins
- `packages/optio-demo/pyproject.toml` — same as above, plus tighten `optio-core[redis]` + `optio-opencode` pins
- `Makefile` — add `release-*`, `release-wire`, `release-all`, `resume-release-*`, `clean-dist-*` targets

### Files left untouched
- All `optio-demo/interop/` files (test harness; not published)
- Generated stubs in `packages/optio-core/src/optio_core/_generated/` and `packages/optio-api/src/_generated/`
- Existing test infrastructure under `packages/*/tests/`

---

## Task 1: `scripts/release/bump.py` — semver computation helper

**Files:**
- Create: `scripts/release/__init__.py`
- Create: `scripts/release/bump.py`
- Create: `scripts/release/tests/__init__.py`
- Create: `scripts/release/tests/conftest.py`
- Create: `scripts/release/tests/test_bump.py`

- [ ] **Step 1: Create package markers and test shim**

Create `scripts/release/__init__.py` as an empty file. Create `scripts/release/tests/__init__.py` as an empty file. Create `scripts/release/tests/conftest.py` with:

```python
import sys
from pathlib import Path

# Make scripts/release/ importable without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
```

- [ ] **Step 2: Write the failing test file**

Create `scripts/release/tests/test_bump.py`:

```python
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
```

- [ ] **Step 3: Run tests to confirm they fail (module not yet written)**

```bash
.venv/bin/pytest scripts/release/tests/test_bump.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'bump'`.

- [ ] **Step 4: Implement `scripts/release/bump.py`**

Create `scripts/release/bump.py`:

```python
"""Compute and apply semver bumps to a package's version file."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Patterns that match the version line in each file type.
# Group 1: prefix (whitespace + key + ': "'  or '= "')
# Group 2: current version literal
_VERSION_PATTERNS = {
    "package.json": re.compile(r'(\s*"version"\s*:\s*)"([^"]+)"'),
    "pyproject.toml": re.compile(r'^(version\s*=\s*)"([^"]+)"', re.M),
}

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def read_version(package_dir: Path) -> tuple[Path, str]:
    """Locate the version file for `package_dir` and return (path, current_version)."""
    for fname in ("pyproject.toml", "package.json"):
        path = package_dir / fname
        if path.exists():
            text = path.read_text()
            m = _VERSION_PATTERNS[fname].search(text)
            if not m:
                raise SystemExit(f"{path}: no version field found")
            return path, m.group(2)
    raise SystemExit(f"{package_dir}: no pyproject.toml or package.json found")


def _parse_semver(v: str) -> tuple[int, int, int]:
    m = _SEMVER_RE.match(v)
    if not m:
        raise SystemExit(f"unsupported version format: {v!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def compute_new_version(current: str, bump: str) -> str:
    major, minor, patch = _parse_semver(current)
    if bump == "none":
        return current
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "major":
        if major == 0:
            raise SystemExit(
                "package is pre-1.0; use BUMP=minor for breaking changes, "
                "or BUMP=promote-to-1.0 to graduate to 1.0"
            )
        return f"{major + 1}.0.0"
    if bump == "promote-to-1.0":
        if major != 0:
            raise SystemExit(f"package is already 1.x (current: {current})")
        return "1.0.0"
    raise SystemExit(f"unknown BUMP: {bump!r}")


def write_version(path: Path, new_version: str) -> None:
    fname = path.name
    if fname not in _VERSION_PATTERNS:
        raise SystemExit(f"{path}: unsupported file type")
    text = path.read_text()
    new_text, n = _VERSION_PATTERNS[fname].subn(
        lambda m: f'{m.group(1)}"{new_version}"', text, count=1
    )
    if n != 1:
        raise SystemExit(f"{path}: failed to rewrite version line")
    path.write_text(new_text)


def main() -> None:
    p = argparse.ArgumentParser(description="Bump a package version.")
    p.add_argument("package_dir", type=Path)
    p.add_argument("bump", choices=["patch", "minor", "major", "promote-to-1.0", "none"])
    args = p.parse_args()
    version_file, current = read_version(args.package_dir)
    new_version = compute_new_version(current, args.bump)
    if new_version != current:
        write_version(version_file, new_version)
    # stdout is the new version, so callers can capture it.
    print(new_version)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest scripts/release/tests/test_bump.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Smoke-test the CLI on a sample**

```bash
mkdir -p /tmp/bump-smoke && cd /tmp/bump-smoke
cat > pyproject.toml <<'EOF'
[project]
name = "x"
version = "0.1.0"
EOF
$OLDPWD/.venv/bin/python $OLDPWD/scripts/release/bump.py /tmp/bump-smoke minor
cat pyproject.toml
cd $OLDPWD
```

Expected: prints `0.2.0`, file shows `version = "0.2.0"`.

- [ ] **Step 7: Commit**

```bash
git add scripts/release/__init__.py scripts/release/bump.py scripts/release/tests/
git commit -m "release: add bump.py helper for semver computation"
```

---

## Task 2: `scripts/release/sibling_pins.py` — Python sibling pin updater

**Files:**
- Create: `scripts/release/sibling_pins.py`
- Create: `scripts/release/tests/test_sibling_pins.py`

- [ ] **Step 1: Write the failing tests**

Create `scripts/release/tests/test_sibling_pins.py`:

```python
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/pytest scripts/release/tests/test_sibling_pins.py -v
```

Expected: `ModuleNotFoundError: No module named 'sibling_pins'`.

- [ ] **Step 3: Implement `scripts/release/sibling_pins.py`**

```python
"""Update Python sibling-package pins after a release."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def compatible_range(version: str) -> str:
    """Return a pin range allowing non-breaking updates from `version`."""
    parts = version.split(".")
    major, minor = int(parts[0]), int(parts[1])
    if major == 0:
        return f">=0.{minor},<0.{minor + 1}"
    return f">={major}.{minor},<{major + 1}"


def _dep_pattern(pkg: str) -> re.Pattern[str]:
    # Match a dep entry: leading whitespace, opening quote, exact package name,
    # optional `[extras]`, anything else up to the closing quote, then a comma
    # and end of line. Anchor the package name so `optio-core` doesn't match
    # `optio-core-extras` or `optio-corex` — the next char must be one of:
    #   "  (no extras, no version constraint)
    #   [  (extras follow)
    #   >, <, ~, =, !, space  (version constraint follows)
    return re.compile(
        r'^(?P<indent>\s*)"' + re.escape(pkg) +
        r'(?P<extras>\[[^\]]+\])?(?P<rest>[>=<!~ ][^"]*)?",\s*$',
        re.M,
    )


def update_pyproject(path: Path, target_pkg: str, new_version: str) -> bool:
    """Return True if the file was modified."""
    text = path.read_text()
    new_range = compatible_range(new_version)
    pat = _dep_pattern(target_pkg)
    matches = pat.findall(text)
    if len(matches) > 1:
        raise SystemExit(
            f"{path}: multiple matches for {target_pkg} — refusing to edit"
        )
    if len(matches) == 0:
        return False

    def repl(m: re.Match[str]) -> str:
        extras = m.group("extras") or ""
        return f'{m.group("indent")}"{target_pkg}{extras}{new_range}",'

    new_text, n = pat.subn(repl, text)
    if n != 1:
        raise SystemExit(f"{path}: substitution failed unexpectedly")
    path.write_text(new_text)
    return True


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> None:
    p = argparse.ArgumentParser(
        description="Update Python sibling pins after a package release."
    )
    p.add_argument("target_pkg")
    p.add_argument("new_version")
    args = p.parse_args()

    packages_dir = _repo_root() / "packages"
    target_dir = packages_dir / args.target_pkg

    changed = []
    for pkg_dir in sorted(packages_dir.iterdir()):
        if not pkg_dir.is_dir():
            continue
        pyproject = pkg_dir / "pyproject.toml"
        if not pyproject.exists():
            continue
        if pkg_dir == target_dir:
            continue
        if update_pyproject(pyproject, args.target_pkg, args.new_version):
            changed.append(pkg_dir.name)

    for name in changed:
        print(name)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest scripts/release/tests/test_sibling_pins.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/release/sibling_pins.py scripts/release/tests/test_sibling_pins.py
git commit -m "release: add sibling_pins.py — auto-update Python sibling pins"
```

---

## Task 3: `scripts/release/registry.py` — npm + PyPI latest-version queries

**Files:**
- Create: `scripts/release/registry.py`
- Create: `scripts/release/tests/test_registry.py`

- [ ] **Step 1: Write the failing tests**

Create `scripts/release/tests/test_registry.py`:

```python
import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from registry import npm_latest, pypi_latest


def _fake_urlopen(response_obj):
    class _Ctx:
        def __enter__(self):
            return response_obj
        def __exit__(self, *exc):
            return False
    return _Ctx()


def _http_error(code: int):
    return urllib.error.HTTPError(
        url="x", code=code, msg="x", hdrs=None, fp=io.BytesIO(b"")
    )


class TestNpmLatest:
    def test_returns_version(self):
        body = io.BytesIO(json.dumps({"version": "1.2.3"}).encode())
        with patch("registry.urllib.request.urlopen", return_value=_fake_urlopen(body)):
            assert npm_latest("optio-ui") == "1.2.3"

    def test_404_returns_none(self):
        def raise_404(*a, **kw):
            raise _http_error(404)
        with patch("registry.urllib.request.urlopen", side_effect=raise_404):
            assert npm_latest("nonexistent-pkg") is None

    def test_other_http_error_propagates(self):
        def raise_500(*a, **kw):
            raise _http_error(500)
        with patch("registry.urllib.request.urlopen", side_effect=raise_500):
            with pytest.raises(urllib.error.HTTPError):
                npm_latest("optio-ui")


class TestPypiLatest:
    def test_returns_version(self):
        body = io.BytesIO(json.dumps({"info": {"version": "0.4.2"}}).encode())
        with patch("registry.urllib.request.urlopen", return_value=_fake_urlopen(body)):
            assert pypi_latest("optio-core") == "0.4.2"

    def test_404_returns_none(self):
        def raise_404(*a, **kw):
            raise _http_error(404)
        with patch("registry.urllib.request.urlopen", side_effect=raise_404):
            assert pypi_latest("nonexistent-pkg") is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/pytest scripts/release/tests/test_registry.py -v
```

Expected: `ModuleNotFoundError: No module named 'registry'`.

- [ ] **Step 3: Implement `scripts/release/registry.py`**

```python
"""Look up the currently-published version of a package on npm or PyPI."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


_NPM_URL = "https://registry.npmjs.org/{pkg}/latest"
_PYPI_URL = "https://pypi.org/pypi/{pkg}/json"


def npm_latest(pkg: str, timeout: float = 10.0) -> str | None:
    """Return the latest published version on npm, or None if unpublished."""
    try:
        with urllib.request.urlopen(_NPM_URL.format(pkg=pkg), timeout=timeout) as r:
            data = json.load(r)
            return data.get("version")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def pypi_latest(pkg: str, timeout: float = 10.0) -> str | None:
    """Return the latest published version on PyPI, or None if unpublished."""
    try:
        with urllib.request.urlopen(_PYPI_URL.format(pkg=pkg), timeout=timeout) as r:
            data = json.load(r)
            return data.get("info", {}).get("version")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def main() -> None:
    p = argparse.ArgumentParser(description="Query npm/PyPI for the latest version.")
    p.add_argument("registry", choices=["npm", "pypi"])
    p.add_argument("pkg")
    args = p.parse_args()
    fn = npm_latest if args.registry == "npm" else pypi_latest
    v = fn(args.pkg)
    print(v if v is not None else "", end="")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest scripts/release/tests/test_registry.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/release/registry.py scripts/release/tests/test_registry.py
git commit -m "release: add registry.py — query npm/PyPI for latest version"
```

---

## Task 4: TS package metadata backfill (`optio-contracts`, `optio-api`, `optio-dashboard`)

**Files:**
- Modify: `packages/optio-contracts/package.json`
- Modify: `packages/optio-api/package.json`
- Modify: `packages/optio-dashboard/package.json`

These three TS packages get the same metadata sweep. `optio-ui` is handled separately in Task 5 because it has the additional `link:` dep to fix.

- [ ] **Step 1: Edit `packages/optio-contracts/package.json`**

Remove `"private": true`. Insert (after `"license"`):

```json
  "description": "Shared wire-protocol types and ts-rest contracts for the optio task runner.",
  "repository": {
    "type": "git",
    "url": "git+https://github.com/deai-network/optio.git",
    "directory": "packages/optio-contracts"
  },
  "homepage": "https://github.com/deai-network/optio/tree/main/packages/optio-contracts#readme",
  "bugs": { "url": "https://github.com/deai-network/optio/issues" },
  "author": "Kristof Csillag <kristof.csillag@deai-labs.com>",
```

- [ ] **Step 2: Edit `packages/optio-api/package.json`**

Remove `"private": true`. Insert (after `"license"`):

```json
  "description": "Server-side optio API with Fastify, Express, and Next.js adapters.",
  "repository": {
    "type": "git",
    "url": "git+https://github.com/deai-network/optio.git",
    "directory": "packages/optio-api"
  },
  "homepage": "https://github.com/deai-network/optio/tree/main/packages/optio-api#readme",
  "bugs": { "url": "https://github.com/deai-network/optio/issues" },
  "author": "Kristof Csillag <kristof.csillag@deai-labs.com>",
```

- [ ] **Step 3: Edit `packages/optio-dashboard/package.json`**

Remove `"private": true`. Insert (after `"license"`):

```json
  "description": "Standalone optio dashboard app — install and run for a ready-to-use process management UI.",
  "repository": {
    "type": "git",
    "url": "git+https://github.com/deai-network/optio.git",
    "directory": "packages/optio-dashboard"
  },
  "homepage": "https://github.com/deai-network/optio/tree/main/packages/optio-dashboard#readme",
  "bugs": { "url": "https://github.com/deai-network/optio/issues" },
  "author": "Kristof Csillag <kristof.csillag@deai-labs.com>",
```

- [ ] **Step 4: Validate each package.json parses and contains expected fields**

```bash
for p in optio-contracts optio-api optio-dashboard; do
  echo "=== $p ==="
  .venv/bin/python -c "
import json,sys
d=json.load(open('packages/$p/package.json'))
assert 'private' not in d, '$p still has private:true'
for k in ('description','repository','homepage','bugs','author'):
    assert k in d, '$p missing ' + k
print('  ok')
"
done
```

Expected: each prints `=== <pkg> ===` then `  ok`. Any AssertionError means an edit was missed.

- [ ] **Step 5: Verify pnpm pack picks up the metadata**

```bash
for p in optio-contracts optio-api optio-dashboard; do
  (cd packages/$p && pnpm pack --dry-run 2>&1 | grep -E "(description|repository|homepage)" | head -5)
done
```

Expected: each package shows the new metadata fields in pnpm's dry-run output. (pnpm prints the manifest fields.) If pnpm gives a non-zero exit, fix before continuing.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-contracts/package.json packages/optio-api/package.json packages/optio-dashboard/package.json
git commit -m "release: backfill npm metadata on optio-contracts/-api/-dashboard"
```

---

## Task 5: `optio-ui` metadata + replace `link:` deps with versioned ranges

**Files:**
- Modify: `packages/optio-ui/package.json`

`optio-ui` is split out because the `link:` deps must be replaced with versioned ranges or npm will reject the publish.

- [ ] **Step 1: Verify the versioned deps exist on npm at the expected version**

```bash
npm view @quaesitor-textus/antd version
npm view @quaesitor-textus/core version
```

Expected: both print `0.1.6`. If a newer version exists, use that instead in step 2. If anything is missing or different, stop and flag.

- [ ] **Step 2: Edit `packages/optio-ui/package.json`**

Remove `"private": true`. Insert (after `"license"`):

```json
  "description": "React components for embedding optio process management UIs into ts-rest applications.",
  "repository": {
    "type": "git",
    "url": "git+https://github.com/deai-network/optio.git",
    "directory": "packages/optio-ui"
  },
  "homepage": "https://github.com/deai-network/optio/tree/main/packages/optio-ui#readme",
  "bugs": { "url": "https://github.com/deai-network/optio/issues" },
  "author": "Kristof Csillag <kristof.csillag@deai-labs.com>",
```

In `"dependencies"`, replace the two `link:` lines:

```diff
-    "@quaesitor-textus/antd": "link:../../../quaesitor-textus/packages/antd",
-    "@quaesitor-textus/core": "link:../../../quaesitor-textus/packages/core",
+    "@quaesitor-textus/antd": "^0.1.6",
+    "@quaesitor-textus/core": "^0.1.6",
```

- [ ] **Step 3: Re-resolve the workspace**

```bash
pnpm install
```

Expected: install succeeds. `pnpm` downloads the published versions of `@quaesitor-textus/antd` and `@quaesitor-textus/core` and stores them in the global pnpm store. No "linked" notation in the lockfile diff for these two packages.

If pnpm complains about a version mismatch or missing build artifact in `@quaesitor-textus/*`, that's evidence the published version is incompatible — stop, report, and discuss before proceeding.

- [ ] **Step 4: Build and test `optio-ui` to confirm nothing regressed**

```bash
pnpm --filter optio-ui build
pnpm --filter optio-ui test
```

Expected: both succeed. If either fails, the published `@quaesitor-textus/*` packages diverge from what's in the linked source — stop and report.

- [ ] **Step 5: Validate package.json**

```bash
.venv/bin/python -c "
import json
d=json.load(open('packages/optio-ui/package.json'))
assert 'private' not in d
for k in ('description','repository','homepage','bugs','author'):
    assert k in d, 'missing ' + k
for k, v in d['dependencies'].items():
    assert not v.startswith('link:'), 'still link: dep: ' + k
print('ok')
"
```

Expected: prints `ok`.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-ui/package.json pnpm-lock.yaml
git commit -m "release: optio-ui metadata + switch quaesitor-textus deps to versioned"
```

---

## Task 6: Python package metadata backfill + sibling-pin tightening

**Files:**
- Modify: `packages/optio-core/pyproject.toml`
- Modify: `packages/optio-host/pyproject.toml`
- Modify: `packages/optio-opencode/pyproject.toml`
- Modify: `packages/optio-demo/pyproject.toml`

All four packages get the same baseline metadata block. Per-package additions: topic classifiers and (for sibs of optio-core/host/opencode) sibling-pin tightening.

- [ ] **Step 1: Edit `packages/optio-core/pyproject.toml`**

Add `readme = "README.md"` immediately after the `license` line. Add `authors` and `classifiers` to the `[project]` table. Append a `[project.urls]` section after `[project.optional-dependencies]`.

Final `[project]` block should look like:

```toml
[project]
name = "optio-core"
version = "0.1.0"
description = "Reusable async process management library"
readme = "README.md"
license = "Apache-2.0"
requires-python = ">=3.11"
authors = [
    { name = "Kristof Csillag", email = "kristof.csillag@deai-labs.com" },
]
classifiers = [
    "Development Status :: 4 - Beta",
    "License :: OSI Approved :: Apache Software License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Operating System :: POSIX :: Linux",
    "Operating System :: MacOS",
    "Topic :: Software Development :: Libraries :: Python Modules",
    "Topic :: System :: Distributed Computing",
    "Framework :: AsyncIO",
]
dependencies = [
    "motor>=3.3.0",
    "apscheduler>=4.0.0a6",
    "mongo-quaestor>=0.1,<0.2",
    "clamator-over-redis>=0.1.9",
    "clamator-protocol>=0.1.9",
    "pydantic>=2.0",
]
```

And insert before `[tool.setuptools.packages.find]`:

```toml
[project.urls]
Homepage = "https://github.com/deai-network/optio"
Repository = "https://github.com/deai-network/optio"
Issues = "https://github.com/deai-network/optio/issues"
```

- [ ] **Step 2: Edit `packages/optio-host/pyproject.toml`**

Same `readme`, `authors`, `[project.urls]` as above. Different `classifiers`:

```toml
classifiers = [
    "Development Status :: 4 - Beta",
    "License :: OSI Approved :: Apache Software License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Operating System :: POSIX :: Linux",
    "Operating System :: MacOS",
    "Topic :: Software Development :: Libraries :: Python Modules",
    "Topic :: System :: Distributed Computing",
    "Topic :: System :: Systems Administration",
    "Framework :: AsyncIO",
]
```

Tighten the `optio-core` dep:

```diff
 dependencies = [
-    "optio-core",
+    "optio-core>=0.1,<0.2",
     "asyncssh>=2.14",
 ]
```

- [ ] **Step 3: Edit `packages/optio-opencode/pyproject.toml`**

Same `readme`, `authors`, `[project.urls]`. Classifiers:

```toml
classifiers = [
    "Development Status :: 4 - Beta",
    "License :: OSI Approved :: Apache Software License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Operating System :: POSIX :: Linux",
    "Operating System :: MacOS",
    "Topic :: Software Development :: Libraries :: Python Modules",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
    "Topic :: Software Development :: Code Generators",
    "Framework :: AsyncIO",
]
```

Tighten the sibling pins:

```diff
 dependencies = [
-    "optio-core",
-    "optio-host",
+    "optio-core>=0.1,<0.2",
+    "optio-host>=0.1,<0.2",
     "asyncssh>=2.14",
 ]
```

- [ ] **Step 4: Edit `packages/optio-demo/pyproject.toml`**

Same `readme`, `authors`, `[project.urls]`. Classifiers:

```toml
classifiers = [
    "Development Status :: 4 - Beta",
    "License :: OSI Approved :: Apache Software License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Operating System :: POSIX :: Linux",
    "Operating System :: MacOS",
    "Topic :: Software Development",
    "Topic :: Software Development :: Libraries :: Application Frameworks",
]
```

Tighten sibling pins (note the `[redis]` extras must be preserved):

```diff
 dependencies = [
-    "optio-core[redis]",
+    "optio-core[redis]>=0.1,<0.2",
     "marimo>=0.9",
-    "optio-opencode",
+    "optio-opencode>=0.1,<0.2",
     "watchfiles>=1.0",
 ]
```

- [ ] **Step 5: Validate every Python pyproject parses and contains expected fields**

```bash
.venv/bin/python -c "
import tomllib
from pathlib import Path
for p in ['optio-core', 'optio-host', 'optio-opencode', 'optio-demo']:
    text = Path(f'packages/{p}/pyproject.toml').read_bytes()
    d = tomllib.loads(text.decode())
    proj = d['project']
    assert proj.get('readme') == 'README.md', f'{p}: readme'
    assert proj.get('authors'), f'{p}: authors'
    assert proj.get('classifiers'), f'{p}: classifiers'
    assert 'Development Status :: 4 - Beta' in proj['classifiers'], f'{p}: dev-status'
    urls = d['project'].get('urls', {})
    assert urls.get('Homepage'), f'{p}: urls.Homepage'
    print(f'{p} ok')
"
```

Expected: prints four `<pkg> ok` lines.

- [ ] **Step 6: Run the full Python test suite to confirm nothing regressed**

```bash
make test
```

Expected: TS tests pass, Python tests pass. (The metadata edits are inert to runtime, but this catches any toml-parsing accidents.) Note `make test` runs the full TS + Python suite. If TS tests pass but Python fails (or vice versa) due to a metadata mistake, fix and re-run.

- [ ] **Step 7: Commit**

```bash
git add packages/optio-core/pyproject.toml packages/optio-host/pyproject.toml packages/optio-opencode/pyproject.toml packages/optio-demo/pyproject.toml
git commit -m "release: backfill PyPI metadata + tighten sibling pins"
```

---

## Task 7: Write READMEs for `optio-host` and `optio-opencode`

**Files:**
- Create: `packages/optio-host/README.md`
- Create: `packages/optio-opencode/README.md`

Use the drafts from the spec verbatim.

- [ ] **Step 1: Create `packages/optio-host/README.md`**

```markdown
# optio-host

Local-or-remote host abstraction plus the log/deliverables coordination protocol used by optio task types.

`optio-host` lets a task author run shell commands, manage workdirs, and stream files **without caring whether the work happens locally or on a remote host over SSH**. It also provides a small line-based protocol that long-running worker processes can use to report progress and produce file deliverables.

## What's in the box

- **`Host` Protocol + `LocalHost` / `RemoteHost` / `make_host()`** — uniform interface for running commands, opening port forwards, transferring files, and tearing down workdirs. SSH details (auth, multiplexing, channel cleanup) are hidden behind `asyncssh`.
- **`HookContext`** — small carrier passed into task hooks so they can run additional host commands, request file fetches, and report progress without touching `optio-core` internals.
- **`optio_host.protocol`** — a line-oriented session driver. A long-running process on the host writes lines prefixed `STATUS:`, `DELIVERABLE:`, `DONE`, or `ERROR`. The driver tails the log, dispatches progress events, fetches deliverable files, and resolves the session on `DONE` / `ERROR`.
- **`create_download_task(...)`** — a ready-made optio task that downloads a file from a remote host with progress reporting and integrity checks.

## When to use it

You're building an [optio](https://github.com/deai-network/optio) task type that needs to run work on a host — local or remote — and you want:

- one abstraction that works in both modes,
- a structured way for the running process to talk back to optio (progress + deliverables),
- SSH transport handled for you.

If you're writing the end-user task type directly (not consuming this library from another optio task package), you probably want `optio-core` instead.

## Installation

```bash
pip install optio-host
```

`optio-host` depends on `optio-core` and `asyncssh`. Python 3.11+.

## Minimal example

```python
from optio_host import make_host, SSHConfig

# Local
async with make_host(ssh=None) as host:
    result = await host.run(["uname", "-a"])
    print(result.stdout)

# Remote
ssh = SSHConfig(host="worker-1", user="optio", key_path="~/.ssh/id_optio")
async with make_host(ssh=ssh) as host:
    result = await host.run(["uname", "-a"])
    print(result.stdout)
```

## License

Apache-2.0.
```

- [ ] **Step 2: Create `packages/optio-opencode/README.md`**

```markdown
# optio-opencode

Run [opencode web](https://github.com/opencode-ai/opencode) as an [optio](https://github.com/deai-network/optio) task — local subprocess or remote over SSH — with opencode's UI reachable through optio's UI components.

## What it does

Given an `OpencodeTaskConfig` (workdir contents, prompt, deliverable callback), `optio-opencode`:

1. Provisions a fresh workdir on the chosen host (local or remote).
2. Writes `AGENTS.md` (base prompt + your instructions) and `opencode.json` (your config) into it.
3. Installs the opencode binary if missing (remote mode only).
4. Launches `opencode web` with a random auth password.
5. Registers the opencode UI as a widget that optio's UI components can embed via the widget proxy — SSH tunnel hidden from optio-api.
6. Tails a log file the LLM writes to and translates structured lines into optio events:
   - `STATUS: …` → `ctx.report_progress(percent, message)`
   - `DELIVERABLE: <path>` → fetches the file, invokes your `on_deliverable` callback
   - `DONE [summary]` → clean completion
   - `ERROR [message]` → failure
7. Cleans up workdir and SSH connection on teardown.

The same `OpencodeTaskConfig` works for local and remote modes; only `SSHConfig` differs.

## When to use it

You want an opencode-driven assistant session as a managed optio task — surfaced through optio's UI, with progress reporting and file deliverables — without writing the host management, log parsing, or widget plumbing yourself.

## Installation

```bash
pip install optio-opencode
```

Python 3.11+. Depends on `optio-core`, `optio-host`, and `asyncssh`.

## Minimal example

```python
from optio_opencode import create_opencode_task, OpencodeTaskConfig
from optio_host import SSHConfig

config = OpencodeTaskConfig(
    workdir_files={"AGENTS.md": "Do the thing.", "opencode.json": "{...}"},
    on_deliverable=lambda ctx, path, text: print(f"got {path}: {len(text)} bytes"),
    ssh=SSHConfig(host="worker-1", user="optio", key_path="~/.ssh/id_optio"),
)

task = create_opencode_task(config)
# Schedule / run via optio-core as usual.
```

Set `ssh=None` for local subprocess mode.

## License

Apache-2.0.
```

- [ ] **Step 3: Verify both READMEs render and contain expected sections**

```bash
for p in optio-host optio-opencode; do
  echo "=== $p ==="
  test -f "packages/$p/README.md"
  grep -E "^# $p$" "packages/$p/README.md" >/dev/null && echo "  has H1"
  grep -E "^## Installation$" "packages/$p/README.md" >/dev/null && echo "  has Installation section"
  grep -E "^## License$" "packages/$p/README.md" >/dev/null && echo "  has License section"
done
```

Expected: each package prints three confirmations.

- [ ] **Step 4: Confirm `python -m build` for both packages now includes the README**

```bash
.venv/bin/pip install build twine
for p in optio-host optio-opencode; do
  rm -rf packages/$p/dist
  (cd packages/$p && ../../.venv/bin/python -m build --wheel 2>&1 | tail -5)
  .venv/bin/python -m twine check packages/$p/dist/*.whl
done
```

Expected: each build produces a wheel; `twine check` prints `Checking ...: PASSED` for each.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-host/README.md packages/optio-opencode/README.md
git commit -m "release: add READMEs for optio-host and optio-opencode"
```

---

## Task 8: `scripts/release/run.py` — orchestrator skeleton + `preflight` and `per-package` subcommands

**Files:**
- Create: `scripts/release/run.py`
- Create: `scripts/release/tests/test_run.py`

This task implements the orchestrator's foundation: argument parsing, preflight checks, and the per-package release flow (used for `release-optio-host`, `release-optio-opencode`, `release-optio-demo`, `release-optio-ui`, `release-optio-api`, `release-optio-dashboard`).

The wire-locked variant (Task 9), batch mode (Task 10), and resume (Task 11) live in follow-on tasks.

- [ ] **Step 1: Write the failing tests**

Create `scripts/release/tests/test_run.py`:

```python
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

        with patch("run.pypi_latest", return_value=None), \
             patch("run.subprocess.run", side_effect=fake_run), \
             patch("run.subprocess.check_call", side_effect=lambda *a, **kw: commands.append(a[0])):
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
```

- [ ] **Step 2: Implement `scripts/release/run.py` skeleton**

Create `scripts/release/run.py`:

```python
"""Optio release orchestrator.

Subcommands:
    preflight              — run pre-release checks only.
    per-package <pkg> <bump>   — release a single package.
    wire <bump>            — release optio-contracts and optio-core in lockstep.
    all                    — release every package whose source version is
                             ahead of its registry version (BUMP=none).
    resume <pkg>           — resume a partially-completed per-package release.

Failure handling: stops on the first failed step and prints a clear message
plus the recommended next step (often `make resume-release-<pkg>`).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Literal

# Make sibling helper modules importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bump import compute_new_version, read_version, write_version  # noqa: E402
from registry import npm_latest, pypi_latest  # noqa: E402
from sibling_pins import update_pyproject  # noqa: E402


# --- Configuration -----------------------------------------------------------

# All publishable packages.
TS_PUBLISHABLE = ["optio-contracts", "optio-ui", "optio-api", "optio-dashboard"]
PY_PUBLISHABLE = ["optio-core", "optio-host", "optio-opencode", "optio-demo"]
# Packages locked in wire-version step.
WIRE_LOCKED = {"optio-contracts", "optio-core"}


# --- Data types --------------------------------------------------------------

@dataclasses.dataclass
class PackageInfo:
    name: str                  # repo-directory name, e.g. "optio-core"
    dir: Path                  # absolute path to packages/<name>
    kind: Literal["ts", "python"]
    dist_name: str             # the registry distribution name (often == name)
    current_version: str       # version currently in source


# --- Discovery ---------------------------------------------------------------

def discover_package(repo_root: Path, pkg_name: str) -> PackageInfo:
    pkg_dir = repo_root / "packages" / pkg_name
    if not pkg_dir.exists():
        raise SystemExit(f"package not found: {pkg_dir}")
    if (pkg_dir / "pyproject.toml").exists():
        import tomllib
        data = tomllib.loads((pkg_dir / "pyproject.toml").read_text())
        return PackageInfo(
            name=pkg_name,
            dir=pkg_dir,
            kind="python",
            dist_name=data["project"]["name"],
            current_version=data["project"]["version"],
        )
    if (pkg_dir / "package.json").exists():
        data = json.loads((pkg_dir / "package.json").read_text())
        return PackageInfo(
            name=pkg_name,
            dir=pkg_dir,
            kind="ts",
            dist_name=data["name"],
            current_version=data["version"],
        )
    raise SystemExit(f"{pkg_dir}: neither pyproject.toml nor package.json")


# --- Preflight ---------------------------------------------------------------

def preflight(*, skip_tests: bool = False, skip_fetch: bool = False) -> None:
    """Run mandatory pre-release checks. Abort on any failure."""
    # Working tree clean
    if subprocess.run(["git", "diff", "--quiet"]).returncode != 0:
        raise SystemExit("preflight failed: working tree is dirty (unstaged changes)")
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode != 0:
        raise SystemExit("preflight failed: working tree is dirty (staged changes)")

    # On main
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if branch != "main":
        raise SystemExit(f"preflight failed: not on main (currently on {branch})")

    # Up to date with origin/main
    if not skip_fetch:
        subprocess.run(["git", "fetch", "origin"], check=True)
    local = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    upstream = subprocess.run(
        ["git", "rev-parse", "@{u}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if local != upstream:
        raise SystemExit("preflight failed: branch is not up to date with origin/main")

    # Tests
    if not skip_tests:
        rc = subprocess.run(["make", "test"]).returncode
        if rc != 0:
            raise SystemExit("preflight failed: `make test` failed")


# --- Registry queries --------------------------------------------------------

def latest_published(info: PackageInfo) -> str | None:
    if info.kind == "ts":
        return npm_latest(info.dist_name)
    return pypi_latest(info.dist_name)


# --- Per-package release -----------------------------------------------------

def release_per_package(
    *,
    repo_root: Path,
    pkg_name: str,
    bump: str,
    skip_tests: bool = False,
    skip_fetch: bool = False,
    skip_publish: bool = False,
    skip_push: bool = False,
) -> None:
    """Release a single package end-to-end."""
    if pkg_name in WIRE_LOCKED:
        raise SystemExit(
            f"{pkg_name} is wire-locked — use `make release-wire BUMP=...` instead"
        )

    info = discover_package(repo_root, pkg_name)

    # Pre-flight
    preflight(skip_tests=skip_tests, skip_fetch=skip_fetch)

    # BUMP=none policy: only valid before first release
    latest = latest_published(info)
    if bump == "none":
        if latest is not None:
            raise SystemExit(
                f"BUMP=none rejected: {info.dist_name} {latest} already published. "
                f"Use BUMP=patch or BUMP=minor."
            )

    # Compute new version
    new_version = compute_new_version(info.current_version, bump)

    # Write the new version (no-op if BUMP=none)
    version_file, _ = read_version(info.dir)
    if new_version != info.current_version:
        write_version(version_file, new_version)

    # Update sibling pins (Python only — TS uses workspace:* which pnpm handles)
    changed_sibs: list[str] = []
    if info.kind == "python":
        packages_dir = repo_root / "packages"
        for sib in sorted(packages_dir.iterdir()):
            if not sib.is_dir() or sib == info.dir:
                continue
            sib_toml = sib / "pyproject.toml"
            if sib_toml.exists() and update_pyproject(sib_toml, info.dist_name, new_version):
                changed_sibs.append(sib.name)

    # Build (clean dist first)
    dist = info.dir / "dist"
    if dist.exists():
        for f in dist.iterdir():
            f.unlink()

    if info.kind == "python":
        subprocess.check_call(
            [sys.executable, "-m", "build"], cwd=str(info.dir)
        )
        subprocess.check_call(
            [sys.executable, "-m", "twine", "check",
             *[str(p) for p in sorted(dist.iterdir())]]
        )
    else:
        subprocess.check_call(
            ["pnpm", "--filter", info.dist_name, "build"], cwd=str(repo_root)
        )

    # Commit + tag
    files_to_add = [str(version_file)]
    files_to_add.extend(
        str(repo_root / "packages" / s / "pyproject.toml") for s in changed_sibs
    )
    subprocess.check_call(["git", "add", *files_to_add])
    subprocess.check_call(
        ["git", "commit", "-m", f"release({info.name}): {new_version}"]
    )
    tag = f"{info.name}-v{new_version}"
    subprocess.check_call(["git", "tag", tag])

    # Publish
    if not skip_publish:
        if info.kind == "python":
            subprocess.check_call(
                [sys.executable, "-m", "twine", "upload",
                 *[str(p) for p in sorted(dist.iterdir())]]
            )
        else:
            subprocess.check_call(
                ["pnpm", "publish", "--access", "public"], cwd=str(info.dir)
            )

    # Push
    if not skip_push:
        subprocess.check_call(["git", "push", "origin", "main"])
        subprocess.check_call(["git", "push", "origin", tag])

    # Summary
    if info.kind == "python":
        url = f"https://pypi.org/project/{info.dist_name}/{new_version}/"
    else:
        url = f"https://www.npmjs.com/package/{info.dist_name}/v/{new_version}"
    print(f"\nReleased {info.dist_name} {new_version}")
    print(f"  Registry: {url}")
    print(f"  Tag:      https://github.com/deai-network/optio/releases/tag/{tag}")


# --- CLI ---------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Optio release orchestrator")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("preflight", help="Run pre-release checks only.")

    pp = sub.add_parser("per-package", help="Release a single package.")
    pp.add_argument("pkg")
    pp.add_argument("bump", choices=["patch", "minor", "major", "promote-to-1.0", "none"])

    args = p.parse_args()
    repo_root = Path(__file__).resolve().parents[2]

    if args.cmd == "preflight":
        preflight()
        print("preflight: ok")
    elif args.cmd == "per-package":
        release_per_package(repo_root=repo_root, pkg_name=args.pkg, bump=args.bump)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run tests to verify they pass**

```bash
.venv/bin/pytest scripts/release/tests/test_run.py -v
```

Expected: all tests PASS.

- [ ] **Step 4: Smoke-test `preflight` subcommand against the live repo**

```bash
.venv/bin/python scripts/release/run.py preflight
```

Expected: passes if the working tree is clean and you're on main up to date. Note: this runs `make test`. If `make test` is slow, set `OPTIO_SKIP_TESTS=1` is NOT supported in this skeleton — you'll have to wait or temporarily run `python -c 'from scripts.release.run import preflight; preflight(skip_tests=True)'`.

If preflight aborts with a clear message about why, that's success (the message itself is the contract).

- [ ] **Step 5: Commit**

```bash
git add scripts/release/run.py scripts/release/tests/test_run.py
git commit -m "release: add orchestrator with preflight and per-package subcommands"
```

---

## Task 9: `wire` subcommand — combined `optio-contracts` + `optio-core` release

**Files:**
- Modify: `scripts/release/run.py`
- Modify: `scripts/release/tests/test_run.py`

- [ ] **Step 1: Append failing test for the wire-locked path**

Append to `scripts/release/tests/test_run.py`:

```python
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

        from run import release_wire  # late import; symbol added in this task

        with patch("run.npm_latest", return_value=None), \
             patch("run.pypi_latest", return_value=None), \
             patch("run.subprocess.run", side_effect=fake_run), \
             patch("run.subprocess.check_call", side_effect=lambda *a, **kw: commands.append(a[0])):
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
        # Two tags issued.
        tag_cmds = [c for c in commands if c[:2] == ["git", "tag"]]
        tag_names = [c[2] for c in tag_cmds]
        assert "optio-contracts-v0.2.0" in tag_names
        assert "optio-core-v0.2.0" in tag_names
        # Single commit covering both packages + sibling.
        commit_cmds = [c for c in commands if c[:2] == ["git", "commit"]]
        assert len(commit_cmds) == 1
        assert "release(wire): 0.2.0" in commit_cmds[0][-1]
```

- [ ] **Step 2: Implement `release_wire` in `scripts/release/run.py`**

After `release_per_package`, add:

```python
def release_wire(
    *,
    repo_root: Path,
    bump: str,
    skip_tests: bool = False,
    skip_fetch: bool = False,
    skip_publish: bool = False,
    skip_push: bool = False,
) -> None:
    """Release optio-contracts + optio-core together at the same new version."""
    contracts = discover_package(repo_root, "optio-contracts")
    core = discover_package(repo_root, "optio-core")
    if contracts.current_version != core.current_version:
        raise SystemExit(
            f"wire-locked but versions diverge: contracts={contracts.current_version}, "
            f"core={core.current_version}"
        )

    preflight(skip_tests=skip_tests, skip_fetch=skip_fetch)

    if bump == "none":
        # Both must be unpublished
        if npm_latest(contracts.dist_name) is not None:
            raise SystemExit("BUMP=none rejected: optio-contracts already on npm")
        if pypi_latest(core.dist_name) is not None:
            raise SystemExit("BUMP=none rejected: optio-core already on PyPI")

    new_version = compute_new_version(contracts.current_version, bump)

    # Bump both
    contracts_file, _ = read_version(contracts.dir)
    core_file, _ = read_version(core.dir)
    if new_version != contracts.current_version:
        write_version(contracts_file, new_version)
        write_version(core_file, new_version)

    # Update Python sibling pins (pinning optio-core only — contracts is TS, no Python sibs)
    changed_sibs: list[str] = []
    packages_dir = repo_root / "packages"
    for sib in sorted(packages_dir.iterdir()):
        if not sib.is_dir() or sib == core.dir:
            continue
        sib_toml = sib / "pyproject.toml"
        if sib_toml.exists() and update_pyproject(sib_toml, core.dist_name, new_version):
            changed_sibs.append(sib.name)

    # Build both
    for pkg in (contracts, core):
        dist = pkg.dir / "dist"
        if dist.exists():
            for f in dist.iterdir():
                f.unlink()
    subprocess.check_call(
        [sys.executable, "-m", "build"], cwd=str(core.dir)
    )
    subprocess.check_call(
        [sys.executable, "-m", "twine", "check",
         *[str(p) for p in sorted((core.dir / "dist").iterdir())]]
    )
    subprocess.check_call(
        ["pnpm", "--filter", contracts.dist_name, "build"], cwd=str(repo_root)
    )

    # Single commit
    files_to_add = [str(contracts_file), str(core_file)]
    files_to_add.extend(
        str(repo_root / "packages" / s / "pyproject.toml") for s in changed_sibs
    )
    subprocess.check_call(["git", "add", *files_to_add])
    subprocess.check_call(
        ["git", "commit", "-m", f"release(wire): {new_version}"]
    )

    # Two tags
    contracts_tag = f"optio-contracts-v{new_version}"
    core_tag = f"optio-core-v{new_version}"
    subprocess.check_call(["git", "tag", contracts_tag])
    subprocess.check_call(["git", "tag", core_tag])

    # Publish both
    if not skip_publish:
        subprocess.check_call(
            [sys.executable, "-m", "twine", "upload",
             *[str(p) for p in sorted((core.dir / "dist").iterdir())]]
        )
        subprocess.check_call(
            ["pnpm", "publish", "--access", "public"], cwd=str(contracts.dir)
        )

    # Push
    if not skip_push:
        subprocess.check_call(["git", "push", "origin", "main"])
        subprocess.check_call(["git", "push", "origin", contracts_tag])
        subprocess.check_call(["git", "push", "origin", core_tag])

    print(f"\nReleased wire {new_version}")
    print(f"  optio-contracts: https://www.npmjs.com/package/{contracts.dist_name}/v/{new_version}")
    print(f"  optio-core:      https://pypi.org/project/{core.dist_name}/{new_version}/")
```

Register the subcommand in `main()`:

```python
    w = sub.add_parser("wire", help="Release optio-contracts + optio-core together.")
    w.add_argument("bump", choices=["patch", "minor", "major", "promote-to-1.0", "none"])
```

And after the `per-package` branch in `main()`:

```python
    elif args.cmd == "wire":
        release_wire(repo_root=repo_root, bump=args.bump)
```

- [ ] **Step 3: Run tests**

```bash
.venv/bin/pytest scripts/release/tests/test_run.py -v
```

Expected: every test PASSES, including the new `TestReleaseWire`.

- [ ] **Step 4: Confirm `release_per_package` still refuses to release wire-locked packages**

The wire-lock check happens before preflight, so invoking the CLI is safe and won't touch git or run tests.

```bash
.venv/bin/python scripts/release/run.py per-package optio-core patch 2>&1 | head -3
echo "exit: $?"
```

Expected: stdout/stderr contains `optio-core is wire-locked — use \`make release-wire BUMP=...\` instead` and the exit code is non-zero (typically `1`).

- [ ] **Step 5: Commit**

```bash
git add scripts/release/run.py scripts/release/tests/test_run.py
git commit -m "release: add wire subcommand for optio-contracts+core lockstep"
```

---

## Task 10: `all` subcommand — release every package whose source > registry

**Files:**
- Modify: `scripts/release/run.py`
- Modify: `scripts/release/tests/test_run.py`

- [ ] **Step 1: Append failing test**

Append to `scripts/release/tests/test_run.py`:

```python
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
             patch("run.pypi_latest", return_value="0.1.0"):
            with pytest.raises(SystemExit, match="nothing pending"):
                release_all(repo_root=tmp_path, skip_tests=True, skip_fetch=True,
                            skip_publish=True, skip_push=True)
```

- [ ] **Step 2: Implement `release_all` in `scripts/release/run.py`**

After `release_wire`, add:

```python
def release_all(
    *,
    repo_root: Path,
    skip_tests: bool = False,
    skip_fetch: bool = False,
    skip_publish: bool = False,
    skip_push: bool = False,
) -> None:
    """Release every package whose source version is ahead of its registry version.

    All releases use BUMP=none (the source version-as-is). Refuses if nothing is pending.
    The wire-locked pair, if pending, is released first via release_wire(bump="none").
    """
    pending_wire = False
    pending_per_pkg: list[str] = []

    for name in TS_PUBLISHABLE + PY_PUBLISHABLE:
        info = discover_package(repo_root, name)
        latest = latest_published(info)
        # "ahead of registry" means source-version differs from registry (or is unpublished)
        if latest != info.current_version:
            if name in WIRE_LOCKED:
                pending_wire = True
            else:
                pending_per_pkg.append(name)

    if not pending_wire and not pending_per_pkg:
        raise SystemExit("nothing pending — every source version matches its registry version")

    if pending_wire:
        release_wire(
            repo_root=repo_root, bump="none",
            skip_tests=skip_tests, skip_fetch=skip_fetch,
            skip_publish=skip_publish, skip_push=skip_push,
        )
        # Subsequent per-package releases inherit a clean tree from wire's commit;
        # preflight will skip fetch/tests on the rest since they were just done.
        skip_tests = True
        skip_fetch = True

    for name in pending_per_pkg:
        release_per_package(
            repo_root=repo_root, pkg_name=name, bump="none",
            skip_tests=skip_tests, skip_fetch=skip_fetch,
            skip_publish=skip_publish, skip_push=skip_push,
        )
```

Register subcommand:

```python
    sub.add_parser("all", help="Release every package whose source > registry.")
```

In `main()` branch dispatch:

```python
    elif args.cmd == "all":
        release_all(repo_root=repo_root)
```

- [ ] **Step 3: Run tests**

```bash
.venv/bin/pytest scripts/release/tests/test_run.py -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add scripts/release/run.py scripts/release/tests/test_run.py
git commit -m "release: add all subcommand for batch publish of pending packages"
```

---

## Task 11: `resume` subcommand — recover from mid-release failure

**Files:**
- Modify: `scripts/release/run.py`
- Modify: `scripts/release/tests/test_run.py`

The resume target inspects three state slots — a local tag, a `dist/` artifact, and the registry — to figure out which step a previous failed release got stuck at, then resumes from the right point.

- [ ] **Step 1: Append failing tests**

Append to `scripts/release/tests/test_run.py`:

```python
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
            if cmd[:2] == ["git", "tag"] and len(cmd) == 3:
                # `git tag -l fake-py-v0.2.0` → tag exists
                pass
            if cmd == ["git", "tag", "-l", "fake-py-v0.2.0"]:
                return MagicMock(returncode=0, stdout="fake-py-v0.2.0\n")
            return MagicMock(returncode=0, stdout="")

        with patch("run.pypi_latest", return_value="0.1.0"), \
             patch("run.subprocess.run", side_effect=fake_run), \
             patch("run.subprocess.check_call", side_effect=lambda *a, **kw: commands.append(a[0])):
            resume(repo_root=tmp_path, pkg_name="fake-py", skip_publish=False, skip_push=True)

        # Should have invoked twine upload.
        assert any(c[:3] == ["python", "-m", "twine"] and "upload" in c for c in commands)

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

        with patch("run.pypi_latest", return_value="0.1.0"), \
             patch("run.subprocess.run", side_effect=fake_run):
            with pytest.raises(SystemExit, match="nothing to resume"):
                resume(repo_root=tmp_path, pkg_name="fake-py", skip_publish=True, skip_push=True)
```

- [ ] **Step 2: Implement `resume` in `scripts/release/run.py`**

```python
def _local_tag_exists(tag: str) -> bool:
    r = subprocess.run(
        ["git", "tag", "-l", tag],
        capture_output=True, text=True,
    )
    return tag in r.stdout.split()


def resume(
    *,
    repo_root: Path,
    pkg_name: str,
    skip_publish: bool = False,
    skip_push: bool = False,
) -> None:
    """Resume a failed release. Diagnoses state and replays the missing steps."""
    if pkg_name in WIRE_LOCKED:
        raise SystemExit(
            f"{pkg_name} is wire-locked — resume via `make resume-release-optio-contracts` "
            f"or `make resume-release-optio-core` is not supported; rerun `make release-wire` "
            f"after manually cleaning state."
        )

    info = discover_package(repo_root, pkg_name)
    expected_tag = f"{pkg_name}-v{info.current_version}"
    tag_present = _local_tag_exists(expected_tag)
    dist_dir = info.dir / "dist"
    have_artifact = dist_dir.exists() and any(dist_dir.iterdir())
    latest = latest_published(info)
    registry_has = (latest == info.current_version)

    # Case analysis:
    # 1. Registry has this version: nothing to do (publish already succeeded).
    if registry_has:
        # If we also haven't pushed, do that.
        if not skip_push:
            print(f"registry has {info.dist_name} {info.current_version}; pushing tag if needed.")
            subprocess.check_call(["git", "push", "origin", "main"])
            subprocess.check_call(["git", "push", "origin", expected_tag])
        else:
            print(f"registry has {info.dist_name} {info.current_version}; nothing to do.")
        return

    # 2. Tag present + artifact present: resume from publish.
    if tag_present and have_artifact:
        print(f"resume: tag {expected_tag} and dist/ present; retrying publish step.")
        if not skip_publish:
            if info.kind == "python":
                subprocess.check_call(
                    [sys.executable, "-m", "twine", "upload",
                     *[str(p) for p in sorted(dist_dir.iterdir())]]
                )
            else:
                subprocess.check_call(
                    ["pnpm", "publish", "--access", "public"], cwd=str(info.dir)
                )
        if not skip_push:
            subprocess.check_call(["git", "push", "origin", "main"])
            subprocess.check_call(["git", "push", "origin", expected_tag])
        return

    # 3. Tag present, no artifact: rebuild and republish.
    if tag_present and not have_artifact:
        print(f"resume: tag {expected_tag} present but no dist/. Rebuilding + republishing.")
        if info.kind == "python":
            subprocess.check_call([sys.executable, "-m", "build"], cwd=str(info.dir))
            subprocess.check_call(
                [sys.executable, "-m", "twine", "check",
                 *[str(p) for p in sorted(dist_dir.iterdir())]]
            )
            if not skip_publish:
                subprocess.check_call(
                    ["python", "-m", "twine", "upload",
                     *[str(p) for p in sorted(dist_dir.iterdir())]]
                )
        else:
            subprocess.check_call(["pnpm", "--filter", info.dist_name, "build"],
                                  cwd=str(repo_root))
            if not skip_publish:
                subprocess.check_call(["pnpm", "publish", "--access", "public"],
                                      cwd=str(info.dir))
        if not skip_push:
            subprocess.check_call(["git", "push", "origin", "main"])
            subprocess.check_call(["git", "push", "origin", expected_tag])
        return

    # 4. No tag, no artifact, no registry entry: rerun the full release.
    raise SystemExit(
        f"nothing to resume: no tag {expected_tag}, no dist artifact, and "
        f"{info.dist_name} {info.current_version} is not on the registry. "
        f"Run `make release-{pkg_name} BUMP=<level>` instead."
    )
```

Register subcommand:

```python
    r = sub.add_parser("resume", help="Resume a partially-completed per-package release.")
    r.add_argument("pkg")
```

Branch dispatch:

```python
    elif args.cmd == "resume":
        resume(repo_root=repo_root, pkg_name=args.pkg)
```

- [ ] **Step 3: Run tests**

```bash
.venv/bin/pytest scripts/release/tests/test_run.py -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add scripts/release/run.py scripts/release/tests/test_run.py
git commit -m "release: add resume subcommand for mid-release failure recovery"
```

---

## Task 12: Wire up Makefile targets

**Files:**
- Modify: `Makefile`

- [ ] **Step 1: Append the release targets block to `Makefile`**

After the existing `clean-deep` target (or anywhere that doesn't collide with existing rules), append:

```makefile
# -----------------------------------------------------------------------------
# Release targets
#
# Per-package releases via Python orchestrator. Each target takes BUMP=...
# Wire-locked optio-contracts and optio-core release together via release-wire.
# See docs/2026-05-18-release-infrastructure-design.md for design.

RELEASABLE_TS      := optio-ui optio-api optio-dashboard
RELEASABLE_PY      := optio-host optio-opencode optio-demo
RELEASE_INDIVIDUAL := $(RELEASABLE_TS) $(RELEASABLE_PY)
WIRE_LOCKED        := optio-contracts optio-core

# Single dispatcher target: delegates to the Python orchestrator.
# Requires BUMP=<level> on the command line.
.PHONY: $(addprefix release-, $(RELEASE_INDIVIDUAL))
$(addprefix release-, $(RELEASE_INDIVIDUAL)): release-%: $(VENV)/bin/python
	@if [ -z "$(BUMP)" ]; then \
	  echo "ERROR: BUMP is required (patch | minor | none | promote-to-1.0)" >&2; \
	  exit 1; \
	fi
	$(PY) scripts/release/run.py per-package $* "$(BUMP)"

# Wire-locked packages: print a helpful message and exit.
.PHONY: $(addprefix release-, $(WIRE_LOCKED))
$(addprefix release-, $(WIRE_LOCKED)):
	@echo "wire-locked: use 'make release-wire BUMP=...' to release optio-contracts + optio-core together." >&2
	@exit 1

.PHONY: release-wire
release-wire: $(VENV)/bin/python  ## Release optio-contracts + optio-core in lockstep (requires BUMP=...)
	@if [ -z "$(BUMP)" ]; then \
	  echo "ERROR: BUMP is required (patch | minor | none | promote-to-1.0)" >&2; \
	  exit 1; \
	fi
	$(PY) scripts/release/run.py wire "$(BUMP)"

.PHONY: release-all
release-all: $(VENV)/bin/python  ## Release every package whose source > registry
	$(PY) scripts/release/run.py all

.PHONY: $(addprefix resume-release-, $(RELEASE_INDIVIDUAL))
$(addprefix resume-release-, $(RELEASE_INDIVIDUAL)): resume-release-%: $(VENV)/bin/python
	$(PY) scripts/release/run.py resume $*

.PHONY: $(addprefix clean-dist-, $(RELEASABLE_PY) $(RELEASABLE_TS) $(WIRE_LOCKED))
$(addprefix clean-dist-, $(RELEASABLE_PY) $(RELEASABLE_TS) $(WIRE_LOCKED)): clean-dist-%:
	rm -rf packages/$*/dist
```

Notes on Make syntax:
- `release-%: $(VENV)/bin/python` is a static pattern rule. Each target name matches `release-%` and depends on the venv.
- `$*` in a static pattern rule expands to the percent-matched part (e.g. for `release-optio-host`, `$*` is `optio-host`).

- [ ] **Step 2: Verify Make targets are recognized**

```bash
make help 2>&1 | head -20
make -n release-optio-host BUMP=patch 2>&1 | head -5
make -n release-wire BUMP=patch 2>&1 | head -5
make -n release-all 2>&1 | head -5
```

Expected: `make help` shows `release-wire` and `release-all` (because they have `##` comments). `make -n release-optio-host BUMP=patch` prints the planned `python scripts/release/run.py per-package optio-host "patch"` command (without executing).

- [ ] **Step 3: Verify wire-lock guard**

```bash
make release-optio-contracts BUMP=patch 2>&1 | head -3
echo "exit code: $?"
```

Expected: prints `wire-locked: use 'make release-wire BUMP=...' to release optio-contracts + optio-core together.` and exit code is non-zero.

- [ ] **Step 4: Verify BUMP-required guard**

```bash
make release-optio-host 2>&1 | head -3
echo "exit code: $?"
```

Expected: prints `ERROR: BUMP is required (patch | minor | none | promote-to-1.0)` and exits non-zero.

- [ ] **Step 5: Commit**

```bash
git add Makefile
git commit -m "release: add Makefile targets for release-*, release-wire, release-all, resume-release-*"
```

---

## Task 13: End-to-end verification (no actual publish)

**Files:** none (verification only)

The acceptance checklist from the spec demands a pre-first-release verification. Run it now against the workspace to confirm everything is in order *before* anyone runs a real `make release-*`.

This task does not commit or publish anything.

- [ ] **Step 1: Confirm `make test` passes against `main`**

```bash
make test
```

Expected: TS + Python suite both PASS.

- [ ] **Step 2: Confirm `make build` succeeds for every package**

```bash
make build
```

Expected: every TS package emits to `dist/`; every Python package emits to `packages/<pkg>/dist/`.

- [ ] **Step 3: For each Python package, confirm `twine check` passes**

```bash
.venv/bin/pip install build twine
for p in optio-core optio-host optio-opencode optio-demo; do
  echo "=== $p ==="
  rm -rf packages/$p/dist
  (cd packages/$p && ../../.venv/bin/python -m build 2>&1 | tail -3)
  .venv/bin/python -m twine check packages/$p/dist/*
done
```

Expected: each package's `twine check` output ends with `PASSED`. If any prints `FAILED`, inspect the error (usually a missing `readme` field, malformed `[project.urls]`, or a non-existent classifier) and fix.

- [ ] **Step 4: For each TS package, inspect the prospective tarball**

```bash
for p in optio-contracts optio-ui optio-api optio-dashboard; do
  echo "=== $p ==="
  (cd packages/$p && pnpm pack 2>&1 | tail -3)
done
```

Then for each, list the tarball contents and check:

```bash
for p in optio-contracts optio-ui optio-api optio-dashboard; do
  tgz=$(ls packages/$p/*.tgz 2>/dev/null | tail -1)
  echo "=== $p ($tgz) ==="
  tar -tzf "$tgz" | grep -E "(package.json|README|dist/)" | head -10
done
```

Expected:
- Every tarball contains `package.json`, `dist/`, and a `README.md`.
- No `link:` paths leak; no source-tree absolute paths inside the tarball.

If any tarball is missing a README (npm uses `README` files at the package root) or contains `link:`-style references, stop and fix before any real release.

- [ ] **Step 5: Run the orchestrator's preflight directly**

```bash
.venv/bin/python scripts/release/run.py preflight
```

Expected: prints `preflight: ok` if the working tree is clean, on `main`, up to date with origin, and `make test` passes.

If preflight fails for an actionable reason (e.g. uncommitted tarball artifacts from step 4), clean up (`git clean -f packages/*/*.tgz`) and re-run.

- [ ] **Step 6: Confirm the helper-script test suite still passes**

```bash
.venv/bin/pytest scripts/release/tests/ -v
```

Expected: all release helper tests PASS.

- [ ] **Step 7: Clean up build/pack artifacts so the tree is publish-ready**

```bash
git clean -f -- packages/*/*.tgz packages/*/dist/ 2>/dev/null
make clean-codegen 2>/dev/null || true
```

Expected: working tree clean. Confirm with:

```bash
git status
```

- [ ] **Step 8: Final acceptance check — print the spec's acceptance list and tick each item**

Walk through the spec's "Acceptance checklist" (in `docs/2026-05-18-release-infrastructure-design.md`) and verify each line:

- [ ] Every publishable package has all required metadata fields (TS) or `pyproject.toml` blocks (Python).
- [ ] `optio-ui`'s `link:` deps are replaced with versioned ranges, and `pnpm install` still resolves cleanly.
- [ ] `optio-host` and `optio-opencode` have a `README.md`.
- [ ] `make release-<pkg> BUMP=<level>` is callable for every publishable individual package (`optio-ui`, `optio-api`, `optio-dashboard`, `optio-host`, `optio-opencode`, `optio-demo`).
- [ ] `make release-wire`, `make release-all`, and `make resume-release-<pkg>` are callable.
- [ ] `make release-optio-contracts` and `make release-optio-core` print the wire-locked error and exit non-zero.
- [ ] `make test` passes against `main`.

If any line cannot be ticked, file what's blocking it.

- [ ] **Step 9: Final commit if anything in `.gitignore` etc. was adjusted**

```bash
git status
```

If anything is dirty (e.g. .gitignore changes to suppress `*.tgz`), commit them:

```bash
git add .gitignore
git commit -m "release: ignore build artifacts produced during pre-release verification"
```

If nothing changed, skip the commit.

---

## After the plan: how to actually do the first releases

This is operational reference, not a checklist task. Run after every step above ticks.

```bash
# First release: wire-locked pair must go first (everyone else depends on these).
make release-wire BUMP=none

# Then non-wire-locked Python packages (host before opencode since opencode depends on host).
make release-optio-host BUMP=none
make release-optio-opencode BUMP=none
make release-optio-demo BUMP=none

# Then TS packages (any order — they only depend on optio-contracts via workspace:*).
make release-optio-ui BUMP=none
make release-optio-api BUMP=none
make release-optio-dashboard BUMP=none
```

Or, as a single convenience after wire:

```bash
make release-wire BUMP=none
make release-all   # publishes every remaining pending package with BUMP=none
```

After the first releases:

- All subsequent releases require a real `BUMP=patch|minor|major|promote-to-1.0`.
- `BUMP=none` is rejected for any package that's already on its registry.

## Follow-ups outside this plan

The spec lists deferred items. They are not implemented here and should be tracked separately if needed:

- CHANGELOG / release notes infrastructure.
- Dry-run / TestPyPI / staging-registry mode.
- Pre-release tags (`-rc.N`, `-alpha.N`, `-beta.N`).
- npm provenance + PyPI trusted publishing via CI.
- Excavator `engine` package switch from in-tree `optio-core` to `optio-core>=0.1,<0.2` once `optio-core` is on PyPI.
