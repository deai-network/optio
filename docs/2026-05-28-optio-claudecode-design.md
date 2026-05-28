# optio-claudecode — Design

This spec was written against the following baseline:

**Base revision:** `9712ae281d29c9401d2e8c1a06abcc47695a9843` on branch `main` (as of 2026-05-28T00:00:00Z)

## Summary

`optio-claudecode` is a new sibling package to `optio-opencode`. It runs Anthropic's Claude Code CLI as an optio task — either as a local subprocess or on a remote host over SSH — and exposes its terminal UI inside the optio dashboard via an iframe widget. The interactive Claude Code TUI is wrapped by `ttyd`, which serves the terminal over HTTP+WebSocket so the dashboard's existing widget proxy can embed it without protocol-level work on the optio side.

The package mirrors `optio-opencode`'s public API shape (`ClaudeCodeTaskConfig` + `create_claudecode_task`), reuses the same `optio.log` keyword contract (`STATUS`/`DELIVERABLE`/`DONE`/`ERROR`), the same hooks (`before_execute` / `after_execute` / `on_deliverable`), the same SSH/remote story via `optio-host`, and the same `<workdir>/AGENTS.md` prompt injection. The two packages are intended to be interchangeable from a consumer's point of view: the same `consumer_instructions` should work against either agent.

## Goals

- Run Claude Code interactively in the browser via an iframe widget.
- Mirror `optio-opencode`'s public-API surface where it makes sense; share the optio.log contract verbatim.
- Local subprocess + remote SSH from day one, using `optio-host`'s existing abstractions for both.
- Authenticate the agent from caller-supplied credentials so a task wakes up ready to work — no user-side login dance per task.
- Fully isolate per-task Claude state (credentials, settings, session history) so concurrent tasks on the same host cannot collide.
- Auto-install both the `claude` binary and `ttyd` on the host if missing, in a way that does not mutate the host user's shell rc files.

## Non-goals (v1)

