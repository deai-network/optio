# optio-claudecode: persistent ttyd sessions via tmux

This spec was written against the following baseline:

**Base revision:** `5e298d3f3f25cf3ffcb6de89205dd393482ba7ed` on branch `main` (as of 2026-06-01T17:16:38Z)

## Problem

optio-claudecode launches claude as ttyd's direct, per-connection child:

```
ttyd -W -i <iface> -p <port> -m 1 -T xterm-256color -- \
  env HOME=<workdir>/home PATH=… bash -c 'cd <workdir> && claude <flags>; rc=$?; <append DONE/ERROR to optio.log>'
```

ttyd's model is connection-scoped: it spawns the command when a WebSocket
client connects and SIGHUPs it when the client disconnects. Verified live
(2026-06-01): the child is spawned on connect and **killed on disconnect**;
the ttyd server survives but the agent does not.

Consequences, all contrary to optio's "persistent background process, N
observers" model (which optio-opencode already satisfies via its persistent
`opencode web` server):

1. claude does **not start until a browser connects**.
2. **Disconnecting the iframe kills the task** — claude dies, the bash wrapper's
   `rc=$?` fires, `DONE`/`ERROR` lands in `optio.log`, and the driver completes +
   tears the task down.
3. `-m 1` allows only **one viewer**.
4. Reconnecting (if the task even survived) would spawn a **fresh** claude with no
   conversation continuity.

## Goal

Bring optio-claudecode to parity with optio-opencode's lifecycle: the agent runs
as a true background process, independent of viewers; connect/disconnect/reconnect
only attach/detach a viewer; multiple viewers can observe the same live session.

Achieve this by running claude inside a **detached tmux session** that ttyd merely
**attaches** to. Verified live (2026-06-01): a child started in a detached tmux
session survives viewer disconnect **and** a subsequent reconnect→disconnect, and
runs before any viewer connects.

Non-goals: changing the opencode integration; changing the snapshot/resume
mechanism; auto-downloading tmux; redesigning completion detection.

## Design

### Launch and lifecycle

Replace the single `ttyd -- bash -c '…claude…'` launch with a two-process launch:

