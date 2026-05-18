# Release Infrastructure for the Optio Monorepo

This spec was written against the following baseline:

**Base revision:** `7d790cad26c7c90e7da2c0d0fd15ebc6bcaf8a04` on branch `main` (as of 2026-05-18T11:07:14Z)

## Summary

Establish first-class release infrastructure for the eight publishable packages in the optio monorepo: four TypeScript packages to npm.org (public) and four Python packages to PyPI (public). Releases are driven from a maintainer's local machine via `make release-<pkg> BUMP=<level>` targets, using already-configured npm/PyPI credentials. No CI workflows are required for publishing.

The design preserves the existing build/test layout and is additive: it adds new Makefile targets and small helper scripts in `scripts/release/`, backfills missing registry metadata in `package.json` / `pyproject.toml` files, drafts two new READMEs, and removes a small number of in-tree blockers (TS `link:` deps, an outdated `quaestor` package name in `optio-core`'s dependencies).

## Motivation

Today the eight publishable packages cannot be released:

- All TS packages have `"private": true` and lack the metadata fields that npm and consumers expect (description, repository, homepage, bugs, author, README cross-link).
- `optio-ui` declares two filesystem `link:` deps that npm will not accept on publish.
- `optio-core` declares a dependency on `quaestor`, whose canonical PyPI name has been renamed to `mongo-quaestor`. Source imports stay valid.
- Python packages lack `readme = "README.md"`, `[project.urls]`, `authors`, and Trove classifiers, leaving PyPI pages bare and uncategorizable.
- `optio-host` and `optio-opencode` have no README.md.
- No release tooling exists — there's nothing to bump versions, update sibling pins, build, tag, publish, push.

Beyond unblocking publication, two practical drivers:

1. **`Patch Excavator` follow-up.** Excavator already consumes `optio-core` and `optio-opencode` directly from the workspace. Once these packages are published, excavator's deps can switch from "in-tree expectation" to versioned PyPI deps, and the optio refactor cycle can decouple from excavator's editable-install loop.
2. **Standalone server + dashboard.** A future thin wrapper around `optio-dashboard` is on the roadmap. That requires `optio-dashboard`, `optio-api`, and `optio-ui` to be installable from npm.

## Out of scope

The following are intentionally deferred. They are documented here so future maintainers know they were considered:

- **CI-driven release.** Publishing stays local for this iteration. Tokens stay on the maintainer's machine. Promoting to CI later means wrapping the same Make targets inside a workflow.
- **CHANGELOG / release notes infrastructure.** Defer until external consumers ask. Git log per package path is the de facto record for now.
- **Dry-run / TestPyPI / staging-registry mode.** No `DRY_RUN=1` or `STAGING=1` toggles. Failed publishes are cheap to diagnose given the Make target's clear logging.
- **Pre-release tags** (`-rc.N`, `-alpha.N`, `-beta.N`). No concrete need yet; downstream consumers can verify against `main` builds before a tagged release.
- **npm provenance + PyPI trusted publishing (OIDC).** Both require publishing from CI; revisit alongside that change.
- **Conventional Commits enforcement / release-please / changesets.** Skipped; the project's commit messages aren't conventional-format, and a manual `BUMP=` flag is enough signal for a project at this scale.

## Architecture overview

### Publishable packages (8)

| Registry | Package | Role |
|---|---|---|
| npm.org | `optio-contracts` | Wire-protocol types and ts-rest contracts |
| npm.org | `optio-ui` | React components consumed by optio dashboards |
| npm.org | `optio-api` | Server-side API with Fastify/Express/Next.js adapters |
| npm.org | `optio-dashboard` | End-user-installable dashboard app (has `bin`) |
| PyPI | `optio-core` | Async process management library; the wire-protocol Python side |
| PyPI | `optio-host` | Local-or-remote host abstraction + log/deliverables protocol |
| PyPI | `optio-opencode` | Run opencode web as an optio task |
| PyPI | `optio-demo` | End-user-installable demo exercising optio-core features |

Not published: `packages/optio-demo/interop` (test harness; stays private with `"private": true`).

### Versioning model: wire-coupled lockstep, rest independent

- **`optio-contracts` (TS) and `optio-core` (Py) share a single version number** and always release together via `make release-wire BUMP=<level>`. They describe the same RPC wire protocol on both sides; running with mismatched versions is a wire-incompatibility bug waiting to happen.
- **Every other package versions independently.** A patch to `optio-ui` does not bump anything else; a minor bump in `optio-opencode` does not touch `optio-host`.
- **Inter-package dep declarations:**
  - TS sibs of `optio-contracts` pin via `workspace:*`; pnpm rewrites this to the published version at publish time. No tooling needed.
  - Py sibs of `optio-core` (i.e. `optio-host`, `optio-opencode`, `optio-demo`) declare e.g. `optio-core>=0.1,<0.2`. When `make release-wire` ships a new version, those pins get auto-updated by the release tooling.
  - Same auto-update behavior for Py sibs that pin `optio-host` (i.e. `optio-opencode`) and `optio-opencode` (i.e. `optio-demo`).

### Pre-1.0 semantics

All packages start at `0.1.0`. While a package's version is `0.x.y`:

- `BUMP=patch` → `0.x.y` → `0.x.(y+1)`. Non-breaking bug fixes.
- `BUMP=minor` → `0.x.y` → `0.(x+1).0`. Anything else, including breaking changes (standard pre-1.0 convention).
- `BUMP=major` → **rejected**. The Make target prints: "package is pre-1.0; use `BUMP=minor` for breaking changes, or `BUMP=promote-to-1.0` to graduate to 1.0".
- `BUMP=promote-to-1.0` → `0.x.y` → `1.0.0`. The explicit graduation path. Valid exactly once per package.
- `BUMP=none` → ship the current version-in-file as-is. Valid only when the package has zero releases on its registry (i.e. the very first release).

After a package is at `1.x.y`, `BUMP=major` is accepted with standard semver semantics; `BUMP=promote-to-1.0` is rejected.

### First-release policy

Every package's source file currently reads `version = "0.1.0"`. The first release of each package ships exactly that version, via the `BUMP=none` special case. After the first release, `BUMP=none` is no longer valid for that package.

## Makefile interface

### Per-package release targets

```
make release-optio-ui        BUMP=patch|minor|none|promote-to-1.0
make release-optio-api       BUMP=patch|minor|none|promote-to-1.0
make release-optio-dashboard BUMP=patch|minor|none|promote-to-1.0
make release-optio-host      BUMP=patch|minor|none|promote-to-1.0
make release-optio-opencode  BUMP=patch|minor|none|promote-to-1.0
make release-optio-demo      BUMP=patch|minor|none|promote-to-1.0
```

- `BUMP` is required. No default. Bare `make release-optio-host` errors out with: `BUMP is required (patch | minor | none | promote-to-1.0)`.
- `BUMP=major` on a `0.x` package is rejected as described above.
- `BUMP=none` is rejected if any version of the package is already on its registry.

### Wire-locked target

```
make release-wire BUMP=patch|minor|none|promote-to-1.0
```

Bumps `optio-contracts` and `optio-core` together to the same new version, updates dep pins in all siblings (TS sibs use `workspace:*` so no edit needed; Py sibs of `optio-core` get pin auto-update), runs the full preflight, builds both, tags `optio-contracts-v<X.Y.Z>` *and* `optio-core-v<X.Y.Z>`, publishes both, and pushes.

The individual targets `make release-optio-contracts` and `make release-optio-core` are *also defined*, but they print `wire-locked: use make release-wire BUMP=...` and exit non-zero. This avoids the surprise of "someone forgot the lockstep."

### Convenience target

```
make release-all
```

Loops over every publishable package whose source-file version is *ahead of* what's on its registry, and runs the relevant release target with `BUMP=none`. Use case: after a series of partial-failure runs or hand-bumped version files, push everything that's pending. Does *not* accept a `BUMP=` flag — it's a "publish pending" target only. Refuses to do anything if every source version equals its registry version (i.e. there's nothing pending).

