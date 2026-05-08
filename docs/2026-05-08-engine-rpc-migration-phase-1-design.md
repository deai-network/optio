# 2026-05-08 — Engine RPC migration, phase 1 design

**Status:** Design.
**Parent spec:** `docs/2026-05-08-engine-rpc-migration-design.md`.
**Branch:** `redis-migration-1` (in worktree `.worktrees/redis-migration-1`, based on `csillag/rpc-migration-1`).

This document supplements the parent spec by recording the decisions that resolve phase-1 open questions and by fixing the commit sequence. Everything not addressed here defers to the parent spec.

## 1. Scope

Phase 1 ships the new contract surface and supporting tooling. **No runtime behavior change.** Engine and API still talk via the legacy `${prefix}:commands` redis stream after phase 1 merges.

### What ships

- New `packages/optio-contracts/src/engine-to-api.ts` — full clamator engine contract per parent spec §3.
- `packages/optio-contracts/src/contract.ts` → `api-to-frontend.ts` (rename + import sweep).
- New subpath export `optio-contracts/engine-to-api`.
- Committed codegen output:
  - `packages/optio-api/src/_generated/engine.ts`
  - `packages/optio-core/src/optio_core/_generated/engine.py`
- Top-level `Makefile` per parent spec §7, minus the `test-interop` target (lands phase 2).
- Clamator runtime dependencies in `optio-api` and `optio-core`. `@clamator/codegen` as root devDep.
- Pre-commit hook installed via `git config core.hooksPath scripts/git-hooks`.
- Doc updates (root `README.md`, root `AGENTS.md`, `packages/optio-contracts/AGENTS.md`, `packages/optio-contracts/README.md`) per parent-spec Appendix A.

### What does not ship

- Engine `EngineService` implementation, `lifecycle.py` `rpc_server` wiring (phase 2).
- API `engine-cache.ts`, adapter changes, `EngineClient` consumption (phase 2).
- HTTP path migration (phase 3).
- API authority-code deletion + `ErrorSchema` shape change to `{ reason, message }` (phase 4).
- Legacy stream removal (phase 5).
- CI workflow.
- Codegen interop test.
- Any restructuring of `packages/optio-contracts/src/schemas/`.

## 2. Phase-1 decisions

| # | Open question | Decision |
|---|---------------|----------|
| 1 | Pre-commit hook delivery | Bash script under `scripts/`. |
| 2 | Failure-reason type imports | Direct from `optio-contracts` (root re-exports from `engine-to-api.ts`). |
| 3 | Codegen interop test | Skipped in phase 1. Phase 2 interop suite covers wire shape. |
| 4 | Commit shape | Sequenced commits, one per concern (see §3). |
| 5 | Error-body shape change timing | `ErrorSchema` stays `{ message }` in phase 1. Schema flip + handler flip happen atomically in phase 4. |
| 6 | Hook install mechanism | `git config core.hooksPath scripts/git-hooks`. No symlink, no copy. |
| 7 | CI in phase 1 | None. Pre-commit hook is the sole drift guard. |
| 8 | Dependency placement | `@clamator/codegen` as root devDep. Clamator runtime deps land in `optio-api` (TS) and `optio-core` (Python) in phase 1, even though phase 1 doesn't construct clients/servers — generated files import them. |
| 9 | Subpath export | Yes. `optio-contracts/engine-to-api` is the canonical entry point for `engineContract`. Index re-exports failure-reason enums only. |
| 10 | Schemas layout | Keep current `schemas/{common,process}.ts` subdir. Edit parent-spec import-path examples accordingly (see §5). |

## 3. Commit sequence

Five commits. Each leaves the tree green.

1. **Rename HTTP contract.** `packages/optio-contracts/src/contract.ts` → `api-to-frontend.ts`. Update `optio-contracts/src/index.ts` re-export source. Inline edit `packages/optio-contracts/AGENTS.md` line 158 path reference. No importer changes (consumers go through package root).

