# optio-claudecode Filesystem Isolation (claustrum) — Design

This spec was written against the following baseline:

**Base revision:** `1a66c1aa38d03c649ec069a5f2e1ad96f6a2214b` on branch `csillag/claudecode-config-dir-isolation` (as of 2026-06-09T12:15:58Z)

## Summary

Wrap the `claude` launch in optio-claudecode with **[claustrum](https://github.com/deai-network/claustrum)**, a standalone Landlock filesystem-sandbox CLI, so a task's `claude` process (and every tool it spawns) can only read and write an explicit allowlist of paths. This is enforcement layered on top of the existing `HOME` / `CLAUDE_CONFIG_DIR` redirect:

- **Redirect (already shipped on this branch)** points `claude` at the per-task isolated home so it *voluntarily* reads/writes the right place. Cooperative; keeps `claude` functional and keeps denial-noise low.
- **claustrum (this spec)** *enforces*, at the kernel level, that nothing outside the allowlist is reachable — covering what env redirection cannot: the host user's real `~/.claude`, `~/.ssh`, `~/.gitconfig`, anything outside the workdir, tool subprocesses, `getpwuid`-resolved `~`, and a future `claude` that ignores `CLAUDE_CONFIG_DIR`.

The feature is **default-on**, **fail-closed**, and applies **both local and remote** (unlike the network seal, which is local-only).

Why Landlock (and a separate tool): see the claustrum design. In short, Landlock is the only unprivileged filesystem-confinement mechanism that works on every host we tested — unprivileged user namespaces (bwrap/unshare) are disabled or AppArmor-gated on most modern hosts.

## Scope

In scope: provisioning claustrum, building the per-task allowlist, wrapping the launch, the task-definition surface, the freshness-notification flow, fail-closed behavior, demo adoption, and the repo-wide migration the new default-on flag forces.

Out of scope: network confinement (the existing pasta seal owns that); the claustrum tool itself (shipped, v0.1.0); Excavator adoption (a later, separate spec).

## 1. Task-definition surface

In `packages/optio-claudecode/src/optio_claudecode/types.py`, `ClaudeCodeTaskConfig` gains:

- `fs_isolation: bool = True` — default on. A caller opts a single task out by setting `False`.
- `extra_allowed_dirs: list[AllowedDir] | None = None` — **add-only** caller extensions to the allowlist. Each entry is `{path: str, mode: "ro" | "rw"}`. Callers may only *add* access; they cannot mask or remove anything from the baseline (the security floor is non-negotiable). A new lightweight type `AllowedDir` (a dataclass or `TypedDict`) defines the shape; `mode` is validated to be exactly `"ro"` or `"rw"`.
- `delivery_type: str | None = None` — **mandatory when `fs_isolation` is True**. It is the top-level subdirectory under `<workdir>/deliverables/` used to route the freshness notification (§3). For Excavator this will be `"bug-report"`; the demo uses `"system-notices"`.

`__post_init__` validation:
- If `fs_isolation` is True and `delivery_type` is falsy → raise `ValueError` with an actionable message ("fs_isolation requires delivery_type; set delivery_type=... or fs_isolation=False").
- Each `extra_allowed_dirs` entry's `mode` must be `"ro"` or `"rw"`, else `ValueError`.

This validation gating is what makes the repo-wide migration (§8) mechanical and test-enforced.

## 2. claustrum provisioning

New helpers in `host_actions.py`, following the existing `ensure_claude_installed` / `ensure_ttyd_installed` / `_resolve_cache_dir` / `_detect_ttyd_asset_name` patterns.

- **Pinned version:** a module constant `CLAUSTRUM_PINNED_TAG = "v0.1.0"`. This is the audited, vendored version; bumping it is a deliberate, reviewed action (§3 notifies; it never auto-follows latest).
- **`ensure_claustrum_installed(host, hook_ctx, ...) -> str`:**
  1. Detect the **target** host architecture: `uname -m` → GOARCH map (`x86_64 → amd64`, `aarch64 → arm64`; unknown arch → clear error). (`uname -s` must be `Linux`.)
  2. Resolve an **engine** cache path keyed by `(tag, arch)`, e.g. `<cache>/claustrum/<tag>/<arch>/claustrum`, reusing the `_resolve_cache_dir` shell-echo pattern so it works for both local and remote target placement.
  3. **Cache miss → build on the engine** (never on an ssh target): clone `github.com/deai-network/claustrum` at `CLAUSTRUM_PINNED_TAG` into a temp dir, then `CGO_ENABLED=0 GOOS=linux GOARCH=<arch> go build -trimpath -o <cache binary> .`. Cache the resulting static binary.
  4. **Place** the cached binary on the target host via `host.put_file_to_host(<bytes-or-path>, <target claustrum path>)`, then `chmod +x`, then verify with `claustrum --version`.
  5. Return the claustrum path on the target host.
- **New engine prerequisites:** `git` and a Go toolchain on the engine host. Missing either, with `fs_isolation` on → fail-closed (§6).
- **Freshness check (separate, cheap):** `git ls-remote --tags https://github.com/deai-network/claustrum` from the engine; pick the highest semver tag. If it is newer than `CLAUSTRUM_PINNED_TAG`, return it so §3 can notify. This is engine-side egress only (the engine has network; ssh targets are never asked to reach GitHub).

Compilation and tag-checking happen **on the engine exclusively**. The ssh target only ever receives a finished static binary.

## 3. Freshness notification (pre-launch, rides the deliverable loop)

When `fs_isolation` is on and the freshness check found a tag newer than the pinned one, optio notifies the consumer **before the real agent starts**, reusing the existing deliverable mechanism rather than inventing a channel:

1. Write a one-sentence file to `<workdir>/deliverables/<delivery_type>/claustrum-update-<newtag>.md` (e.g. "A newer claustrum release (<newtag>) is available; the pinned version is <pinned>. Consider auditing and bumping.").
2. Deliver it through the existing `on_deliverable` path (`DeliverableCallback`, defined in `optio-agents/.../protocol/session.py`), so the consumer handles it exactly like any deliverable, routed by its `<delivery_type>` prefix.
3. **Clean up before launching the real agent:** delete the deliverable file and scrub its auto-generated `Deliverable: <path>` line from `<workdir>/optio.log`, so the agent session starts on a clean slate with no stale deliverable or log noise.

This is a self-contained pre-launch step; it does not interfere with the agent's own deliverables during the run.

## 4. Allowlist construction

A new module `fs_allowlist.py` builds the claustrum grant flags from three parts:

- **Static baseline** — a curated, commented constant list of `(mode, path)` grants covering what `claude` and its tool subprocesses need: system directories `--rox` (`/usr`, `/bin`, `/lib`, `/lib64`, `/etc`, and the usrmerge variants that exist), required `/dev` nodes (`/dev/null`, `/dev/zero`, `/dev/urandom`, `/dev/tty`, …), `/proc`, CA certificates (`/etc/ssl`), timezone/locale. **This baseline is produced once, during implementation, by running the LD_PRELOAD path tracer** against a real `claude` session and distilling the touched paths. The tracer is a development tool to *build* the list; it is **not** a runtime dependency. Non-existent baseline paths are harmless (claustrum ignores missing paths).
- **Dynamic per-task** — computed at launch:
  - the task **workdir** → `--rwx` (claude tools may write a script and execute it),
  - the **claude install tree** (resolved from the active `claude_path`, including the `node`/versions directory under `~/.local/share/claude`) → `--rox`.
  - (The isolated home `<workdir>/home` is already covered by the workdir `--rwx` grant.)
- **Caller** — `extra_allowed_dirs` entries mapped `ro → --ro`, `rw → --rw`.

The module emits the ordered claustrum flag list. Pure and unit-testable: given inputs → expected flags.

## 5. Launch wrapping

In `host_actions.py` `_build_claude_shell_command`, when `fs_isolation` is on, wrap the assembled claude command with claustrum, composing with the existing (local-only) pasta network seal as **pasta outside, claustrum inside**:

- Build the claustrum prefix: `[<claustrum_path>, "--best-effort", "--abi-min", "1", <allowlist grant flags>, "--"]`.
  - `--abi-min 1` is the security floor: refuse (fail-closed) only if Landlock is truly absent (ABI 0). ABI 1 already gives full read/write/exec confinement — our entire requirement.
  - `--best-effort` uses `refer`/`truncate` (ABI 2/3) where available and degrades gracefully where not; isolation stays fully enforced regardless.
- Resulting inner command:
  - local **with** pasta: `pasta -- claustrum <grants> -- bash -c "IS_SANDBOX=1 <claude> <flags>"`
  - local without pasta, **and all remote**: `claustrum <grants> -- bash -c "<claude> <flags>"`
- pasta runs **outside** claustrum, so claustrum confines only `claude` and its subprocesses; pasta itself is unconfined and needs no allowlist entry. pasta stays local-only as today; claustrum applies local **and** remote.

Unlike the env-driven `OPTIO_CLAUDECODE_NETNS`, claustrum activation and its allowlist are driven by the **task config** (`fs_isolation`, `extra_allowed_dirs`) and per-task computed paths, threaded into `_build_claude_shell_command` as parameters.

When `fs_isolation` is off, no claustrum wrapper is applied (behavior identical to today).

## 6. Fail-closed behavior

With `fs_isolation` on, the task must not launch `claude` unsandboxed:

- **Provisioning failure** (engine lacks `git`/Go, clone/build fails, placement/`chmod`/`--version` verification fails) → raise a clear error during `_prepare`; the task does not proceed to launch.
- **Landlock absent on the target** → claustrum itself exits non-zero (its `--abi-min 1` floor), which surfaces as a launch failure rather than an unconfined run.

The error message names the cause and the opt-out (`fs_isolation=False`) so an operator on an incapable host has a clear path.

## 7. Orchestration

- In `session.py` `_prepare` (after the claude/ttyd install steps), add `ensure_claustrum_installed(...)` and the freshness check (both engine-side), guarded by `config.fs_isolation`.
- After `plant_home_files(...)` in `_claudecode_body` (and after any seed merge), if a newer tag was found, run the §3 freshness-deliver-then-cleanup step before launch.
- The allowlist (§4) is computed at launch and threaded into `launch_ttyd_with_claude` → `_build_claude_shell_command` (§5), alongside `config.fs_isolation` and `config.extra_allowed_dirs`.

## 8. Demo adoption & repo-wide migration (must-not-break)

Adding a required field gated by a default-on flag is a breaking change for every existing `ClaudeCodeTaskConfig` construction. The in-repo canonical demo (optio-demo) uses optio-claudecode and **must keep working**.

- **optio-demo** (`packages/optio-demo/src/optio_demo/tasks/claudecode.py`, two configs at ~lines 175 and 206): set `delivery_type="system-notices"` on both. The seed-pinned task already wires `_on_deliverable`, which will also receive the pre-launch freshness notice and simply accept it. Confirm the baseline + workdir(`--rwx`) + claude-install allowlist covers the demo's actual behavior — read `context.txt` in the workdir, interactive OAuth login (network is not Landlock-restricted), seed capture from `<workdir>/home/.claude` (read by the engine outside the jail), and shipping a deliverable to `deliverables/`. All fall inside the allowlist by construction.
- **optio-demo as the in-repo end-to-end test** for this feature: a real `fs_isolation=True` run that (a) completes normally and (b) denies a read of a path outside the workdir. This is consistent with how the demo already validates optio-claudecode end-to-end in the repo.
- **Repo-wide migration:** audit every `ClaudeCodeTaskConfig(...)` construction across the repo (optio-demo src; tests in optio-claudecode and optio-demo, e.g. `test_claudecode_task.py`, `test_demo_smoke.py`, `test_feedback_helper.py`). Each site either sets a `delivery_type` (sites exercising isolation) or sets `fs_isolation=False` explicitly (sites not concerned with it). The full test suite staying green is the safety net for "don't break it."

## Testing

- **Unit:** `fs_allowlist` construction (baseline + dynamic + caller extras → expected ordered claustrum flags); `ClaudeCodeTaskConfig.__post_init__` validation (`delivery_type` mandatory when `fs_isolation`; `mode` validation).
- **Provisioning:** `ensure_claustrum_installed` builds, caches by `(tag, arch)`, places, and verifies; idempotent on a warm cache; cross-compile path for a non-native arch.
- **Freshness:** a simulated newer tag results in the deliverable being written, delivered via `on_deliverable`, then removed, with its `optio.log` line scrubbed — all before launch.
- **Fail-closed:** with `fs_isolation` on and provisioning forced to fail, the task raises and does not launch.
- **Integration (real, on a Landlock-capable kernel, via the demo / `fake_claude` harness):** a full `fs_isolation=True` session where a read outside the workdir is denied (`EACCES`) and reads/writes inside the workdir succeed; the session otherwise completes normally.

## Deferred to the implementation plan

- The exact static baseline path list — produced by running the LD_PRELOAD tracer against a real `claude` session during implementation, then distilled into the commented `fs_allowlist` baseline constant.
