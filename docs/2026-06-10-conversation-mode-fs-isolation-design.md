# Conversation-Mode Filesystem Isolation (claustrum) — Design

This spec was written against the following baseline:

**Base revision:** `bdfcf379378b565699020b8bebf67dd303a00c42` on branch `csillag/claudecode-config-dir-isolation` (as of 2026-06-10T12:34:11Z)

## Summary

The claustrum Landlock fs-isolation wrapper is currently applied only to the **iframe** (tmux/ttyd) launch path. Main's **conversation** mode (headless `claude -p --input-format stream-json --output-format stream-json`) is a second, separate launch path that is not wrapped — so conversation tasks run unconfined. This spec applies the same claustrum wrapper to the conversation launch, so conversation-mode tasks get the identical filesystem confinement as iframe-mode tasks.

The allowlist, provisioning, and fail-closed behavior are reused unchanged. **pasta/netns is not involved**: conversation mode is headless and authenticates from seeded/planted credentials — there is no in-session OAuth loopback to seal (verified live: `/login` and other interactive commands are unavailable in `-p` mode; auth comes from the seed). So conversation mode is claustrum-only.

## Background

- Conversation launch (`session.py` `_conversation_body`): builds `argv` via `build_conversation_argv`, env via `conversation_launch_env`, then `cmd = " ".join(shlex.quote(a) for a in argv)` and `host.launch_subprocess(cmd, env=env, cwd=host.workdir, env_remove=config.scrub_env, stdin=True)`.
- Iframe launch already builds a wrapper inline: `claustrum_wrap = [claustrum_path, "--best-effort", "--abi-min", "1", *grants, "--"]` (when `config.fs_isolation`), threaded into `_build_claude_shell_command`.
- `claustrum` `execve`s its target command, so the wrapped claude inherits stdin/stdout/stderr — the bidirectional stream-json pipes pass through unchanged.
- claustrum provisioning already happens in `_prepare` (`ensure_claustrum_installed`, gated only on `config.fs_isolation`, mode-agnostic); the resulting `claustrum_path` is a closure variable visible to `_conversation_body`.

## Changes

1. **Shared wrap helper.** Extract the inline iframe wrap computation into a module-level helper in `session.py`:

   ```python
   async def _build_claustrum_wrap(host, config, claustrum_path):
       """Return the claustrum argv prefix for an fs-isolated launch, or None
       when fs_isolation is off. Shared by the iframe and conversation paths."""
       if not config.fs_isolation:
           return None
       from . import fs_allowlist
       cache_dir = await host_actions._resolve_cache_dir(host, config.claude_install_dir)
       grants = fs_allowlist.build_grant_flags(
           workdir=host.workdir,
           claude_cache_dir=cache_dir,
           extra_allowed_dirs=config.extra_allowed_dirs,
       )
       return [claustrum_path, "--best-effort", "--abi-min", "1", *grants, "--"]
   ```

2. **Iframe path** (`_claudecode_body`): replace the inline block with `claustrum_wrap = await _build_claustrum_wrap(host, config, claustrum_path)`. No behavior change.

3. **Conversation path** (`_conversation_body`): after building `argv` and before joining into `cmd`, prepend the wrapper when present:

   ```python
   wrap = await _build_claustrum_wrap(host, config, claustrum_path)
   if wrap:
       argv = [*wrap, *argv]
   cmd = " ".join(shlex.quote(a) for a in argv)
   ```

4. **Demo.** Flip the two `mode="conversation"` demo tasks (`optio_demo/tasks/claudecode.py`) from `fs_isolation=False` (the rebase TODO) to `fs_isolation=True` + a `delivery_type`, now that the headless launch is wrapped.

## Unchanged / reused

- **Provisioning** (`_prepare` → `ensure_claustrum_installed`): mode-agnostic, already gated on `fs_isolation`.
- **Allowlist** (`fs_allowlist.build_grant_flags`): identical — workdir `--rwx`, claude cache `--rox`, the static baseline, caller `extra_allowed_dirs`. Conversation needs no extra paths (same claude binary, same isolated home; tmux/ttyd are not in the picture and were never inside claustrum anyway).
- **Fail-closed**: provisioning failure raises in `_prepare`; `--abi-min 1` makes claustrum exit non-zero (refuse to run unconfined) when Landlock is unavailable.

## Out of scope

- **pasta/netns** — no in-session OAuth loopback in conversation mode; nothing to seal.
- **Real stream-json-under-claustrum end-to-end test** — driving a real authenticated headless session inside claustrum in pytest is heavy and brittle; the confinement property is already proven mode-independently by `test_fs_isolation_e2e`, and the stdin/stdout passthrough is a live-test item.

## Testing

One unit test (mirroring the iframe `test_claustrum_wrap`):

- Drive the conversation launch path far enough to obtain the `cmd` string it would pass to `launch_subprocess` (e.g. by capturing the args via a fake host / monkeypatching `launch_subprocess`, or by unit-testing `_build_claustrum_wrap` + the prepend directly).
- Assert: when `fs_isolation=True`, the command is prefixed with `claustrum --best-effort --abi-min 1 <grants> --` followed by the claude conversation argv, with grants matching `fs_allowlist.build_grant_flags`.
- Assert: when `fs_isolation=False`, no claustrum prefix is present.
