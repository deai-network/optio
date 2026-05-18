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


# --- Wire-locked release -----------------------------------------------------

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


# --- CLI ---------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Optio release orchestrator")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("preflight", help="Run pre-release checks only.")

    pp = sub.add_parser("per-package", help="Release a single package.")
    pp.add_argument("pkg")
    pp.add_argument("bump", choices=["patch", "minor", "major", "promote-to-1.0", "none"])

    w = sub.add_parser("wire", help="Release optio-contracts + optio-core together.")
    w.add_argument("bump", choices=["patch", "minor", "major", "promote-to-1.0", "none"])

    args = p.parse_args()
    repo_root = Path(__file__).resolve().parents[2]

    if args.cmd == "preflight":
        preflight()
        print("preflight: ok")
    elif args.cmd == "per-package":
        release_per_package(repo_root=repo_root, pkg_name=args.pkg, bump=args.bump)
    elif args.cmd == "wire":
        release_wire(repo_root=repo_root, bump=args.bump)


if __name__ == "__main__":
    main()
