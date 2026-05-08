# Engine RPC migration — phase 1 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the new clamator RPC contract surface and supporting tooling without changing runtime behavior.

**Architecture:** Phase 1 introduces `engine-to-api.ts` (clamator contract), renames `contract.ts` to `api-to-frontend.ts`, ships committed codegen output for both languages, adds a top-level `Makefile` and `git config core.hooksPath`-style pre-commit hook for drift detection, and updates docs to declare the authority rule. Engine and API still talk via the legacy redis stream after phase 1.

**Tech Stack:** TypeScript (pnpm workspace, vitest, ts-rest, zod), Python (setuptools, pytest, pydantic), `@clamator/codegen` 0.1.1, `@clamator/protocol`, `@clamator/over-redis`, `clamator-protocol`, `clamator-over-redis`, GNU make, bash.

**Working dir:** `/home/csillag/deai/optio/.worktrees/redis-migration-1` (branch `redis-migration-1`, off `csillag/rpc-migration-1`).

**Reference docs:**
- `docs/2026-05-08-engine-rpc-migration-design.md` — parent spec.
- `docs/2026-05-08-engine-rpc-migration-phase-1-design.md` — phase-1 decisions and commit sequence.

---

## File map

**Created**
- `packages/optio-contracts/src/engine-to-api.ts` — clamator engine RPC contract.
- `packages/optio-contracts/src/__tests__/engine-contract.test.ts` — parse-shape tests for discriminated unions.
- `packages/optio-api/src/_generated/engine.ts` — codegen output (committed).
- `packages/optio-core/src/optio_core/_generated/engine.py` — codegen output (committed).
- `packages/optio-core/src/optio_core/_generated/__init__.py` — codegen output marker (committed).
- `Makefile` — top-level.
- `scripts/git-hooks/pre-commit` — drift-check hook.
- `scripts/install-hooks.sh` — one-line installer.

**Renamed**
- `packages/optio-contracts/src/contract.ts` → `api-to-frontend.ts`.

**Modified**
- `packages/optio-contracts/src/index.ts` — re-export source updated; failure-reason re-exports added.
- `packages/optio-contracts/package.json` — `@clamator/protocol` dep, subpath export.
- `packages/optio-contracts/AGENTS.md` — line 158 path edit (commit 1); Package structure section (commit 5).
- `packages/optio-contracts/README.md` — Contracts section rewrite (commit 5).
- `packages/optio-api/package.json` — `@clamator/protocol` + `@clamator/over-redis` deps.
- `packages/optio-core/pyproject.toml` — `clamator-protocol` + `clamator-over-redis` deps.
- `package.json` (root) — `@clamator/codegen` devDep.
- `README.md` (root) — Authority and data flow section.
- `AGENTS.md` (root) — Architecture Notes update.
- `docs/2026-05-08-engine-rpc-migration-design.md` — fold phase-1 decisions Q2, Q5, Q7, Q9, Q10 back into parent spec (commit 5).

---

## Task 1: Rename HTTP contract file

**Files:**
- Rename: `packages/optio-contracts/src/contract.ts` → `packages/optio-contracts/src/api-to-frontend.ts`
- Modify: `packages/optio-contracts/src/index.ts`
- Modify: `packages/optio-contracts/AGENTS.md` (line 158)

- [ ] **Step 1: Rename via git mv to preserve history**

```bash
cd /home/csillag/deai/optio/.worktrees/redis-migration-1
git mv packages/optio-contracts/src/contract.ts packages/optio-contracts/src/api-to-frontend.ts
```

- [ ] **Step 2: Update `optio-contracts/src/index.ts` re-export source**

Edit the line `export { processesContract, discoveryContract } from './contract.js';` to:

```typescript
export { processesContract, discoveryContract } from './api-to-frontend.js';
```

- [ ] **Step 3: Update AGENTS.md line 158 path reference**

Edit `packages/optio-contracts/AGENTS.md`. Replace the substring `ts-rest router exported from \`contract.ts\`` with `ts-rest router exported from \`api-to-frontend.ts\``.

- [ ] **Step 4: Verify no stragglers reference the old name**

```bash
grep -rn "contract\\.ts\\|from .*'\\./contract'" packages/ --include='*.ts' --include='*.tsx' --include='*.md' --include='*.json'
```
Expected: no matches outside this design's own doc files (docs/ paths are OK).

