# Documentation Housekeeping — Design Spec

## Goal

Restructure Optio's documentation after the monorepo reorganization and rename. The root README becomes an overview with images and diagrams; API reference content moves to per-package READMEs. AGENTS.md is updated to track the current state.

## Approach

Hybrid: rewrite the root README from scratch with the new structure; move existing API docs into per-package READMEs with light editing.

---

## Root README.md — New Structure

1. **Logo/banner image** — AI-generated Roman centurion in armor, standing on a hill, pointing with sword, directing underlings. Stored at `docs/images/banner.png`.
2. **Heading + name explanation** — "Optio" heading followed by:
   > *In the Roman army, the **optio** was the centurion's second-in-command — responsible for scheduling daily routines, managing operations, and ensuring everything ran smoothly behind the scenes. This library serves the same role for your application: scheduling and managing background processes, tracking their lifecycle, and keeping everything under control.*
3. **Overview** — What Optio is, the progressive stack philosophy (start simple, add complexity as needed).
4. **Key Concepts** — State machine, task definitions (async callback pattern), ProcessContext, child processes (sequential and parallel), cooperative cancellation.
5. **Features** — Bullet list of capabilities.
6. **Architecture diagram** — Programmatically generated (Mermaid rendered to SVG), showing the 4-layer stack. Stored at `docs/images/architecture.svg`.
7. **Deployment Levels** — The 4 integration levels, each with a diagram:
   - **Level 1:** Python core + MongoDB
   - **Level 2:** + Redis for multi-worker command ingestion
   - **Level 3:** + REST API (TypeScript)
   - **Level 4:** + React UI — includes a UI screenshot (placeholder for now)
   - Deployment diagrams stored at `docs/images/level-{1,2,3,4}.svg`.
8. **Quick Start** — Minimal code example to get running.
9. **Packages** — Brief description of each package + link to its README.
10. **License** (if applicable).

All API reference material (Python API, REST endpoints, React components/hooks) is removed from the root README.

---

## Per-Package READMEs

### packages/optio-core/README.md

Brief intro, then Python API reference moved from the root README. Covers:
- `TaskInstance` model
- `Optio` lifecycle (`init`, `run`, `shutdown`)
- `ProcessContext` interface (progress, cancellation, child processes)
- State machine and transitions
- Cron scheduling
- Redis integration (optional)
- `OptioConfig` options

### packages/optio-contracts/README.md

Brief intro clearly stating that **these contracts are an implementation detail** for communication between optio-ui and optio-api. Users only need to care about this package if they want to build an alternative frontend.

Content: schema definitions, ts-rest contract, all 9 endpoints, exported types.

### packages/optio-api/README.md

Brief intro, then REST API reference moved from the root README. Covers:
- Handler functions
- SSE streams (flat list and tree)
- Fastify adapter
- Publisher utilities (`publishLaunch`, `publishResync`)

### packages/optio-ui/README.md

Brief intro, then React component/hook reference moved from the root README. Includes the same UI screenshot as the root README Level 4 section (placeholder for now). Covers:
- `OptioProvider` setup
- Components (`ProcessList`, `ProcessItem`, `ProcessTreeView`, `ProcessLogPanel`, `ProcessFilters`, `ProcessStatusBadge`)
- Hooks (`useProcessActions`, `useProcessQueries`, `useProcessStream`, `useProcessListStream`)
- i18n support

---

## AGENTS.md Update

Verify and fix all references (package names, file paths, module names) to match the current monorepo structure. No structural changes — just ensure accuracy post-reorg and rename.

---

## Images

| Image | Format | Location | Generation |
|-------|--------|----------|------------|
| Logo/banner | PNG | `docs/images/banner.png` | AI-generated (Roman centurion scene) |
| Architecture diagram | SVG | `docs/images/architecture.svg` | Mermaid → SVG |
| Deployment level 1-4 | SVG | `docs/images/level-{1,2,3,4}.svg` | Mermaid → SVG |
| UI screenshot | PNG | `docs/images/ui-screenshot.png` | Captured from running instance (deferred) |

All images referenced via relative paths so they render inline on GitHub.

---

## Out of Scope

- UI screenshot capture (scheduled for later)
- Changes to source code
- Changes to package.json or build configuration
