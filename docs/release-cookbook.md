# Release Cookbook

How to release optio packages to npm / PyPI. Living reference; pairs with the
design doc `docs/2026-05-18-release-infrastructure-design.md`.

The release orchestrator is `scripts/release/run.py`, driven by `make` targets.
Every release: preflight → bump version → build → commit + tag → publish → push.

## Prerequisites (enforced by preflight)

1. **Clean working tree** — no staged or unstaged changes. (pnpm publish also
   fails on *any* untracked file in a package dir.)
2. **On `main`.**
3. **`main` up to date with `origin/main`** — preflight does `git fetch` and
   requires `HEAD == @{u}`. **If `main` has unpushed commits, `git push origin
   main` FIRST**, or every release aborts at preflight.
4. **Publish auth configured**: `npm login` (npm) and `~/.pypirc` or a
   `TWINE_*` token (PyPI). The orchestrator does not handle auth.
5. **Tests**: preflight runs `make test`. Set `OPTIO_SKIP_PREFLIGHT_TESTS=1`
   when you have already verified the suites manually and want to avoid known
   flakes (e.g. the fastify-widget-proxy WS test) re-running on every package.

## Package registry — two lists, keep them in sync

A package is releasable only if it appears in BOTH:

- `scripts/release/run.py`: `TS_PUBLISHABLE` / `PY_PUBLISHABLE` (used by
  `release-all` discovery; **order = release order**).
- `Makefile`: `RELEASABLE_TS` / `RELEASABLE_PY` (generates the `release-<pkg>`
  targets).

`WIRE_LOCKED = {optio-contracts, optio-core}` release together at one version
via `release-wire` (never individually).

## Commands

| Command | Use |
|---|---|
| `make release-wire BUMP=<lvl>` | Release optio-contracts + optio-core in lockstep |
| `make release-<pkg> BUMP=<lvl>` | Release one package |
| `make release-all` | Release every package whose source version ≠ registry (all `BUMP=none`) |
| `make resume-release-<pkg>` | Resume a release that failed mid-way |

`BUMP` ∈ `patch | minor | major | promote-to-1.0 | none`.
`none` publishes the current source version as-is — allowed only when the
package is unpublished or source > registry (used for a first publish, or after
a manual bump).

## Standard flow: "release everything"

1. **Find what changed** since each package's last release tag:
   ```bash
   for p in $(ls packages); do
     tag=$(git tag -l "$p-v*" | sort -V | tail -1)
     [ -n "$tag" ] && echo "$p: $(git rev-list --count "$tag"..HEAD -- "packages/$p") commits"
   done
   ```
   A package with 0 commits since its tag needs no release. A package with no
   tag is new (first publish).

2. **Push `main`** (preflight requirement).

3. **Release changed packages in dependency order** — a package must be
   published AFTER its workspace/sibling deps and BEFORE its consumers:
   - **Python:** `wire` (core) → `optio-host` → `optio-agents` →
     `optio-opencode` → `optio-claudecode` → `optio-grok` → `optio-codex` →
     `optio-cursor` → `optio-kimicode` → `optio-antigravity` →
     `optio-agents-all` → `optio-demo`
     (the agent wrappers `optio-grok` / `optio-codex` / `optio-cursor` /
     `optio-kimicode` / `optio-antigravity` all depend on `optio-agents`+
     `optio-host`; `optio-agents-all` re-exports ALL seven wrappers so it must
     come AFTER every wrapper; `optio-demo` consumes `optio-agents-all` so it is
     last. This matches `PY_PUBLISHABLE` in `run.py` verbatim.)
     First publishes (no prior tag, `BUMP=none`): `optio-kimicode`,
     `optio-antigravity`, `optio-agents-all`.
   - **TS:** `wire` (contracts) → `optio-ui` → `optio-api` →
     `optio-conversation-ui` → `optio-dashboard`
     (`filtrum-core`, `filtrum-mongo` are independent)

   Skip unchanged packages. Use `BUMP=patch` by default; `BUMP=minor` for a
   large feature set. Run one at a time so a failure is easy to resume.

   Example (`OPTIO_SKIP_PREFLIGHT_TESTS=1` after verifying tests manually):
   ```bash
   OPTIO_SKIP_PREFLIGHT_TESTS=1 make release-wire BUMP=patch
   OPTIO_SKIP_PREFLIGHT_TESTS=1 make release-optio-host BUMP=patch
   OPTIO_SKIP_PREFLIGHT_TESTS=1 make release-optio-claudecode BUMP=minor
   # ...etc, in the order above
   ```

`make release-all` automates step 3 (all `BUMP=none`) — but only for packages
whose **source version already differs** from the registry, so you must bump
each changed package's source version first. Per-package in dependency order is
the simpler, more controllable path and is what the examples above use.

### Sibling pins (Python) and compatible ranges

Releasing a Python package rewrites sibling `pyproject.toml` pins to its new
compatible range. For a `0.x` package the range is `>=0.<minor>,<0.<minor+1>`,
so a **patch** bump within the same minor changes no pins (dependents keep
working without a re-release). A **minor** bump moves the range and updates
dependents' pins — those dependents must be re-released. TS packages use
`workspace:*`, which pnpm rewrites to the concrete version at publish time.

## Adding a NEW package to the release scripts

1. **Make `package.json` / `pyproject.toml` publishable.** For a TS package,
   mirror `optio-ui` (the source-distributed template): `name`, `version`,
   `license`, not `private`, `main`/`types`/`exports` → `src/index.ts`, a
   `files` allowlist, a `build` script (`tsc`), sibling deps as `workspace:*`.
2. **Register in both lists, in dependency order** — insert after its
   dependencies and before its consumers:
   - `scripts/release/run.py` → `TS_PUBLISHABLE` or `PY_PUBLISHABLE`
   - `Makefile` → `RELEASABLE_TS` or `RELEASABLE_PY`
3. **First publish:** `make release-<pkg> BUMP=none` (publishes the version set
   in the manifest, e.g. `0.1.0`).

Example: `optio-conversation-ui` was added between `optio-api` and
`optio-dashboard` (depends on `optio-ui`; consumed by `optio-dashboard`).

## When a release fails mid-way

The orchestrator stops on the first failed step and prints the next move.
`make resume-release-<pkg>` diagnoses state (tag present? dist built? already on
registry?) and replays only the missing steps. The wire pair is not resumable —
clean up manually and re-run `make release-wire`.