- [ ] **Step 5: Build + tests green**

```bash
pnpm -r build
pnpm -r test
```
Expected: all packages build, all vitest suites pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(optio-contracts): rename contract.ts to api-to-frontend.ts"
```

---

## Task 2: Add engine RPC contract

**Files:**
- Modify: `packages/optio-contracts/package.json`
- Create: `packages/optio-contracts/src/engine-to-api.ts`
- Create: `packages/optio-contracts/src/__tests__/engine-contract.test.ts`
- Modify: `packages/optio-contracts/src/index.ts`

- [ ] **Step 1: Add `@clamator/protocol` runtime dep to optio-contracts**

Edit `packages/optio-contracts/package.json` `dependencies` block to add `"@clamator/protocol": "^0.1.0"` (alphabetical placement after `@ts-rest/core`). Keep zod and the others.

- [ ] **Step 2: Install**

```bash
pnpm install
```
Expected: `@clamator/protocol` resolves; lockfile updated.

- [ ] **Step 3: Write the failing test for engine contract parse shape**

Create `packages/optio-contracts/src/__tests__/engine-contract.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import { engineContract, LaunchFailureReason } from '../engine-to-api.js';

describe('engineContract', () => {
  it('declares the expected service name', () => {
    expect(engineContract.service).toBe('engine');
  });

  it('exposes launch as a method with discriminated-union result', () => {
    const launch = engineContract.methods.launch;
    expect(launch).toBeDefined();
    const ok = launch.result.parse({
      ok: true,
      process: {
        _id: '507f1f77bcf86cd799439011',
        processId: 'p1',
        name: 'P1',
        rootId: '507f1f77bcf86cd799439011',
        depth: 0,
        order: 0,
        cancellable: true,
        status: { state: 'idle' },
        progress: { percent: null },
        log: [],
        createdAt: new Date().toISOString(),
      },
    });
    expect(ok.ok).toBe(true);
    const fail = launch.result.parse({ ok: false, reason: 'not-found' });
    expect(fail.ok).toBe(false);
    if (!fail.ok) expect(fail.reason).toBe('not-found');
  });

  it('rejects an unknown LaunchFailureReason', () => {
    expect(() => LaunchFailureReason.parse('bogus')).toThrow();
  });

  it('exposes resync as a notification (no result schema)', () => {
    const resync = engineContract.methods.resync;
    expect(resync).toBeDefined();
    expect((resync as { result?: unknown }).result).toBeUndefined();
  });
});
```

- [ ] **Step 4: Run test, verify it fails**

```bash
cd packages/optio-contracts && pnpm vitest run src/__tests__/engine-contract.test.ts
```
Expected: FAIL — module `../engine-to-api.js` not found.

- [ ] **Step 5: Create `packages/optio-contracts/src/engine-to-api.ts`**

```typescript
import { z } from 'zod';
import { defineContract, defineMethod, defineNotification } from '@clamator/protocol';
import { ProcessSchema, ProcessMetadataFilterSchema } from './schemas/process.js';

const ProcessIdParam = z.string().min(1);

export const LaunchFailureReason = z.enum([
  'not-found',
  'not-launchable',
  'no-resume-support',
  'launch-blocked',
]);

export const CancelFailureReason = z.enum([
  'not-found',
  'not-cancellable',
]);

export const DismissFailureReason = z.enum([
  'not-found',
  'not-dismissable',
]);

export const GroupCancelFailureReason = z.enum([
  'invalid-persist-without-block',
]);

export const BlockLaunchesFailureReason = z.enum([
  'invalid-filter',
]);

export type LaunchFailureReason = z.infer<typeof LaunchFailureReason>;
export type CancelFailureReason = z.infer<typeof CancelFailureReason>;
export type DismissFailureReason = z.infer<typeof DismissFailureReason>;
export type GroupCancelFailureReason = z.infer<typeof GroupCancelFailureReason>;
export type BlockLaunchesFailureReason = z.infer<typeof BlockLaunchesFailureReason>;

const launchResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), process: ProcessSchema }),
  z.object({ ok: z.literal(false), reason: LaunchFailureReason }),
]);

const cancelResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), process: ProcessSchema }),
  z.object({ ok: z.literal(false), reason: CancelFailureReason }),
]);

const dismissResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), process: ProcessSchema }),
  z.object({ ok: z.literal(false), reason: DismissFailureReason }),
]);

const groupCancelResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), cancelledCount: z.number().int().nonnegative() }),
  z.object({ ok: z.literal(false), reason: GroupCancelFailureReason }),
]);

const groupCancelAndWaitResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), cancelledCount: z.number().int().nonnegative() }),
  z.object({ ok: z.literal(false), reason: GroupCancelFailureReason }),
]);

const blockLaunchesResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true) }),
  z.object({ ok: z.literal(false), reason: BlockLaunchesFailureReason }),
]);

const unblockLaunchesResult = z.object({
  removed: z.number().int().nonnegative(),
});

export const engineContract = defineContract('engine', {
  launch: defineMethod({
    params: z.object({
      processId: ProcessIdParam,
      resume: z.boolean().optional(),
    }),
    result: launchResult,
  }),
  cancel: defineMethod({
    params: z.object({ processId: ProcessIdParam }),
    result: cancelResult,
  }),
  dismiss: defineMethod({
    params: z.object({ processId: ProcessIdParam }),
    result: dismissResult,
  }),
  groupCancel: defineMethod({
    params: z.object({
      metadataFilter: ProcessMetadataFilterSchema,
      blockNewLaunches: z.boolean().optional(),
      persist: z.boolean().optional(),
      reason: z.string().optional(),
    }),
    result: groupCancelResult,
  }),
  groupCancelAndWait: defineMethod({
    params: z.object({
      metadataFilter: ProcessMetadataFilterSchema,
      blockNewLaunches: z.boolean().optional(),
      persist: z.boolean().optional(),
      reason: z.string().optional(),
    }),
    result: groupCancelAndWaitResult,
  }),
  blockLaunches: defineMethod({
    params: z.object({
      launchFilter: ProcessMetadataFilterSchema,
      reason: z.string().optional(),
    }),
    result: blockLaunchesResult,
  }),
  unblockLaunches: defineMethod({
    params: z.object({ launchFilter: ProcessMetadataFilterSchema }),
    result: unblockLaunchesResult,
  }),
  resync: defineNotification({
    params: z.object({
      clean: z.boolean().optional(),
      metadataFilter: ProcessMetadataFilterSchema.optional(),
    }),
  }),
});
```

- [ ] **Step 6: Run test, verify it passes**

```bash
cd packages/optio-contracts && pnpm vitest run src/__tests__/engine-contract.test.ts
```
Expected: PASS — all four `it` blocks green.

- [ ] **Step 7: Re-export failure-reason enums from optio-contracts root**

Edit `packages/optio-contracts/src/index.ts`. Append:

```typescript
// Engine contract failure-reason enums (Zod schemas + types)
export {
  LaunchFailureReason,
  CancelFailureReason,
  DismissFailureReason,
  GroupCancelFailureReason,
  BlockLaunchesFailureReason,
} from './engine-to-api.js';
```

Note: do NOT re-export `engineContract` itself — codegen consumes it via the subpath added in step 8.

- [ ] **Step 8: Add subpath export to optio-contracts package.json**

Edit `packages/optio-contracts/package.json` `exports` block:

```json
"exports": {
  ".": {
    "import": "./dist/index.js",
    "types": "./dist/index.d.ts"
  },
  "./engine-to-api": {
    "import": "./dist/engine-to-api.js",
    "types": "./dist/engine-to-api.d.ts"
  }
}
```

- [ ] **Step 9: Build + full test suite green**

```bash
cd /home/csillag/deai/optio/.worktrees/redis-migration-1
pnpm -r build
pnpm -r test
```
Expected: all builds and tests pass.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "feat(optio-contracts): add engine-to-api clamator contract"
```

---

## Task 3: Codegen tooling, clamator runtime deps, generated output

**Files:**
- Modify: `package.json` (root)
- Modify: `packages/optio-api/package.json`
- Modify: `packages/optio-core/pyproject.toml`
- Create: `Makefile` (root)
- Create: `packages/optio-api/src/_generated/engine.ts` (via codegen)
- Create: `packages/optio-core/src/optio_core/_generated/engine.py` (via codegen)
- Create: `packages/optio-core/src/optio_core/_generated/__init__.py` (via codegen)

