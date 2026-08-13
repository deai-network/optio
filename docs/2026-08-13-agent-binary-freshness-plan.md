# Agent-binary freshness — implementation plan

**Bug (confirmed live, /tmp/optui-cc-update-fckup.md):** claustrum grants the shared
engine version cache `--rox` (fs_grants.py:47), so any in-session self-update write
into the cache EACCESes. claudecode relied on exactly that write for freshness and
does no upstream check on cache hit → its cache is FROZEN (stuck at 2.1.185 since
June while upstream is 2.1.231). Audit across all 7: cursor / codex / antigravity
share the failure class (MANUAL-ONLY — no cache-hit freshness check); grok /
opencode / kimicode already implement the correct pattern (suppress in-session
self-update + trusted UNCONFINED provisioning-time upstream check that refreshes
the cache in place).

**Fix principle (uniform):** freshness is owned by the unconfined provisioning path
(`ensure_<agent>_installed`), never by the confined session. Cache stays `--rox`.
Reference implementations in-repo: grok (`_grok_update_target` probe-then-refresh),
opencode/kimicode (fork `smart-install.sh --check` per launch).

**Branch:** `csillag/agent-binary-freshness` (off main). One task per engine,
package-local paths only, `git add <own paths>` (never -A), no Co-Authored-By.
**Verification deferred to the final phase** (`make test` + straggler greps); task
agents write tests but do not run suites.

**Real-binary rule (MANDATORY, see AGENTS.md lesson):** any assumption about a
binary's version-probe output, env-var kill-switch, or installer behavior MUST be
verified against the REAL binaries on this machine — `~/.cache/optio-cursor/`
(cursor-agent), `~/.cache/optio-antigravity/bin/agy`, `~/.cache/optio-codex/bin/codex`,
`~/.cache/optio-claudecode/versions/2.1.185` — and the real vendor install scripts
(fetch them read-only: `https://claude.ai/install.sh`, `https://cursor.com/install`).
Run probes read-only (`--version`, `--help`, `update --help`); never run an actual
update against the live caches.

Out of scope (user did not opt in): shared throttle stamp, per-engine operator
update-notices.

---

## Task 1 — optio-claudecode (FROZEN → FRESH)

Files: `packages/optio-claudecode/src/optio_claudecode/host_actions.py`,
tests, `packages/optio-claudecode/AGENTS.md`,
`docs/2026-05-31-optio-claudecode-runtime-cache-design.md` (addendum only).

1. **Launch-time upstream check** in `ensure_claude_installed` (host_actions.py:179):
   on cache hit, resolve upstream stable version — read the REAL install.sh to find
   the version endpoint it consults (a GCS/manifest URL; extract it into a module
   constant with a comment naming its provenance). Compare against
   `_newest_cached_version`. Upstream newer → run the EXISTING cache-miss install
   branch (vendor install.sh, unconfined, writes through the symlink into the
   cache) before linking; then link newest as before. Check is best-effort:
   curl failure / unparsable version → log + keep cached (offline workers must
   still launch). Mirror grok's shape (grok host_actions.py:263-286).
2. **`DISABLE_AUTOUPDATER=1`** in every session env: `conversation_launch_env` and
   `_build_claude_shell_command` (host_actions.py:536-544 area). Verify against the
   real binary/docs that this is the correct kill-switch (claude docs/`claude
   --help`/install.sh mention; record evidence in the commit message). The updater
   can never succeed under `--rox` — stop the doomed EACCES attempts.
3. **Docs**: fix the `ensure_claude_installed` docstring (autoupdater-maintains-cache
   claim is dead); ADD an addendum section to the 2026-05-31 design doc (do not
   rewrite history): the autoupdater-freshness design was invalidated by claustrum
   `--rox`; freshness now provisioning-owned. Update package AGENTS.md if it
   documents the update flow.
4. **Tests** (fake-host pattern of the existing test suite): hit + upstream newer →
   install command issued; hit + upstream equal/unreachable → no install; env
   assertions include `DISABLE_AUTOUPDATER=1` on both launch paths.

## Task 2 — optio-cursor (MANUAL-ONLY → FRESH)

Files: `packages/optio-cursor/src/optio_cursor/host_actions.py`, tests,
`packages/optio-cursor/AGENTS.md`, `packages/optio-cursor/src/optio_cursor/snapshots.py`
(comment update only if its "no self-update disable" note changes).

