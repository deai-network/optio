# Universal Claustrum — Design

**Date:** 2026-07-08
**Status:** Approved (brainstormed), pending implementation plan.

## Goal

Make **claustrum** (the Landlock, fail-closed filesystem sandbox in
`optio_agents.claustrum`) the filesystem-isolation guarantee for **all 7 agent
wrappers** — claudecode, cursor, kimicode, antigravity, grok, codex, opencode.
Today only 4 wrap the agent in claustrum; grok and codex rely on vendor-native
sandboxes and opencode has none (its `fs_isolation` field is inert). Vendor
sandbox implementations are **not trusted** for filesystem confinement.

Along the way, pay down the duplication this exposes: the claustrum config
fields and the wrap/notify plumbing are copy-pasted per engine. Lift the
genuinely-identical pieces into the shared `optio_agents` package.

## Trust model

Claustrum is the **single trusted fs-isolation layer** on every engine,
fail-closed: if the kernel cannot apply the Landlock sandbox, the task **refuses
to launch** rather than run unconfined. A vendor's native sandbox is retained
**only** for a capability Landlock physically cannot provide — never for fs
isolation.

## Current state (verified 2026-07-08)

- **claustrum module (`ensure_claustrum_installed`) used by 4 engines:**
  claudecode, cursor, kimicode, antigravity.
- **native sandbox, no claustrum:** grok (`--sandbox optio`, an optio-planted
  custom Landlock profile) and codex (native `workspace-write`).
- **no sandbox:** opencode — `fs_isolation`/`extra_allowed_dirs` are present but
  **inert** (a launch-time warning only; the Spec A placeholder).
- **`fs_isolation` + `extra_allowed_dirs`** are duplicated verbatim in all 7
  `types.py`. Shared `config_types.py` holds only `AllowedDir` /
  `SeedUnavailableError`.
- **`delivery_type`** exists **only** on claudecode. It names a subdir under
  `<workdir>/deliverables/` used to route the "a newer claustrum release is
  available" notice through `on_deliverable`. Mandatory when `fs_isolation` is
  on (claudecode raises otherwise).
- **The "newer claustrum available" notice** is implemented **only** in
  claudecode's session body. cursor/kimicode/antigravity provision claustrum but
  **never surface the update notice** — a silent security-relevant gap.
- **`_build_claustrum_wrap`** (the argv-prefix builder that Landlock-confines the
  process tree) is **duplicated** across claudecode, cursor, kimicode,
  antigravity ("ported from claudecode's").
- **`on_deliverable` exists on all 7** configs — the notice can route everywhere.

## Design

### Shared surface (SSOT in `optio_agents`)

1. **`ClaustrumConfigMixin`** — a frozen dataclass in
   `optio_agents/config_types.py` holding the triad:

   ```python
   @dataclass(frozen=True)
   class ClaustrumConfigMixin:
       fs_isolation: bool = True
       extra_allowed_dirs: list[AllowedDir] | None = None
       delivery_type: str | None = None
   ```

   Each engine `TaskConfig` **inherits** it. Fields stay top-level, so callers
   still write `fs_isolation=False` / `delivery_type="…"` verbatim — **zero
   churn** at construction sites (demo, Excavator, all consumers). All fields are
   defaulted, so dataclass field-ordering / MRO is safe.

   The mixin carries the **shared validation** (run from each config's
   `__post_init__`, which calls `super().__post_init__()` or an explicit
   `_validate_claustrum(self)` helper):
   - **`delivery_type` is MANDATORY when `fs_isolation` is on.** Claustrum is a
     security feature; a new release may be patching a vulnerability, so the
     operator must be notified as soon as possible. The friction is intentional.
     Raise a clear `ValueError` when `fs_isolation and not delivery_type`.
   - each `extra_allowed_dirs` entry's `mode` is one of `ro/rw/rox/rwx`
     (already enforced by `AllowedDir`, re-checked here for the collection).