- **Resume support** — no workdir snapshot, no session rehydration, no `resume.log`. Tasks are launch-fresh-each-time. (`optio-opencode`'s snapshot machinery is not duplicated. Resume can land as a follow-up release if needed; the architecture is compatible with it.)
- **Custom React chat widget** — we use ttyd's xterm-based browser terminal, not a bespoke transcript renderer over `stream-json`.
- **Re-implementing Claude Code's installer in Python** — we call the vendor `claude.ai/install.sh` end-to-end and accept the loss of byte-progress UI for that download. (See Open questions.)
- **Permission defaults** — optio-claudecode imposes no permission policy. Caller decides.
- **Headless `claude -p` mode** — interactive dialog is a hard requirement; the headless mode does not satisfy it without re-implementing the chat surface.

## Architecture

Local case:

```
optio worker (python)
  └─ LocalHost (optio-host)
       └─ asyncio subprocess: ttyd
            └─ bash -c 'cd <workdir> && exec /<real-home>/.local/bin/claude'
                 with env: HOME=<workdir>/home, ...config env
       └─ tail -F <workdir>/optio.log  ← STATUS/DELIVERABLE/DONE/ERROR
       └─ widget proxy iframes http://<bind>:<port> into dashboard
```

Remote case (SSH):

```
optio worker (python)
  └─ RemoteHost (optio-host, asyncssh)
       ├─ exec: ttyd ...
       ├─ exec: tail -F .../optio.log
       ├─ SFTP: ship credentials.json, settings.json, AGENTS.md
       ├─ SFTP: fetch DELIVERABLE files
       └─ local SSH port-forward: localhost:<local-port> → <bind>:<port> on host
            └─ widget proxy iframes the local-port side
```

Both shapes are existing optio-host abstractions; the claudecode package never speaks SSH directly.

## Public API

```python
from optio_claudecode import ClaudeCodeTaskConfig, create_claudecode_task

def get_tasks():
    return [
        create_claudecode_task(
            process_id="my-task",
            name="My task",
            config=ClaudeCodeTaskConfig(
                consumer_instructions="...",
                # Authentication (one or more of these typically set)
                credentials_json=None,         # dict | bytes | str | None
                claude_config=None,            # dict | None → ~/.claude/settings.json
                env=None,                      # dict[str, str] | None
                # Permission knobs (forwarded verbatim to claude CLI flags)
                permission_mode=None,          # default | plan | acceptEdits | bypassPermissions
                allowed_tools=None,            # list[str] | None
                disallowed_tools=None,         # list[str] | None
                # Host
                ssh=None,                      # None = LocalHost; else SSHConfig
                # Binary install
                install_if_missing=True,       # auto-install claude
                install_ttyd_if_missing=True,  # auto-install ttyd
                claude_install_dir=None,       # default: ~/.local/bin on host
                ttyd_install_dir=None,         # default: ~/.local/bin on host
                # Hooks
                before_execute=None,
                after_execute=None,
                on_deliverable=None,
            ),
        )
    ]
```

The returned `TaskInstance` has `ui_widget="iframe"`. `supports_resume` is omitted from v1 (effectively `False`).

### Field semantics

- **`consumer_instructions`** — appended verbatim after the optio coordination prompt section in the generated `<workdir>/AGENTS.md`. Same role as in opencode.
- **`credentials_json`** — opaque payload (dict, bytes, or str). Written verbatim to `<workdir>/home/.claude/.credentials.json` (chmod 0600) before launching claude. Format is Claude's; optio does not parse or validate beyond serialization. When `None`, no file is written and claude will hit its own "Not logged in" path inside the TUI.
- **`claude_config`** — dict serialized as JSON to `<workdir>/home/.claude/settings.json`. Replaces any prior content (no merge). Used for permission allowlists, default tools, MCP servers, etc.
- **`env`** — additional environment variables injected into the ttyd-spawned shell that wraps claude. Used for `ANTHROPIC_BASE_URL`, Bedrock/Vertex routing vars, or any other host-claude env.
- **`permission_mode` / `allowed_tools` / `disallowed_tools`** — forwarded verbatim to claude as `--permission-mode <x>` / `--allowed-tools "<csv>"` / `--disallowed-tools "<csv>"`. When all are `None`, no flags are passed and claude uses its own default (interactive per-tool prompts inside the TUI).
- **`ssh`** — when `None`, the task runs on `LocalHost`. Otherwise, `RemoteHost` is constructed from the `SSHConfig`. Identical to opencode.
- **`install_if_missing` / `claude_install_dir`** — when `True` (default) and `[ ! -x <claude_install_dir>/claude ]`, the vendor install script (`curl -fsSL https://claude.ai/install.sh | bash`) is run via `host.run_command`. `claude_install_dir` defaults to `~/.local/bin` on the host. When `False` and the binary is absent, raise `RuntimeError`.
- **`install_ttyd_if_missing` / `ttyd_install_dir`** — same shape for ttyd. ttyd is downloaded from the `tsl0922/ttyd` GitHub Releases (per-platform static binary), placed at `<ttyd_install_dir>/ttyd`, chmod +x. When `False` and absent, raise `RuntimeError`.
- **`before_execute` / `after_execute` / `on_deliverable`** — exactly the opencode signatures and failure semantics. Reuse `optio_host.HookContext` / `optio_host.protocol.session.{HookCallback, DeliverableCallback}`.

## HOME isolation

Each task gets a workdir tempdir on the host (`/tmp/optio-claudecode-<uuid>/`). Inside the workdir, the framework creates `<workdir>/home/` and uses that as `HOME` for the entire claude+ttyd process tree:

- `<workdir>/home/.claude/.credentials.json` (chmod 0600) — written if `credentials_json` is set.
- `<workdir>/home/.claude/settings.json` — written if `claude_config` is set.
- `<workdir>/home/.claude/projects/<encoded-cwd>/...jsonl` — Claude's session log, indexed by CWD. With CWD = `<workdir>`, sessions are workdir-relative and self-contained.
- `<workdir>/home/.claude.json` — top-level state file claude maintains on its own.

The real `~/.claude/` of the host user is never read or modified by an optio-claudecode task. This was verified empirically against `claude` v2.1.153: running `env -i HOME=/tmp/test PATH=... claude --print "say hi"` produces "Not logged in" without reaching the real credentials file, and `~/.claude/` is untouched after the run.

The `claude` binary itself is invoked by absolute path (`<real-host-home>/.local/bin/claude`, or `<claude_install_dir>/claude` when that config field is set), so HOME isolation does not affect binary resolution.

## ttyd launch

```
<ttyd_install_dir>/ttyd \
  -W \                       # writable (user can type)
  -i <bind_iface> \          # see "Network binding"
  -p <port> \                # framework-allocated
  -m 1 \                     # one concurrent client; refresh reconnects cleanly
  -T xterm-256color \
  -- \
  env HOME=<workdir>/home <extra-env-from-config> \
  bash -c 'cd <workdir> && exec <claude_install_dir>/claude <permission-flags>'
```

`-o` is NOT used: it would terminate the ttyd server on the first client disconnect, breaking the desired "user can close the iframe and reopen later" behavior.

ttyd outputs no inline progress to optio; readiness is detected by the framework's existing port-probing logic (same as opencode).

## Network binding

Identical to opencode's existing handling:

- `LocalHost` in multi-container deploys: derive `bind_iface` from `OPTIO_WIDGET_TUNNEL_BIND` env so ttyd binds to a sibling-container-reachable interface. `establish_tunnel` is a no-op for LocalHost.
- `RemoteHost`: bind to loopback on the remote host; expose via SSH local port-forward (`asyncssh.SSHClientConnection.forward_local_port`). The dashboard widget proxy iframes the local-port side.

Port allocation reuses optio-host's existing mechanism.

## optio.log contract + AGENTS.md prompt

Same four-keyword contract as opencode, byte-identical wording:

- `STATUS: [N%] <msg>` — progress
- `DELIVERABLE: <path>` — file in `<workdir>/deliverables/`; framework SFTPs it back, decodes UTF-8, invokes `on_deliverable(hook_ctx, relative_path, decoded_text)`
- `DONE[: summary]` — terminates session as success
- `ERROR[: message]` — terminates session as failure

The framework writes `<workdir>/AGENTS.md` containing:

1. The optio coordination protocol (log channel, deliverables, termination semantics).
2. The verbatim `consumer_instructions`.

Claude Code reads `AGENTS.md` natively, so no extra hooking is needed.

## Termination

Driven by the agent's `DONE`/`ERROR` line via the AGENTS.md prompt. When the log-tailer sees the keyword, the framework terminates the claude process (sends `SIGTERM`, escalates to `SIGKILL` after a grace period), terminates ttyd, closes the SSH tunnel (remote case), and removes the workdir. From the dashboard's point of view, the iframe widget detaches; the task transitions to the appropriate terminal state.

User-initiated cancellation flows through the existing optio task-cancel path: framework sends `SIGTERM` to ttyd → claude exits → cleanup proceeds.

## Hooks

Hooks reuse the `optio_host.HookContext` surface from opencode without modification:

- `before_execute(hook_ctx)` — runs after both `claude` and `ttyd` binaries are confirmed present, and after `credentials.json` / `settings.json` / `AGENTS.md` are written into `<workdir>/home/.claude/` and `<workdir>/`. Runs before ttyd is started. Failure: session fails immediately; `after_execute` still runs; cleanup runs.
- `after_execute(hook_ctx)` — runs after claude has terminated (or been cancelled). Failure semantics identical to opencode.
- `on_deliverable(hook_ctx, deliverable_path, decoded_text)` — identical signature to opencode's, including the relative-path convention.

## Shared refactor precursor: `optio_host.agents`

The optio coordination prompt (the `BASE_PROMPT_PRE` / `BASE_PROMPT_POST` strings and the `compose_agents_md()` composer) currently lives in `optio_opencode.prompt`. Since the user wants `optio-opencode` and `optio-claudecode` to be interchangeable with the same `consumer_instructions`, this content must be the single source of truth across both packages.

**Precursor work (separate PR, lands first):**

1. Create a new module `optio_host.agents` (one new file, e.g. `packages/optio-host/src/optio_host/agents.py`).
2. Move `BASE_PROMPT_PRE`, `BASE_PROMPT_POST`, and `compose_agents_md()` into it. The signature is reshaped so the host module no longer knows about resume-specific concerns:
   ```python
   compose_agents_md(
       consumer_instructions: str,
       *,
       resume_section: str | None = None,
   ) -> str
   ```
   - `optio-opencode` renders the resume section itself (using its existing `RESUME_SECTION_TEMPLATE` and `_render_resume_section`, which know about `workdir_exclude` and snapshot semantics) and passes the rendered string in.
   - `optio-claudecode` passes `None` in v1 (no resume).
3. `optio_opencode.prompt` keeps `RESUME_SECTION_TEMPLATE`, `_render_resume_section`, and a thin opencode-facing helper that takes `workdir_exclude` + `supports_resume` and calls `optio_host.agents.compose_agents_md(...)` underneath. The opencode public call site in `session.py` is updated only as needed.
4. Run the opencode test suite unchanged to confirm behavior is preserved.

This precursor must land before optio-claudecode can be merged; otherwise the two packages would carry drifting copies of the prompt.

## Binary install details

### claude

The `claude_install_dir` config field refers to the directory containing the **launcher symlink** that the framework will exec, not the directory containing the real native binary. The vendor installer always places the native per-version binaries under `~/.local/share/claude/versions/<v>/` (this is not configurable). It then creates a symlink named `claude` at `~/.local/bin/claude` (configurable only in the sense that the framework can override `claude_install_dir` to point at a different directory in which to *look for* the symlink).

On task setup, if `install_if_missing=True` and `<claude_install_dir>/claude` is not executable:

```
curl -fsSL https://claude.ai/install.sh | bash
```

The vendor script downloads + verifies SHA256 + writes the native binary under `~/.local/share/claude/versions/<v>/` and updates the `~/.local/bin/claude` symlink. It may also append a PATH line to the host user's shell rc if `~/.local/bin` is not already on PATH. The framework sidesteps the PATH concern by always invoking claude via the absolute path `<claude_install_dir>/claude`.

After install, claude self-updates via background symlink rotation; the framework re-resolves the symlink at the start of each task.

### ttyd

Download a per-platform static binary from `https://github.com/tsl0922/ttyd/releases`. Place at `<ttyd_install_dir>/ttyd`, chmod +x. Platform detection mirrors the opencode pattern (uname-based).

## Testing

Mirror `optio-opencode`'s test layout:

- `test_sanity.py` — module imports, public-API surface.
- `test_types.py` — `ClaudeCodeTaskConfig` field defaults, validation, encoding/decoding of `credentials_json` payloads.
- `test_prompt.py` — `optio_host.agents.compose_agents_md` produces the expected body for claudecode (no resume section).
- `test_session_local.py` — fake-claude script (mirroring opencode's `fake_opencode.py`) emits log keywords; assert termination, deliverable fetch, on_deliverable invocation.
- `test_session_remote.py` — same against the test SSH container from opencode (`docker-compose.sshd.yml`).
- `test_session_hooks.py` — before/after/on_deliverable signatures and failure semantics.
- `test_host_local.py` / `test_host_remote.py` — HOME-isolation assertions: real `~/.claude/` untouched after a session.
- `test_smart_install.py` — claude-install-script invocation + ttyd-release download, behind mocks; absent-binary failure path with `install_if_missing=False`.

Local integration needs MongoDB via Docker (per repo convention).

## Out of scope (deferred)

- **Resume support** (snapshot, `resume.log`, `on_resume_refresh`, `session_blob_encrypt/decrypt`). Will be revisited after v1 ships.
- **Multiple concurrent users on one ttyd session.** `-m 1` enforces single client. A "shared session" mode (`-m N`, multi-user TUI) is not in scope.
- **In-browser keyboard remapping / mobile-friendly TUI ergonomics.** Whatever ttyd ships is what users get.
- **Bundling vendored ttyd / claude binaries** inside the wheel. Cross-platform fat-binary distribution is out of scope; we rely on auto-install.

## Open questions / follow-ups

- **Byte-progress for claude download.** Vendor install.sh is monolithic. If download time is noticeable in practice, re-implement the install in Python (~80 lines: platform detect, version fetch, manifest fetch, download via `hook_ctx.download_file` for byte-progress, checksum verify, symlink). Track as a follow-up only if user complains.
- **`claude install` shell-rc mutation.** Vendor script may append to `~/.bashrc` if `~/.local/bin` is not on PATH. The framework's absolute-path approach makes this irrelevant for optio's own operation, but the host user's shell may still get a one-line PATH entry. Document; do not work around.
- **Excavator integration.** Excavator (the user's downstream consumer) will adopt this package "very soon" after it ships, and must pass `permission_mode="bypassPermissions"` so its autonomous flows don't block on per-tool prompts. Tracked in the user's memory; not a part of this package's spec.
