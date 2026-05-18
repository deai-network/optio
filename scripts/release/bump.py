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