1. **Launch-time version check** in `ensure_cursor_installed` (host_actions.py:347):
   on cache hit, get cached version (`cursor-agent --version` against the REAL
   binary first to learn the output shape) and upstream latest — read the REAL
   `https://cursor.com/install` script to find its version-resolution endpoint;
   constant + provenance comment. Upstream newer → run the existing
   `_vendor_install_cursor` staging path (it installs latest into
   `<cache>/versions/<v>` + repoints the symlink), then proceed. Best-effort on
   network failure.
2. **Self-update suppression**: probe the real binary (`cursor-agent --help`,
   `cursor-agent update --help`, strings if needed) for a supported disable
   mechanism (env var / config key). If a mechanism is CONFIRMED against the real
   binary, set it in `_isolation_env`. If none is found, do NOT invent one —
   leave the updater be (its write lands in the pruned per-task workdir, harmless)
   and document that in a comment + snapshots.py note. Never ship an unverified
   kill-switch (the antigravity-effort lesson).
3. **Tests**: hit + newer → staging install path invoked; hit + current/unreachable
   → link-only; (if suppression shipped) env assertion.

## Task 3 — optio-antigravity (MANUAL-ONLY → FRESH)

Files: `packages/optio-antigravity/src/optio_antigravity/host_actions.py`, tests,
`packages/optio-antigravity/AGENTS.md`.

1. **Launch-time manifest check** in `ensure_antigravity_installed`
   (host_actions.py:415): on cache hit (post `_is_agy` identity gate), fetch the
   SAME updater manifest Tier-2 already uses (constants host_actions.py:82-87),
   read `manifest["version"]`, compare against the cached binary's version — probe
   the REAL `~/.cache/optio-antigravity/bin/agy` to learn the version-output shape
   (`agy --version` / `agy version`); parse accordingly. Mismatch (manifest newer)
   → run the existing `_install_antigravity_into_cache` Tier-2 path (download +
   SHA512 + swap). Best-effort: manifest unreachable → keep cached.
2. Self-update already disabled twice (env + settings) — unchanged.
3. **Tests**: hit + manifest newer → reinstall invoked; hit + same/unreachable →
   keep; version-parse unit test over the real probe output shape (fixture).

## Task 4 — optio-codex (pin made effective; stays PINNED-BY-DESIGN)

Files: `packages/optio-codex/src/optio_codex/host_actions.py`, tests,
`packages/optio-codex/AGENTS.md`.

Policy: the `_CODEX_VERSION` pin stays authoritative (wire-shape probes gate
upgrades — see host_actions.py:58-61 comment). The fix makes pin bumps EFFECTIVE
on warm caches, not upstream-tracking.

1. **Version stamp**: on every install into the cache, write `<cache>/codex.version`
   — pinned-download tier stamps `_CODEX_VERSION`; host-copy tier stamps the copied
   binary's reported version (probe the REAL `~/.cache/optio-codex/bin/codex
   --version` for the output shape).
2. **Cache-hit check** in `ensure_codex_installed` (host_actions.py:242-245): read
   the stamp; stamp == `_CODEX_VERSION` → hit as today. Stamp missing (legacy warm
   cache) or != pin → re-download the pinned release into the cache (Tier-2; do
   NOT host-copy here — the pin is authoritative on refresh), stamp, proceed.
   Keeps offline workers working: download failure with an executable cached
   binary → log + use cached (stale pin beats no engine).
3. **Tests**: warm cache + matching stamp → no download; stale/missing stamp →
   pinned download + stamp written; download failure + executable cache → falls
   back to cached.

## Final phase — verification (main loop)

- `docker run -d --rm --name optio-test-mongo -p 27017:27017 mongo:7`; `make test`
  green; stop container.
- Straggler greps: no remaining claim that the in-session autoupdater maintains
  the claudecode cache; `DISABLE_AUTOUPDATER` present on both claudecode launch
  paths; every `ensure_*_installed` cache-hit path now contains a freshness (or
  pin-stamp) check EXCEPT grok/opencode/kimicode which already had one.
- Live spot-check (manual, user): next optio-claudecode launch on this machine
  should install 2.1.231 into `~/.cache/optio-claudecode/versions/`.
