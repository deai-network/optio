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
import shutil
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
        shutil.rmtree(dist)

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

    # Commit (only if anything actually changed) + tag
    version_changed = new_version != info.current_version
    if version_changed or changed_sibs:
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
            shutil.rmtree(dist)
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

    # Single commit (only if anything actually changed)
    version_changed = new_version != contracts.current_version
    if version_changed or changed_sibs:
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


# --- Batch release -----------------------------------------------------------

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


# --- Resume ------------------------------------------------------------------

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
                    [sys.executable, "-m", "twine", "upload",
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

    sub.add_parser("all", help="Release every package whose source > registry.")

    r = sub.add_parser("resume", help="Resume a partially-completed per-package release.")
    r.add_argument("pkg")

    args = p.parse_args()
    repo_root = Path(__file__).resolve().parents[2]

    if args.cmd == "preflight":
        preflight()
        print("preflight: ok")
    elif args.cmd == "per-package":
        release_per_package(repo_root=repo_root, pkg_name=args.pkg, bump=args.bump)
    elif args.cmd == "wire":
        release_wire(repo_root=repo_root, bump=args.bump)
    elif args.cmd == "all":
        release_all(repo_root=repo_root)
    elif args.cmd == "resume":
        resume(repo_root=repo_root, pkg_name=args.pkg)


if __name__ == "__main__":
    main()