### Resume target

```
make resume-release-<pkg>
```

Used after a `make release-<pkg>` failure (see "Failure handling" below). Inspects the working tree, tags, and registry state to determine which step to retry; prints what it intends to do before doing it. Refuses to run if state is ambiguous.

### Required pre-existing targets, slightly extended

- `make test` — runs the full TS + Python test suite. Called by release preflight. Must pass before any release proceeds.
- `make build` — already exists; release targets invoke it for the package being released.
- `make clean-dist-<pkg>` — new; wipes `packages/<pkg>/dist/` for a clean rebuild before publish.

## Release flow

Walkthrough of `make release-optio-core BUMP=minor` (note: this would actually be `make release-wire BUMP=minor` per the lockstep rule, but the same steps apply to any per-package release):

1. **Preflight.** Abort with a clear message if any of these fail:
   - Working tree clean (no uncommitted changes).
   - On `main` branch.
   - `main` is up to date with `origin/main`.
   - **Full** test suite (`make test`) passes.
   - `make build` succeeds for this package.
   - `twine check dist/*` passes (Python only).
   - Source version of this package isn't already on its registry (unless `BUMP=none` is permitted as the first-release flag).
2. **Compute new version** from current `pyproject.toml` / `package.json` + `BUMP=`. Reject `BUMP=major` on pre-1.0 packages here with the helpful error described earlier.
3. **Edit version file** to the new version.
4. **TS sibling deps via `workspace:*`** — no edit needed; pnpm rewrites at publish time.
5. **Python sibling deps** — for every other Python package whose `dependencies` lists this package by name, rewrite the pin to `>=<new>,<<next-major>` (e.g. `optio-core>=0.2,<0.3` after `optio-core` releases `0.2.0`).
6. **Commit** the version bump + sibling-pin edits: `release(optio-core): 0.2.0` (or `release(wire): 0.2.0` for the wire-locked target).
7. **Tag.** For per-package releases: `optio-<pkg>-v<X.Y.Z>`. For wire-locked release: both `optio-contracts-v<X.Y.Z>` and `optio-core-v<X.Y.Z>`.
8. **Build** clean artifact (`pnpm build` for TS, `python -m build` for Py).
9. **Publish.**
   - TS: `pnpm publish --access public`
   - Python: `twine upload dist/optio_core-<X.Y.Z>*`