2. **Hoist `_build_claustrum_wrap` into `optio_agents.claustrum`.** One shared
   implementation replaces the 4 copies. The **per-engine baseline grant set**
   (the engine's own config/cache/temp dirs that must be readable/writable) is
   passed **as a parameter** — the wrap *mechanics* are shared; the *grant data*
   stays per-engine. Signature (final names fixed in the plan):

   ```python
   async def build_claustrum_wrap(
       host, *, claustrum_path, workdir,
       baseline_grants: list[AllowedDir],
       extra_allowed_dirs: list[AllowedDir] | None,
   ) -> list[str]: ...
   ```

3. **`emit_claustrum_update_notice`** — factor claudecode's notice block into a
   shared coroutine in `optio_agents.claustrum`:

   ```python
   async def emit_claustrum_update_notice(
       host, hook_ctx, *, delivery_type, on_deliverable, newer, pinned,
   ) -> None: ...
   ```

   Writes `deliverables/<delivery_type>/claustrum-update-<newer>.md`, invokes
   `on_deliverable`, then removes the notice file (clean slate for the real
   agent). No-ops when `on_deliverable is None` or `newer is None`. All 7 call it
   from their session body when `fs_isolation` and a newer tag is detected.

### Per-engine changes

| engine | claustrum today | change |
|---|---|---|
| claudecode | yes | refactor onto shared builder + shared notice + mixin |
| cursor | yes | **add notice**; shared builder + mixin |
| kimicode | yes | **add notice**; shared builder + mixin |
| antigravity | yes | **add notice**; shared builder + mixin |
| grok | no (native `--sandbox optio` custom profile) | **rip the custom profile + its planting machinery**; add claustrum provision + wrap + notice; mixin (gains `delivery_type`) |
| codex | no (native `workspace-write`) | add claustrum provision + wrap + notice; **rework sandbox semantics** (see below); mixin |
| opencode | no (inert) | wire claustrum provision + wrap + notice; **remove the inert warning**; mixin; add opencode's config/cache dirs to its baseline grants |

### codex network wrinkle (decided)

codex's `network_access` is a **sub-knob of `workspace-write`** — codex only
exposes network confinement *inside* its own fs sandbox mode; you cannot get
codex network confinement without also turning on codex's native fs sandbox.

Decision: keep codex's native sandbox **solely for the network knob**. When
network confinement is desired, codex runs **claustrum (trusted fs) + native
`workspace-write` (carries the network knob; its fs restriction is a harmless
redundant layer)**. Claustrum remains the guarantee regardless.

- `fs_isolation` → **always** claustrum-wraps codex (fail-closed), decoupled
  from the native `sandbox` value.
- `sandbox` / `network_access` are retained **only** to govern network.
- The old cross-validation (`fs_isolation` incompatible with
  `sandbox='danger-full-access'`, and `fs_isolation=False` incompatible with
  `read-only`/`workspace-write`) is **reworked**: `fs_isolation` no longer
  constrains the native sandbox mode, because claustrum — not the native mode —
  owns fs isolation now. Network-only validation remains (e.g. `network_access`
  still requires a mode that supports it).

### grok custom-profile removal

grok's `--sandbox optio` planted a custom Landlock profile via grok's own
`fs_allowlist.py` machinery. Claustrum fully subsumes it (both are Landlock,
fs-only). **Remove** the custom-profile planting path and launch grok under the
shared claustrum wrap instead. Net simplification.

### opencode wiring

opencode runs `opencode web` (a server) as the process tree. Wrap that tree in
claustrum. Landlock does not restrict the network, so the web server still binds
localhost — **must be re-verified live**. opencode needs its own config/cache
dirs (`~/.config/opencode`, `~/.local/share/opencode`, …) in the baseline grant
set; enumerate and grant them so opencode still functions confined.

## Verification

- **Parity guard.** Extend `optio-demo/tests/test_config_parity.py` to assert
  the triad (`fs_isolation`, `extra_allowed_dirs`, `delivery_type`) is present
  and type-identical on all 7 configs, and that each raises when `fs_isolation`
  is on and `delivery_type` is unset.
- **Live claustrum check on the 3 newly-wrapped engines (grok, codex,
  opencode)** against the real installed binaries — fail-closed proven: with
  Landlock available the agent is confined to workdir + grants; simulate
  kernel-can't-Landlock ⇒ launch refuses. Per
  [[feedback_real_binary_capability_data]], build/verify against real binaries,
  never fakes.
- **opencode web still binds localhost** under the wrap (Landlock ≠ network).
- **codex** still honours `network_access` under the claustrum + workspace-write
  combination.
- Existing per-engine suites stay green (two-phase `make test`).

## Scope boundary

- **In scope:** the claustrum triad only — `fs_isolation`,
  `extra_allowed_dirs`, `delivery_type` — hoisted to the mixin; universal
  claustrum wrap + notice on all 7; grok profile removal; codex sandbox rework;
  opencode wiring.
- **Out of scope (parked follow-up):** extracting the *other* genuinely-common
  config fields (~20+ candidates: `install_dir`, `install_if_missing`,
  `before_execute`/`after_execute`/`on_deliverable`, `ssh`, `seed_id`, …) into a
  broader shared base. That is a separate, larger refactor with per-engine
  default/semantic drift to reconcile against the parity guard. NOT
  `permission_mode` (4 distinct value-sets), `sandbox` (2 engines only),
  `ttyd_install_dir` (absent on 2), `model` (per-engine enum).

## Risks / open items

- **Frozen-dataclass inheritance + `__post_init__` chaining** across the mixin
  and 7 engine configs — get the `super().__post_init__()` wiring right so no
  engine silently drops its existing validation.
- **codex process tree under claustrum** (app-server transport) — confirm the
  spawn point accepts the argv-prefix wrap the way the ttyd engines do.
- **opencode baseline grants** — under-granting breaks opencode functionally;
  enumerate real dirs against the live binary.
- **grok** currently has no `delivery_type`; adding it + the mandatory rule is a
  behavior change for existing grok callers (they must now set it). Acceptable
  per the security rationale; call it out in the plan.
