# optio-agents-ui Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `optio-agents-ui`, a published TS package exposing canonical agent metadata (`AgentType`, `AgentInfo`, `AGENTS`, `getAgentInfo`) generated from the Python `optio_agents_all.AGENTS` source of truth, and dedup the TS engine-slug union onto it.

**Architecture:** New TS package under `packages/optio-agents-ui`, external to core (no `optio-contracts`/core dependency). Its data module `src/agents.generated.ts` is emitted by `scripts/generate.py` (imports Python `AGENTS`) and committed; a hand-written `src/agent-info.ts` holds the `AgentInfo` interface + `getAgentInfo`. Hooked into `make codegen`. `optio-conversation-ui` consumes it, replacing its duplicated `SpinnerEngine` literal union with the shared `AgentType`.

**Tech Stack:** TypeScript (ESM, `tsc`-built lib), vitest, pnpm workspace, Python 3 (repo `.venv`) for the generator, Make.

## Global Constraints

- Package name: `optio-agents-ui` (unscoped, matches repo TS naming). Version `0.1.0`.
- External to core: `optio-agents-ui` must NOT depend on `optio-contracts` or any core package.
- Python `optio_agents_all.AGENTS` is the single source of truth. `src/agents.generated.ts` is GENERATED + committed; never hand-edit it.
- Canonical data (7 engines, sorted): antigravity=`Antigravity CLI`/`https://antigravity.google`; claudecode=`Claude Code`/`https://claude.com/product/claude-code`; codex=`Codex`/`https://openai.com/codex`; cursor=`Cursor CLI`/`https://cursor.com/cli`; grok=`Grok Build`/`https://x.ai/cli`; kimicode=`Kimi Code`/`https://www.kimi.com/coding`; opencode=`OpenCode`/`https://opencode.ai`.
- Build follows the `optio-contracts` pattern: ships compiled `dist` (`main: dist/index.js`, `files: ["dist"]`), `tsc` build, `tsconfig` extends `../../tsconfig.base.json`.
- This work is sequential (package → codegen wiring → consumer → verify); defer full-suite verification to the last task. Do NOT force parallelism.
- Do not use `npx`; use `pnpm exec` / `node_modules/.bin/tsc`. Do not publish — release is a separate user-gated step after this plan.

---

### Task 1: Scaffold `optio-agents-ui` + generator + generated data

**Files:**
- Create: `packages/optio-agents-ui/package.json`
- Create: `packages/optio-agents-ui/tsconfig.json`
- Create: `packages/optio-agents-ui/README.md`
- Create: `packages/optio-agents-ui/LICENSE` (copy `packages/optio-contracts/LICENSE`)
- Create: `packages/optio-agents-ui/scripts/generate.py`
- Create: `packages/optio-agents-ui/src/agent-info.ts`
- Create: `packages/optio-agents-ui/src/index.ts`
- Generated: `packages/optio-agents-ui/src/agents.generated.ts` (produced by the generator)
- Test: `packages/optio-agents-ui/src/__tests__/agents.test.ts`

**Interfaces:**
- Produces: `import { AGENTS, getAgentInfo, type AgentType, type AgentInfo } from 'optio-agents-ui'`.
  - `type AgentType = 'antigravity' | 'claudecode' | 'codex' | 'cursor' | 'grok' | 'kimicode' | 'opencode'`
  - `interface AgentInfo { slug: AgentType; name: string; url: string }`
  - `const AGENTS: Record<AgentType, AgentInfo>`
  - `function getAgentInfo(slug: AgentType): AgentInfo`

- [ ] **Step 1: Write `package.json`**

```json
{
  "name": "optio-agents-ui",
  "version": "0.1.0",
  "license": "Apache-2.0",
  "description": "Canonical agent metadata (slug, name, URL) for optio agent engines — TS catalog generated from the Python source of truth.",
  "repository": {
    "type": "git",
    "url": "git+https://github.com/deai-network/optio.git",
    "directory": "packages/optio-agents-ui"
  },
  "homepage": "https://github.com/deai-network/optio/tree/main/packages/optio-agents-ui#readme",
  "bugs": { "url": "https://github.com/deai-network/optio/issues" },
  "author": "Kristof Csillag <kristof.csillag@deai-labs.com>",
  "type": "module",
  "files": ["dist", "README.md", "LICENSE"],
  "main": "dist/index.js",
  "types": "dist/index.d.ts",
  "exports": {
    ".": { "import": "./dist/index.js", "types": "./dist/index.d.ts" }
  },
  "scripts": {
    "build": "tsc",
    "dev": "tsc --watch",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "devDependencies": {
    "typescript": "^5.7.0",
    "vitest": "^3.0.0"
  }
}
```