10. **Push** commit + tag(s) to `origin`.
11. **Print** human-readable summary: package, new version, registry URL, GitHub tag URL.

For the wire-locked `make release-wire`: steps 2–10 are repeated for both `optio-contracts` and `optio-core` within a single Make invocation. The two packages share the same new version number, so step 2 is computed once. A single git commit edits both files (and all affected sibling pins); two tags are created; both artifacts are built; both publishes happen back-to-back; one push covers everything.

## Failure handling

Per Q13: no automatic rollback. The Make target stops at the first failed step, prints the step name and the underlying error, and prints exactly what unblocks recovery:

- If the failure was *before* the version-bump commit: rerun the Make target after fixing the underlying issue.
- If the failure was *after* the version-bump commit but *before* publish: run `make resume-release-<pkg>`. The resume target detects via git tags / `dist/` artifacts / registry state what step to retry from.
- If the failure was *after* a partial publish (e.g. npm succeeded, PyPI failed for the wire-locked case): the resume target detects this from registry state and only retries the missing publish step. The commit and tags stay in place since the version is now real on at least one registry.

Rollback (deleting the commit, deleting the tag) is intentionally *not* automated. Deleting a tag pushed to origin is a destructive action; deleting a published version on npm or PyPI is bounded by the registry's recall window. The maintainer can do either manually if they decide that's the right move.

## Package metadata (pre-publish prep)

Every publishable package needs metadata that's currently missing. This is one-time pre-work; everything below is mechanical.

### TS packages — `optio-contracts`, `optio-ui`, `optio-api`, `optio-dashboard`