- [ ] **Step 1: Add `@clamator/codegen` to root devDependencies**

Edit root `package.json`:

```json
{
  "name": "optio-monorepo",
  "private": true,
  "license": "Apache-2.0",
  "scripts": {
    "build": "pnpm -r build",
    "test": "pnpm -r test"
  },
  "engines": { "node": ">=20" },
  "devDependencies": {
    "@clamator/codegen": "^0.1.1"
  }
}
```

- [ ] **Step 2: Add clamator runtime deps to optio-api**

Edit `packages/optio-api/package.json` `dependencies` to add (alphabetical):

```json
"@clamator/over-redis": "^0.1.0",
"@clamator/protocol": "^0.1.0",
```

- [ ] **Step 3: Install TS deps**

```bash
pnpm install
```
Expected: lockfile updated, all clamator packages resolve.

- [ ] **Step 4: Add clamator runtime deps to optio-core**

Edit `packages/optio-core/pyproject.toml`. Replace the `dependencies` block with:

```toml
dependencies = [
    "motor>=3.3.0",
    "apscheduler>=4.0.0a5",
    "quaestor",
    "clamator-protocol>=0.1.0",
    "clamator-over-redis>=0.1.0",
    "pydantic>=2.0",
]
```

(`pydantic` is required by generated Python code; add explicitly even if pulled transitively.)

- [ ] **Step 5: Reinstall optio-core in editable mode**

```bash
cd packages/optio-core && pip install -e .[dev,redis]
```
Expected: clamator-protocol + clamator-over-redis + pydantic resolved.

- [ ] **Step 6: Create root `Makefile`**

```bash
cd /home/csillag/deai/optio/.worktrees/redis-migration-1
```

Write `Makefile`:

```makefile
.DEFAULT_GOAL := help
.PHONY: help install build codegen test lint clean clean-codegen clean-deep

PY_PACKAGES := optio-core optio-host optio-opencode

help:  ## Show this help
	@awk 'BEGIN { FS = ":.*##" } /^[a-zA-Z_-]+:.*##/ { printf "  \033[1m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install:  ## Install dependencies (TS workspace + Python packages)
	pnpm install
	for pkg in $(PY_PACKAGES); do \
	  (cd packages/$$pkg && pip install -e .[dev] 2>/dev/null || pip install -e .); \
	done

build:  ## Build all packages
	pnpm -r build
	for pkg in $(PY_PACKAGES); do \
	  (cd packages/$$pkg && python -m build 2>/dev/null || true); \
	done

codegen:  ## Regenerate clamator RPC client/server stubs from optio-contracts source
	pnpm exec clamator-codegen \
	  --src packages/optio-contracts/src \
	  --out-ts packages/optio-api/src/_generated \
	  --out-py packages/optio-core/src/optio_core/_generated \
	  --ts-contract-import 'optio-contracts/engine-to-api'

test:  ## Run all tests (TS + Python; per-package, no docker)
	pnpm -r test
	for pkg in $(PY_PACKAGES); do \
	  (cd packages/$$pkg && pytest); \
	done

lint:  ## Lint all packages
	pnpm -r lint 2>/dev/null || true
	for pkg in $(PY_PACKAGES); do \
	  (cd packages/$$pkg && ruff check . 2>/dev/null || true); \
	done

clean:  ## Remove build artifacts and dependency caches (KEEPS committed _generated/)
	pnpm -r clean 2>/dev/null || true
	rm -rf node_modules packages/*/node_modules packages/*/dist
	for pkg in $(PY_PACKAGES); do \
	  (cd packages/$$pkg && rm -rf build dist *.egg-info .pytest_cache); \
	done
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +

clean-codegen:  ## Remove generated clamator stubs (require make codegen to rebuild)
	rm -rf packages/optio-api/src/_generated
	rm -rf packages/optio-core/src/optio_core/_generated

clean-deep: clean clean-codegen  ## clean + clean-codegen (full reset)
```

- [ ] **Step 7: Run codegen**

```bash
make codegen
```
Expected: prints clamator-codegen output. Creates `packages/optio-api/src/_generated/engine.ts` and `packages/optio-core/src/optio_core/_generated/engine.py` (and `__init__.py` if produced).

- [ ] **Step 8: Verify codegen idempotency (the deterministic-output requirement)**