- [ ] **Step 2: Write `tsconfig.json`**

```json
{
  "extends": "../../tsconfig.base.json",
  "compilerOptions": {
    "outDir": "dist",
    "rootDir": "src"
  },
  "include": ["src"],
  "exclude": ["src/**/*.test.ts", "src/**/__tests__/**"]
}
```

- [ ] **Step 3: Write `scripts/generate.py`**

```python
#!/usr/bin/env python3
"""Generate optio-agents-ui/src/agents.generated.ts from the Python AGENTS SSOT.

Run via `make codegen`. The output is committed; never edit it by hand.
"""
from __future__ import annotations

import json
from pathlib import Path

from optio_agents_all import AGENTS

OUT = Path(__file__).resolve().parent.parent / "src" / "agents.generated.ts"

HEADER = (
    "// AUTO-GENERATED by scripts/generate.py from optio_agents_all.AGENTS. DO NOT EDIT.\n"
    "// Regenerate with `make codegen`.\n\n"
)


def main() -> None:
    slugs = sorted(AGENTS.keys())
    union = " | ".join(f"'{s}'" for s in slugs)
    parts = [HEADER]
    parts.append("import type { AgentInfo } from './agent-info.js';\n\n")
    parts.append(f"export type AgentType = {union};\n\n")
    parts.append("export const AGENTS: Record<AgentType, AgentInfo> = {\n")
    for s in slugs:
        info = AGENTS[s]
        parts.append(
            f"  {s}: {{ slug: '{s}', name: {json.dumps(info.name)}, url: {json.dumps(info.url)} }},\n"
        )
    parts.append("};\n")
    OUT.write_text("".join(parts))
    print(f"wrote {OUT} ({len(slugs)} engines)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Write `src/agent-info.ts` (hand-written)**

```ts
import { AGENTS, type AgentType } from './agents.generated.js';

/** Canonical, user-facing metadata for an agent engine. */
export interface AgentInfo {
  slug: AgentType;
  name: string;
  url: string;
}

/** Canonical metadata for the given engine slug. */
export function getAgentInfo(slug: AgentType): AgentInfo {
  return AGENTS[slug];
}
```

- [ ] **Step 5: Write `src/index.ts`**

```ts
export type { AgentType } from './agents.generated.js';
export { AGENTS } from './agents.generated.js';
export { getAgentInfo, type AgentInfo } from './agent-info.js';
```

- [ ] **Step 6: Write `README.md`**

```markdown
# optio-agents-ui

Canonical agent metadata (slug, name, URL) for optio agent engines, for TypeScript
consumers. The data in `src/agents.generated.ts` is **generated** from the Python
source of truth (`optio_agents_all.AGENTS`) via `make codegen` — do not edit it by
hand.

```ts
import { AGENTS, getAgentInfo } from 'optio-agents-ui';

getAgentInfo('claudecode').name; // "Claude Code"
getAgentInfo('grok').url;        // "https://x.ai/cli"
```
```

- [ ] **Step 7: Run the generator to produce `agents.generated.ts`**

Run: `.venv/bin/python packages/optio-agents-ui/scripts/generate.py`
Expected: prints `wrote .../agents.generated.ts (7 engines)`. The file now contains the header, `AgentType` union, and `AGENTS` const. Verify it matches the canonical data (7 entries, sorted antigravity→opencode).

- [ ] **Step 8: Write the failing test**

```ts
// packages/optio-agents-ui/src/__tests__/agents.test.ts
import { describe, it, expect } from 'vitest';
import { AGENTS, getAgentInfo, type AgentType } from '../index.js';

const EXPECTED: Record<AgentType, { name: string; url: string }> = {
  antigravity: { name: 'Antigravity CLI', url: 'https://antigravity.google' },
  claudecode: { name: 'Claude Code', url: 'https://claude.com/product/claude-code' },
  codex: { name: 'Codex', url: 'https://openai.com/codex' },
  cursor: { name: 'Cursor CLI', url: 'https://cursor.com/cli' },
  grok: { name: 'Grok Build', url: 'https://x.ai/cli' },
  kimicode: { name: 'Kimi Code', url: 'https://www.kimi.com/coding' },
  opencode: { name: 'OpenCode', url: 'https://opencode.ai' },
};