For each `package.json`:

1. **Remove `"private": true`.**
2. **Add** the following fields:
   ```json
   "description": "<one-line role description>",
   "repository": {
     "type": "git",
     "url": "git+https://github.com/deai-network/optio.git",
     "directory": "packages/<pkg>"
   },
   "homepage": "https://github.com/deai-network/optio/tree/main/packages/<pkg>#readme",
   "bugs": { "url": "https://github.com/deai-network/optio/issues" },
   "author": "Kristof Csillag <kristof.csillag@deai-labs.com>"
   ```
3. **Description draft per package** (refine as needed during implementation):
   - `optio-contracts`: "Shared wire-protocol types and ts-rest contracts for the optio task runner."
   - `optio-ui`: "React components for embedding optio process management UIs into ts-rest applications."
   - `optio-api`: "Server-side optio API with Fastify, Express, and Next.js adapters."
   - `optio-dashboard`: "Standalone optio dashboard app — install and run for a ready-to-use process management UI."
4. **No `keywords` field** (per Q15, low return).

#### `optio-ui` additional fix

Replace the two `link:` deps with versioned ranges:

```diff
-    "@quaesitor-textus/antd": "link:../../../quaesitor-textus/packages/antd",
-    "@quaesitor-textus/core": "link:../../../quaesitor-textus/packages/core",
+    "@quaesitor-textus/antd": "^0.1.6",
+    "@quaesitor-textus/core": "^0.1.6",
```

(Verify each published version against npm before locking the exact range; the example assumes both are at 0.1.6 today.)

### Python packages — `optio-core`, `optio-host`, `optio-opencode`, `optio-demo`

For each `pyproject.toml`:

1. **Add `readme = "README.md"`** to the `[project]` table so PyPI renders the README.
2. **Add `authors`**:
   ```toml
   authors = [
       { name = "Kristof Csillag", email = "kristof.csillag@deai-labs.com" },
   ]
   ```
3. **Add `[project.urls]` block**:
   ```toml
   [project.urls]
   Homepage = "https://github.com/deai-network/optio"
   Repository = "https://github.com/deai-network/optio"
   Issues = "https://github.com/deai-network/optio/issues"
   ```