```bash
make codegen
git status --short -- packages/optio-api/src/_generated packages/optio-core/src/optio_core/_generated
```
Expected: shows files exist (untracked). Now run codegen a third time and diff:

```bash
make codegen
git diff -- packages/optio-api/src/_generated packages/optio-core/src/optio_core/_generated
```
Expected: empty diff. If non-empty, STOP. File issue upstream and consult phase-1 design §7 risks before proceeding.

- [ ] **Step 9: Verify generated TS compiles**

```bash
pnpm -r build
```
Expected: optio-api builds; `_generated/engine.ts` typechecks against `@clamator/protocol`.

- [ ] **Step 10: Verify generated Python imports**

```bash
cd packages/optio-core && python -c "from optio_core._generated import engine; print(engine.__name__)"
```
Expected: prints `optio_core._generated.engine` (or similar). No import errors.

- [ ] **Step 11: Run all tests**

```bash
cd /home/csillag/deai/optio/.worktrees/redis-migration-1
make test
```
Expected: all green.

- [ ] **Step 12: Commit**

```bash
git add -A
git commit -m "feat: add Makefile, clamator deps, and committed RPC codegen output"
```

---

## Task 4: Pre-commit drift hook

**Files:**
- Create: `scripts/git-hooks/pre-commit`
- Create: `scripts/install-hooks.sh`

- [ ] **Step 1: Create `scripts/git-hooks/pre-commit`**

```bash
mkdir -p scripts/git-hooks
```

Write `scripts/git-hooks/pre-commit`:

```bash
#!/usr/bin/env bash
# Drift-check hook for clamator RPC codegen output.
# Re-runs codegen and asserts that committed _generated/ paths match the source.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

# Run codegen quietly. If make is missing or codegen fails, surface the error.
if ! make -s codegen >/dev/null 2>&1; then
  echo "pre-commit: 'make codegen' failed. Run it manually for output." >&2
  exit 1
fi

if ! git diff --exit-code --quiet -- \
    packages/optio-api/src/_generated \
    packages/optio-core/src/optio_core/_generated; then
  echo "pre-commit: generated stubs drifted from contract source." >&2
  echo "Run 'make codegen', stage the result, and commit again." >&2
  exit 1
fi
```

```bash
chmod +x scripts/git-hooks/pre-commit
```

- [ ] **Step 2: Create `scripts/install-hooks.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
git config core.hooksPath scripts/git-hooks
echo "Hooks installed via core.hooksPath=scripts/git-hooks"
echo "Pre-commit will run 'make codegen' drift check."
```

```bash
chmod +x scripts/install-hooks.sh
```

- [ ] **Step 3: Install hooks for this clone**

```bash
bash scripts/install-hooks.sh
git config --get core.hooksPath
```
Expected output: `scripts/git-hooks`.

- [ ] **Step 4: Drift-rejection test — synthesize drift, attempt commit, expect rejection**

```bash
# Append a no-op comment to a generated file, stage it.
echo "// drift-test" >> packages/optio-api/src/_generated/engine.ts
git add packages/optio-api/src/_generated/engine.ts
git commit -m "drift test (should fail)" 2>&1 | tee /tmp/drift-attempt.log || true
```
Expected: commit rejected. Log contains `pre-commit: generated stubs drifted from contract source.`

- [ ] **Step 5: Restore generated file, confirm clean state**

```bash
git restore --staged packages/optio-api/src/_generated/engine.ts
git restore packages/optio-api/src/_generated/engine.ts
make codegen
git diff --exit-code -- packages/optio-api/src/_generated packages/optio-core/src/optio_core/_generated
```
Expected: empty diff (zero exit).

- [ ] **Step 6: Stage and commit hook scripts**

```bash
git add scripts/git-hooks/pre-commit scripts/install-hooks.sh
git commit -m "chore: add pre-commit codegen-drift hook + installer"
```
Expected: commit succeeds (the hook itself runs and passes — `_generated/` is in sync).

---

## Task 5: Doc updates and parent-spec corrections

**Files:**
- Modify: `README.md` (root)
- Modify: `AGENTS.md` (root)
- Modify: `packages/optio-contracts/AGENTS.md`
- Modify: `packages/optio-contracts/README.md`
- Modify: `docs/2026-05-08-engine-rpc-migration-design.md`

- [ ] **Step 1: Root `README.md` — Authority and data flow**