1. **Provision check.** Resolve tmux on the host via a login shell
   (`bash -lc 'command -v tmux'`). If absent, raise a clear, actionable error
   ("tmux is required on the worker for optio-claudecode; install it or add it to
   the container image"). Runs during session setup alongside
   `ensure_ttyd_installed`, before launch. No download/cache infra — tmux is a
   documented worker prerequisite (the prod container image installs it).

2. **Start claude detached, immediately.** On a per-task **private tmux socket**
   under the workdir:

   ```
   tmux -S <workdir>/tmux.sock new-session -d -s optio -x <cols> -y <rows> \
     'env HOME=<workdir>/home PATH=… [netns-seal] bash -c "cd <workdir> && claude <flags>; rc=$?; <append DONE/ERROR to optio.log>"'
   ```

   claude runs the moment the task launches — before and without any viewer. The
   private socket (`-S <workdir>/tmux.sock`) gives each task its own tmux server,
   isolating concurrent tasks from each other and honoring the existing
   HOME-isolation; teardown of that socket's server, and removal of the workdir,
   leave no global tmux state. The bash wrapper (env assignments, the optional
   `OPTIO_CLAUDECODE_NETNS` seal, and the `DONE`/`ERROR`-to-`optio.log` append) is
   carried over unchanged from today's `build_ttyd_argv`.

3. **ttyd attaches viewers.** ttyd no longer runs claude; it runs `tmux attach`:

   ```
   ttyd -W -i <iface> -p <port> -T xterm-256color -- \
     tmux -S <workdir>/tmux.sock attach -t optio
   ```

   `-m 1` is **dropped** so N viewers can attach to the same live session
   simultaneously (read-mostly; one drives — inherent to a shared terminal).

Net behavior: claude starts at launch and keeps running regardless of viewers;
connect/disconnect/reconnect only attach/detach a ttyd client; the conversation
persists across reconnects.

Implication (accepted): for `auto_start` tasks claude immediately works the task
and consumes tokens even if nobody opens the iframe — exactly the background-task
model intended, matching opencode. For the interactive seed-setup task, claude
starts and idles in tmux awaiting the operator to attach and `/login`.

### Completion detection (unchanged invariant)

The `DONE`/`ERROR`-to-`optio.log` wrapper stays **inside the tmux command**, so it
fires when claude exits exactly as today. The protocol driver continues to tail
`optio.log` and complete/fail the session on `DONE`/`ERROR`. Premature-exit
detection is preserved: an unexpected claude exit appends `ERROR: claude exited
<rc>` → the driver catches it.

Completion is therefore **process-driven (optio.log), never connection-driven** — a
viewer disconnect produces no terminal line and cannot end the task.

The session body now awaits the **claude process inside tmux** (e.g. poll
`tmux -S <sock> has-session -t optio`) rather than the ttyd child. ttyd stays up
serving viewers for the task's whole life; its lifetime no longer defines the
task's.

### Teardown

The session `finally` now tears down **both** processes:

1. `tmux -S <workdir>/tmux.sock kill-session -t optio` (equivalently kill the
   socket's server) — stops claude.
2. Terminate the ttyd subprocess (as today).

The cancel path applies SIGKILL semantics to both. The per-task socket lives under
the workdir, so it is removed with the workdir during cleanup.

Edge: if claude exits while viewers are attached, the tmux session closes; attached
ttyd clients see "session ended" and detach — harmless, since completion was
already registered via `optio.log`.

### Resume and multi-viewer

- **Resume** is unchanged in mechanism. The snapshot still captures the workdir; on
  resume the workdir is restored and the tmux wrapper runs `claude --continue` (the
  existing resume `claude_flags`). The prior task's tmux session/socket was killed
  at its teardown, so resume creates a fresh detached session on a fresh per-task
  socket. Only the launch is now tmux-wrapped.
- **Multi-viewer** follows from dropping `-m 1`: N ttyd clients attach to the one
  tmux session and share the live TUI.

### Code shape

`build_ttyd_argv` splits into two focused builders in
`optio_claudecode/host_actions.py`:

- `build_tmux_session_argv(...)` — the detached `tmux new-session` argv running the
  bash+claude wrapper (env, netns seal, DONE/ERROR append moved here verbatim).
- `build_ttyd_attach_argv(...)` — the ttyd argv that runs `tmux attach` (no `-m 1`).

`launch_ttyd_with_claude` becomes a launch that (a) starts the detached tmux session,
(b) starts ttyd attaching to it, and returns handles/identifiers for both so the
session can await claude and tear both down. A `_require_tmux(host)` helper performs
the provisioning check.

## Testing

Use **real tmux** (present at `/usr/bin/tmux` in dev/CI) with the existing
fake-claude harness as the tmux session command; ttyd stays shimmed/real as today.
The tmux lifecycle is the point — exercising it for real beats mocking it.

- **Unit (pure):** `build_tmux_session_argv` + `build_ttyd_attach_argv` — assert env
  assignments, the netns seal, the `DONE`/`ERROR` `optio.log` wrapper, the per-task
  socket path, the session name, and that `-m 1` is gone from the ttyd argv.
- **Integration (real tmux + fake claude):**
  1. **Detached immediate start** — after launch, the tmux session exists *before any
     viewer connects*.
  2. **Completion** — fake claude writes a deliverable and the wrapper appends `DONE`
     → the driver completes; **teardown leaves no tmux session/socket**.
  3. **Regression test for this bug** — kill the ttyd handle (a proxy for "viewer
     disconnected") and assert the **tmux session / claude survives**, decoupled from
     the connection. This directly pins the fix.
- **`_require_tmux`:** monkeypatch the host so `command -v tmux` fails → asserts the
  clear error.

## Risks

- **tmux prerequisite.** Workers without tmux fail fast with a clear error; the prod
  container image and the demo docs must install tmux. Acceptable per the
  provisioning decision.
- **Shared-terminal write contention.** Multiple attached viewers share one TUI;
  concurrent typing conflicts. Accepted as inherent to terminal sharing ("N
  observers", one driver).
- **tmux version skew.** `new-session -x/-y` sizing and `attach` flags are stable
  across tmux 3.x; pin behavior to documented flags only.