4. **Add Trove classifiers** (`[project]` table, `classifiers = [...]`). Common base for every Python package:
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
   ]
   ```
   Per-package topic additions:
   - `optio-core`:
     - `Topic :: Software Development :: Libraries :: Python Modules`
     - `Topic :: System :: Distributed Computing`
     - `Framework :: AsyncIO`
   - `optio-host`:
     - `Topic :: Software Development :: Libraries :: Python Modules`
     - `Topic :: System :: Distributed Computing`
     - `Topic :: System :: Systems Administration`
     - `Framework :: AsyncIO`
   - `optio-opencode`:
     - `Topic :: Software Development :: Libraries :: Python Modules`
     - `Topic :: Scientific/Engineering :: Artificial Intelligence`
     - `Topic :: Software Development :: Code Generators`
     - `Framework :: AsyncIO`
   - `optio-demo`:
     - `Topic :: Software Development`
     - `Topic :: Software Development :: Libraries :: Application Frameworks`
5. **Tighten existing sibling deps to ranges** where they're currently bare:
   - `optio-host`: `optio-core` → `optio-core>=0.1,<0.2`
   - `optio-opencode`: `optio-core` → `optio-core>=0.1,<0.2`; `optio-host` → `optio-host>=0.1,<0.2`
   - `optio-demo`: `optio-core[redis]` → `optio-core[redis]>=0.1,<0.2`; `optio-opencode` → `optio-opencode>=0.1,<0.2`
   - `optio-core`'s `motor`, `apscheduler`, `clamator-*`, `pydantic` already have ranges; leave alone.
   - `optio-core`'s `mongo-quaestor` is already at `>=0.1,<0.2` (fixed during this brainstorm).

### Missing READMEs

`optio-host` and `optio-opencode` have no README.md. Two drafts were agreed during this brainstorm and are reproduced below for the implementation plan to use verbatim (subject to a final review pass).

#### `packages/optio-host/README.md`

````markdown
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
````

#### `packages/optio-opencode/README.md`

````markdown
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
````

## Helper scripts

Two small Python scripts live in `scripts/release/`. Each is a pure function over file content, easy to unit-test in isolation.

### `scripts/release/bump.py`

CLI:

```
python scripts/release/bump.py <package_dir> <bump_level>
```

Reads the current version from `<package_dir>/pyproject.toml` (preferred) or `<package_dir>/package.json`, applies the bump per the rules in "Pre-1.0 semantics," writes the new version back, and prints the new version to stdout. Exits non-zero with a clear message on any rejected bump (`major` on pre-1.0, `promote-to-1.0` on already-1.x, etc).

### `scripts/release/update_sibling_pins.py`

CLI:

```
python scripts/release/update_sibling_pins.py <package_name> <new_version>
```

For every other Python package's `pyproject.toml`, finds any `dependencies` entry referencing `<package_name>` (with or without an extras suffix like `[redis]`, with or without an existing version range) and rewrites the pin to `>=<new>,<<next-major>`. Preserves the extras suffix. Prints what it edited.

For TS packages: no-op — `workspace:*` already does the right thing at publish time.

### Makefile targets call them

Each `release-*` Make target is a short shell pipeline: preflight checks → `bump.py` → `update_sibling_pins.py` (Python only) → `git commit` → `git tag` → `pnpm build` or `python -m build` → `pnpm publish --access public` or `twine upload` → `git push` → summary echo. No clever logic; each step is a one-liner that's easy to read.

## Pre-first-release verification

Before running any `make release-*` for the very first time, do a dry trial:

1. All prep work above merged to `main`.
2. `make test` passes the full TS + Python suite.
3. `make build` succeeds end-to-end.
4. For Python packages: `python -m twine check dist/*` passes.
5. For TS packages: `pnpm pack --dry-run` (or unpacking the produced tarball) reveals no `link:` references, no source-tree absolute paths, no leaking dotfiles.

Once the trial passes, the first real run is:

```
make release-wire BUMP=none           # publishes optio-contracts@0.1.0 + optio-core@0.1.0 first (everything else depends on these)
make release-optio-host BUMP=none
make release-optio-opencode BUMP=none
make release-optio-ui BUMP=none
make release-optio-api BUMP=none
make release-optio-dashboard BUMP=none
make release-optio-demo BUMP=none
```

Or, equivalently, `make release-all` after the first wire release completes.

## Open questions for implementation

These are details that don't affect the design's correctness but do need a small decision when writing code:

- **Where to put the "release in progress" marker**, if any, for `make resume-release-*` to disambiguate state. A small file under `.release/` (gitignored) is one option; reading git state and registry state directly is another. Pick the one with the simpler code path during implementation.
- **`pnpm pack --dry-run` vs. `pnpm pack` then inspect** for the pre-flight check. Either is acceptable; `--dry-run` is faster but slightly less faithful.

## Acceptance checklist

This spec is satisfied when:

- [ ] Every publishable package has all the required metadata fields (TS) or `pyproject.toml` blocks (Python) backfilled.
- [ ] `optio-ui`'s `link:` deps are replaced with versioned ranges, and `pnpm install` still resolves cleanly.
- [ ] `optio-host` and `optio-opencode` have a README.md per the drafts above.
- [ ] `make release-<pkg> BUMP=<level>` is a callable target for every publishable package, plus `make release-wire`, `make release-all`, and `make resume-release-<pkg>`.
- [ ] `make release-optio-contracts` and `make release-optio-core` print a "wire-locked" error and exit non-zero.
- [ ] `make test` passes against `main`.
- [ ] Pre-first-release verification (above) passes for every publishable package.
- [ ] (Optional, not part of this spec) Excavator's `engine` package switches from in-tree `optio-core` to `optio-core>=0.1,<0.2` once optio-core is on PyPI.