Find the existing Architecture section. After the architecture image, insert (verbatim from parent-spec Appendix A.1):

```markdown
### Authority and data flow

Optio enforces a clean separation between writes and reads:

- **`optio-core` (the engine) is the sole writer to MongoDB.** All state transitions, validation, scheduling, and policy decisions happen in the engine process. The engine is the single source of truth for what commands are allowed and what state results.
- **`optio-api` (the REST API) is read-only against MongoDB.** It serves REST GETs, SSE streams, the widget proxy, and instance discovery by reading directly from MongoDB and from redis heartbeat keys. It performs no DB writes.
- **Mutating operations (launch, cancel, dismiss, resync, group-cancel, launch blocks) flow from the API to the engine via clamator RPC over redis.** The API translates an HTTP request into a typed RPC call; the engine validates, acts, and returns a typed result; the API translates the result back into an HTTP response. The API enforces no state machine, no `cancellable` policy, no command-acceptance rules of its own.
- **External applications** that need to control the engine without going through HTTP can use the engine's Python API directly (in-process), or register as a clamator RPC client (cross-process). They never write to MongoDB themselves.
```

Also append (in the install / quickstart section, wherever fits) a one-liner pointing to `bash scripts/install-hooks.sh` for contributors.

- [ ] **Step 2: Root `AGENTS.md` — Architecture Notes**

In the Architecture Notes section: at the top of the bullet list, insert the Authority rule bullet. Find the existing Redis stream bullet, replace with the Engine RPC bullet. Adjust the "No Redis mode" bullet to mention `rpc_server`. Verbatim from parent-spec Appendix A.2:

```markdown
- **Authority rule.** `optio-core` is the sole writer to MongoDB. `optio-api` reads MongoDB directly for queries (REST GETs, SSE, widget proxy, discovery) and forwards every mutating operation to the engine via clamator RPC. The API enforces no state machine, no policy, no command-acceptance rules. Engine is single source of truth for what commands are allowed and what state results. Full statement: top-level README "Authority and data flow".
- **Engine RPC.** clamator over-redis. Engine hosts a `RedisRpcServer` constructed by `optio_core.init()` with `key_prefix=f"{database}/{prefix}"`, registering the `engine` service defined in `optio-contracts/src/engine-to-api.ts`. API uses a `RedisRpcClient` per `(database, prefix)` constructed by `registerOptioApi`. Apps can register additional services on `optio_core.rpc_server` before calling `optio_core.run()`.
- **Collection name**: `{prefix}_processes` (MongoDB)
- **No Redis mode**: `init()` with `redis_url=None` and no `rpc_server` disables the command surface; use direct Python API calls (`optio.launch()`, etc.) instead.
```

(Other Architecture Notes bullets — Progress flushing, Child processes, Ephemeral processes, Migrations, Scheduler, Process state reconciliation, Persistent launch blocks — remain unchanged.)

- [ ] **Step 3: `packages/optio-contracts/AGENTS.md` — Package structure section**

Insert this section between the existing `## Package` block and the existing `## Schemas` block (Q10 fix applied: schemas row points at `src/schemas/`):

```markdown
## Package structure

The package hosts two typed contracts that define optio's internal communication surfaces:

| File | Contract type | Purpose |
|------|---------------|---------|
| `src/api-to-frontend.ts` | ts-rest HTTP contract | What `optio-api` exposes to its REST clients (UI, external integrations). Used by `optio-ui` to construct typed clients and by `optio-api` to register typed handlers. |
| `src/engine-to-api.ts` | clamator RPC contract | What `optio-core` (the engine) exposes to its RPC callers (typically `optio-api`). Used by `optio-api` to issue typed RPC calls and by `optio-core` to implement typed handlers. |
| `src/schemas/` | Shared Zod schemas | Common types used by both contracts. `common.ts` holds generic primitives (ObjectId, Pagination, Error). `process.ts` holds process-domain types (Process, ProcessState, LogEntry, ProcessMetadataFilter). |

### Naming convention

Contract files follow `<server>-to-<client>.ts`, where the **server** is the side that exposes the contract and the **client** is the side that consumes it. For example, in `engine-to-api.ts`, the engine exposes methods that the API calls. The "to" indicates exposure, not call direction.

### Codegen

The clamator contract (`engine-to-api.ts`) is the single source of truth for the RPC surface. clamator's codegen produces matching wrappers in both languages:

- **TypeScript output:** `packages/optio-api/src/_generated/engine.ts` — `EngineClient` class, params/result types.
- **Python output:** `packages/optio-core/src/optio_core/_generated/engine.py` — Pydantic models, `EngineService` ABC.

Generated files are committed. Regenerate via `make codegen` at the repo root. A pre-commit hook runs codegen and fails on `git diff` non-empty under `_generated/` paths to catch drift.

The HTTP contract (`api-to-frontend.ts`) does not require codegen: ts-rest builds typed clients and handlers from the contract object via TypeScript's type system at the consumer's compile time.
```