2. **Add engine RPC contract.** Add `@clamator/protocol` runtime dep to `optio-contracts`. Create `src/engine-to-api.ts` per parent spec §3 (with Q10 import-path fix). Add `./engine-to-api` subpath export to `optio-contracts/package.json`. Re-export failure-reason enums from `optio-contracts/src/index.ts`. No consumers yet.

3. **Codegen tooling + clamator runtime deps + generated output.** Add root `@clamator/codegen` devDep. Add `@clamator/protocol` + `@clamator/over-redis` to `optio-api/package.json`. Add `clamator-protocol` + `clamator-over-redis` to `optio-core/pyproject.toml`. Add top-level `Makefile`. Run `make codegen`; commit `_generated/` outputs. Verify idempotency by running codegen twice in a row and confirming no diff.

4. **Pre-commit hook.** Add `scripts/git-hooks/pre-commit` (runs `make codegen` then asserts no diff under `_generated/` paths). Add `scripts/install-hooks.sh` (one-line `git config core.hooksPath scripts/git-hooks`). Document the install snippet in root README install instructions.

5. **Doc updates.** Apply parent-spec Appendix A.1 to root `README.md`. Apply A.2 to root `AGENTS.md`. Apply A.3 to `packages/optio-contracts/AGENTS.md` (Q10 fix: rows reference `src/schemas/` not `src/schemas.ts`). Apply A.4 to `packages/optio-contracts/README.md`.

## 4. Acceptance

- `make build` green across all TS and Python packages.
- `make test` green.
- `make codegen` is deterministic (run twice, second run produces no diff).
- After `bash scripts/install-hooks.sh`, attempting a commit while `_generated/` is stale fails the hook with the documented error message.
- `grep -rn "from.*'\\./contract'\\|contract\\.ts" packages/` returns zero results outside `dist/` and parent-spec doc references.
- Root `README.md` contains the Authority and data flow section verbatim from parent-spec Appendix A.1.

## 5. Parent-spec corrections (applied during phase 1)

These corrections are mechanical edits to the parent spec to reflect Q10 (keep `schemas/` subdir):

- §2 file layout: replace the `schemas.ts` row with two rows — `schemas/common.ts` and `schemas/process.ts` — describing what each holds.
- §3 import example: change `from './schemas.js'` to `from './schemas/process.js'` for `ProcessSchema`/`ProcessMetadataFilterSchema`.
- §6 `packages/optio-contracts/AGENTS.md` row: change `src/schemas.ts` to `src/schemas/`.
- Appendix A.3 table: same fix as §6.

The parent spec is the authoritative end-state document, so its corrections ship in commit 5 alongside the new doc additions.

## 6. Out of scope

- Anything not listed in §1 "What ships" — including everything from parent-spec phases 2 through 5.
- Bootstrapping CI infrastructure.
- Restructuring `optio-contracts/src/schemas/`.
- Moving toward TS-side codegen for ts-rest contracts (the HTTP contract continues to use ts-rest's compile-time type derivation; only the RPC contract is codegenned).

## 7. Risks

- **Codegen scanner imports every `*.ts` in src dir.** `api-to-frontend.ts` and `index.ts` get loaded too. Side-effect-free imports, so scanner-time failure unlikely; but worth flagging if a future contributor adds top-level work in those files.
- **Idempotency of generated output.** If `make codegen` is non-deterministic, the pre-commit hook will fire after every routine commit. Phase 1 commit 3 verifies idempotency before committing the hook. Mitigation: if non-determinism observed, file an upstream clamator issue and either pin the version or weaken the hook.
- **Subpath export interaction with TypeScript's resolution.** `optio-contracts/engine-to-api` requires `tsconfig.base.json` to allow subpath imports for downstream packages. If consumers can't resolve the subpath, fall back to allowing `engineContract` re-export from the root — but only if forced.
