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
    if new_text == text:
        # Pin already at the target range; no rewrite needed.
        return False
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