- [ ] **Step 4: `packages/optio-contracts/README.md` — Contracts section**

Replace the current `## Contract` section (lines 28–43 in the present file) with (verbatim from parent-spec Appendix A.4):

```markdown
## Contracts

The package hosts two typed contracts. Contract files follow `<server>-to-<client>.ts` naming: the server side is the one that exposes the contract; the client side calls it.

- `processesContract` (in `src/api-to-frontend.ts`) — ts-rest HTTP contract that `optio-api` exposes to its REST clients.
- `engineContract` (in `src/engine-to-api.ts`) — clamator RPC contract that `optio-core` exposes to its RPC callers.

### `processesContract` (HTTP, ts-rest)

ts-rest router with 9 endpoints, used by `optio-ui` to call `optio-api`:

| Name | Method | Path |
|------|--------|------|
| `list` | GET | `/processes/:prefix` |
| `get` | GET | `/processes/:prefix/:id` |
| `getTree` | GET | `/processes/:prefix/:id/tree` |
| `getLog` | GET | `/processes/:prefix/:id/log` |
| `getTreeLog` | GET | `/processes/:prefix/:id/tree/log` |
| `launch` | POST | `/processes/:prefix/:id/launch` |
| `cancel` | POST | `/processes/:prefix/:id/cancel` |
| `dismiss` | POST | `/processes/:prefix/:id/dismiss` |
| `resync` | POST | `/processes/:prefix/resync` |

Used at runtime by ts-rest; no codegen step.

### `engineContract` (RPC, clamator)

clamator service named `engine`, used by `optio-api` to call `optio-core`. Methods:

| Method | Kind | Purpose |
|--------|------|---------|
| `launch` | request/reply | Launch a process; returns post-command process state or typed failure reason. |
| `cancel` | request/reply | Cancel a running or scheduled process; returns post-command state or typed failure reason. |
| `dismiss` | request/reply | Reset a terminal process to idle; returns post-command state or typed failure reason. |
| `groupCancel` | request/reply | Cancel all processes matching a metadata filter; returns count. |
| `groupCancelAndWait` | request/reply | Cancel all matching processes and wait until they reach a terminal state. |
| `blockLaunches` | request/reply | Add a persistent launch block. |
| `unblockLaunches` | request/reply | Remove a persistent launch block; returns count removed. |
| `resync` | notification | Re-sync task definitions. Fire-and-forget; no reply. |

Failure modes use discriminated-union result types (e.g. `{ ok: true, process } | { ok: false, reason: 'not-found' | 'not-launchable' | … }`) so consumers get exhaustive type coverage on success and failure branches.

Generated wrappers ship next to consumers: `packages/optio-api/src/_generated/engine.ts` for TypeScript, `packages/optio-core/src/optio_core/_generated/engine.py` for Python. Regenerate via `make codegen` at the repo root.
```

- [ ] **Step 5: Apply parent-spec corrections (decisions Q2, Q5, Q7, Q9, Q10)**

Edit `docs/2026-05-08-engine-rpc-migration-design.md` to fold phase-1 brainstorming decisions back into the authoritative end-state document. Phase-1 design doc retains the journey; parent spec captures the answers.

**Q10 — schemas/ subdir kept (path corrections):**
- §2 file layout: locate the `schemas.ts` row in the optio-contracts tree and replace it with two rows — `schemas/common.ts # generic primitives (ObjectId, Pagination, Error)` and `schemas/process.ts # process-domain types (Process, ProcessState, LogEntry, ProcessMetadataFilter)`.
- §3 Imports block: change `from './schemas.js'` to `from './schemas/process.js'`.
- §6 `packages/optio-contracts/AGENTS.md` row: change `src/schemas.ts` to `src/schemas/`.
- Appendix A.3 Package structure table: same `src/schemas/` fix as §6.