describe('AGENTS catalog', () => {
  it('has exactly the 7 canonical engines', () => {
    expect(Object.keys(AGENTS).sort()).toEqual(Object.keys(EXPECTED).sort());
  });

  it('exposes the canonical name and url for each engine', () => {
    for (const slug of Object.keys(EXPECTED) as AgentType[]) {
      expect(getAgentInfo(slug)).toEqual({ slug, ...EXPECTED[slug] });
    }
  });
});
```

- [ ] **Step 9: Install workspace deps + run the test**

Run: `pnpm install` (registers the new workspace package), then
`pnpm --filter optio-agents-ui test`
Expected: PASS (2 tests). If `pnpm install` reports the package added, that is expected.

- [ ] **Step 10: Typecheck-build the package**

Run: `pnpm --filter optio-agents-ui build`
Expected: `tsc` exits 0; `dist/index.js`, `dist/index.d.ts`, `dist/agents.generated.js`, `dist/agent-info.js` produced.

- [ ] **Step 11: Commit**

```bash
git add packages/optio-agents-ui pnpm-lock.yaml
git commit -m "feat(optio-agents-ui): TS agent-metadata catalog generated from Python AGENTS"
```

---

### Task 2: Hook the generator into `make codegen` + `clean-codegen`

**Files:**
- Modify: `Makefile` (the `codegen:` target ~line 69; the `clean-codegen:` target ~line 124)

**Interfaces:**
- Consumes: `packages/optio-agents-ui/scripts/generate.py` (Task 1), the repo `.venv` Python (`$(PY)`).

- [ ] **Step 1: Append the emitter to the `codegen:` target**

After the existing `clamator-codegen` block and its Python-rename post-process, add (use the Makefile's existing `$(PY)` venv-python variable, matching other Python invocations):

```make
	@# Agent metadata: emit the TS catalog from the Python AGENTS source of truth.
	$(PY) packages/optio-agents-ui/scripts/generate.py
```

- [ ] **Step 2: Extend `clean-codegen:` to drop the generated file**

Add to the `clean-codegen:` recipe:

```make
	rm -f packages/optio-agents-ui/src/agents.generated.ts
```

- [ ] **Step 3: Verify regeneration is byte-stable (codegen-clean guard)**

Run:
```bash
make codegen
git diff --exit-code packages/optio-agents-ui/src/agents.generated.ts
```
Expected: `make codegen` regenerates the file; `git diff --exit-code` exits 0 (no diff — deterministic output). If it exits non-zero, the generator is non-deterministic — fix ordering/formatting before proceeding.

- [ ] **Step 4: Commit**

```bash
git add Makefile
git commit -m "build(codegen): generate optio-agents-ui agent catalog from Python AGENTS"
```

---

### Task 3: Consume in `optio-conversation-ui` (dedup the slug union)

**Files:**
- Modify: `packages/optio-conversation-ui/package.json` (add dependency)
- Modify: `packages/optio-conversation-ui/src/spinners/NativeSpinner.tsx:17-18` (replace `SpinnerEngine` literal union)
- Test: existing `optio-conversation-ui` vitest suite (no new test required)

**Interfaces:**
- Consumes: `import type { AgentType } from 'optio-agents-ui'` (Task 1).

- [ ] **Step 1: Add the workspace dependency**

In `packages/optio-conversation-ui/package.json`, add to `"dependencies"` (alongside `"optio-ui": "workspace:*"`):

```json
    "optio-agents-ui": "workspace:*",
```

Run: `pnpm install`
Expected: dependency linked; lockfile updated.

- [ ] **Step 2: Replace the duplicated `SpinnerEngine` literal union**

In `packages/optio-conversation-ui/src/spinners/NativeSpinner.tsx`, replace the hand-maintained union:

```ts
export type SpinnerEngine =
  | 'claudecode' | 'opencode' | 'grok' | 'codex' | 'cursor' | 'kimicode' | 'antigravity';
```

with a re-export of the shared type (add the import near the top, after the existing `import type { CSSProperties } from 'react';`):

```ts
import type { AgentType } from 'optio-agents-ui';

