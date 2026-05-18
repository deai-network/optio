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