**Q9 — engineContract consumed only via subpath:**
- §3 — after the contract definition block, insert a one-line note:

  > `engineContract` is consumed via the `optio-contracts/engine-to-api` subpath export only. `packages/optio-contracts/src/index.ts` re-exports failure-reason enums (`LaunchFailureReason`, `CancelFailureReason`, `DismissFailureReason`, `GroupCancelFailureReason`, `BlockLaunchesFailureReason`) for direct import by consumers, but does not re-export `engineContract` itself.

**Q2 — failure-reason imports direct from `optio-contracts`:**
- §3 "What `api-to-frontend.ts` reuses": directly above the import block, add:

  > Within `optio-contracts`, `api-to-frontend.ts` imports failure-reason enums from `./engine-to-api.js`. External consumers (`optio-api/src/handlers.ts`, `optio-ui` error UI, custom adapters) import the same enums from the package root: `import { LaunchFailureReason } from 'optio-contracts'`. The package root re-exports the enum values and types, but not `engineContract` (Q9).

**Q5 — error-body shape change moves from phase 1 to phase 4:**
- §8 phase 1 "Deliverables": delete the bullet `Error response bodies extended to '{ reason, message }'.` from the `api-to-frontend.ts` line. Replace with: `Imports failure-reason enums from 'engine-to-api.ts' and re-exports them from 'index.ts'; HTTP error-body schema unchanged in phase 1 (flips in phase 4 alongside handler rewrite).`
- §8 phase 4 "Deliverables", `packages/optio-api/src/handlers.ts` block: add a new bullet `Update 'api-to-frontend.ts' error response schemas (404 / 409 bodies) to '{ reason, message }'. Either extend 'ErrorSchema' or introduce per-command error bodies (LaunchErrorBody, CancelErrorBody, DismissErrorBody) per §3.`
- §3 "What `api-to-frontend.ts` reuses": add a one-line note that the new `LaunchErrorBody` / `CancelErrorBody` / `DismissErrorBody` schemas land in phase 4, not phase 1.

**Q7 — CI deferred:**
- §6 "Pre-commit / CI": delete the line `CI step: 'make codegen && git diff --exit-code'. Phase 1.`. Replace with: `CI bootstrapping (running 'make lint && make test' and the codegen drift check on PRs) is out of scope for this migration. Tracked separately. The pre-commit hook is the sole drift guard until CI exists.`
- §10 "CI structure": prefix the section with: `Note: this section describes the target CI shape once CI infrastructure exists in the repo. CI bootstrapping is not part of this migration.` Bullet list otherwise unchanged.

- [ ] **Step 6: Acceptance verification — all green**

```bash
cd /home/csillag/deai/optio/.worktrees/redis-migration-1
make build
make test
make codegen && git diff --exit-code -- packages/optio-api/src/_generated packages/optio-core/src/optio_core/_generated
grep -rn "from .*'\\./contract'\\|contract\\.ts" packages/ --include='*.ts' --include='*.tsx' --include='*.json' --include='*.md'
```
Expected:
- `make build` exits zero.
- `make test` exits zero.
- `git diff` after `make codegen` is empty.
- `grep` returns nothing (any matches outside dist or this plan are stragglers; clean them up).

- [ ] **Step 7: Commit doc updates**

```bash
git add -A
git commit -m "docs: declare authority rule, document engine contract layout"
```

---

## Final verification

After Task 5, the branch should contain five commits on top of `csillag/rpc-migration-1`:

1. `refactor(optio-contracts): rename contract.ts to api-to-frontend.ts`
2. `feat(optio-contracts): add engine-to-api clamator contract`
3. `feat: add Makefile, clamator deps, and committed RPC codegen output`
4. `chore: add pre-commit codegen-drift hook + installer`
5. `docs: declare authority rule, document engine contract layout`

Run once more:

```bash
git log --oneline csillag/rpc-migration-1..HEAD
make build && make test
make codegen && git diff --exit-code -- packages/optio-api/src/_generated packages/optio-core/src/optio_core/_generated
```
Expected: five commits listed; all checks green.

Phase 1 complete. Phase 2 plan picks up from this branch state.