// The engine set is owned by optio-agents-ui (generated from the Python SSOT).
export type SpinnerEngine = AgentType;
```

Leave `BUILDERS: Record<SpinnerEngine, …>` and the `NativeSpinner` component unchanged — `SpinnerEngine` is now an alias of `AgentType`, so exhaustiveness of the `BUILDERS` map is still enforced against the same 7 engines. (Scope note: `ConversationWidget.tsx`'s runtime `protocol` string-dispatch is intentionally left as-is — the wire value is untyped `z.unknown()`, so casting it to `AgentType` would be unsound; the real duplication was the `SpinnerEngine` literal list, now removed.)

- [ ] **Step 3: Typecheck the consumer**

Run: `pnpm --filter optio-conversation-ui build`
Expected: `tsc` exits 0 (the `Record<AgentType, …>` still resolves all 7 engines; no missing/extra key errors).

- [ ] **Step 4: Run the consumer test suite**

Run: `pnpm --filter optio-conversation-ui test`
Expected: PASS (same green counts as before — no behavior change).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-conversation-ui/package.json \
        packages/optio-conversation-ui/src/spinners/NativeSpinner.tsx \
        pnpm-lock.yaml
git commit -m "refactor(optio-conversation-ui): source engine union from optio-agents-ui"
```

---

### Task 4: Release wiring + full verification

**Files:**
- Modify: `Makefile:155` (`RELEASABLE_TS`)
- Modify: `scripts/release/run.py` (`TS_PUBLISHABLE` list)

**Interfaces:** none (registration + verification only).

- [ ] **Step 1: Register `optio-agents-ui` as releasable**

In `Makefile`, add `optio-agents-ui` to `RELEASABLE_TS` (place it before `optio-conversation-ui`, its dependent):

```make
RELEASABLE_TS      := filtrum-core filtrum-mongo optio-agents-ui optio-ui optio-api optio-conversation-ui optio-dashboard
```

In `scripts/release/run.py`, add `"optio-agents-ui"` to `TS_PUBLISHABLE` (before `"optio-conversation-ui"`):

```python
TS_PUBLISHABLE = ["filtrum-core", "filtrum-mongo", "optio-agents-ui", "optio-ui", "optio-api", "optio-conversation-ui", "optio-dashboard"]
```

- [ ] **Step 2: Full-suite verification**

Run: `make test`
Expected: PASS across TS + Python, including the new `optio-agents-ui` tests and the unchanged `optio-conversation-ui` suite. (If the known unrelated `optio-core` cancel flake trips, re-run; it is not caused by this change.)

- [ ] **Step 3: Codegen-clean re-check**

Run:
```bash
make codegen && git diff --exit-code
```
Expected: exit 0 — no uncommitted diff after regeneration (generated catalog is stable and committed).

- [ ] **Step 4: Commit**

```bash
git add Makefile scripts/release/run.py
git commit -m "build(release): register optio-agents-ui as a publishable TS package"
```

---

## Self-Review

**Spec coverage:**
- New package `optio-agents-ui`, external to core (spec §A) → Task 1. ✓
- Public API `AgentType`/`AgentInfo`/`AGENTS`/`getAgentInfo` (spec §A) → Task 1 Steps 3-5. ✓
- Python→TS generation, committed, deterministic (spec §B) → Task 1 Step 7 + Task 2. ✓
- Hook into `make codegen` + `clean-codegen` (spec §B) → Task 2. ✓
- Consumer dedup: conversation-ui dep + `SpinnerEngine`→`AgentType` (spec §C, scope 1+3) → Task 3. ✓
- No new UI text (spec §C) → honored (Task 3 touches only the type). ✓
- Release wiring: `RELEASABLE_TS` + `TS_PUBLISHABLE`, order before conversation-ui (spec §D) → Task 4. ✓
- Testing: generation stability, catalog snapshot, consumer typecheck, full `make test` (spec Testing) → Task 1 Step 8, Task 2 Step 3, Task 3 Steps 3-4, Task 4. ✓

**Placeholder scan:** No TBD/TODO. All code blocks are complete. The generated `agents.generated.ts` content is not transcribed (it is machine-emitted in Task 1 Step 7 and verified against the canonical data table).

**Type consistency:** `AgentType`/`AgentInfo`/`AGENTS`/`getAgentInfo` names and signatures are identical across Task 1 (produced), Task 3 (consumed via `AgentType`), and the tests. `SpinnerEngine = AgentType` alias preserves the existing `BUILDERS: Record<SpinnerEngine, …>` usage.

## Notes for execution

- Sequential: Task 1 → 2 → 3 → 4. No worktree needed; work in-place on a feature branch.
- The generator needs `optio_agents_all` importable in the `.venv` (already installed/published).
- Publishing is NOT part of this plan — after merge, release `optio-agents-ui` (new, `0.1.0`) then bump+release `optio-conversation-ui` (its dependency changed), per the standard user-gated release flow.
