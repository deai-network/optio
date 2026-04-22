# optio-opencode — Design Specification

**Base revision:** `208973097c480d0af61caa5e4e0591bb36ebd707` on branch `main` (as of 2026-04-22T12:05:40Z)

**Date:** 2026-04-22
**Status:** Draft (supersedes the 2026-04-21 seed spec)

**Companion documents:**

- `docs/2026-04-21-optio-opencode-seed-spec.md` — prior seed, superseded by this document for the same design.
- `docs/2026-04-21-optio-opencode-notes.md` — audit of opencode's web interface and rejected alternatives; still useful reference.
- `docs/2026-04-21-optio-widget-extensions-design.md` — the optio primitives (`uiWidget`, widget proxy, `widgetData`, generic iframe widget) that this spec consumes.

---

## 1. Purpose & Scope

**What it is.** `optio-opencode` is a Python library that lets a consumer (windage, etc.) run an `opencode web` session as an optio task — either as a local subprocess or on a remote host over SSH — with opencode's UI embedded in the optio dashboard via the widget proxy.

**What it does for the consumer.**

1. Provisions a workdir on the opencode host.
2. Writes `AGENTS.md` (base prompt + consumer's instructions) and `opencode.json` (consumer's config) into it.
3. Installs opencode on the host if needed (remote only; local expects pre-install).
4. Launches `opencode web` there with a random auth password.
5. Sets up the widget proxy registration: `widgetUpstream` = `http://127.0.0.1:<port>` (worker-local; SSH tunnel hidden) with Basic-auth inner credentials; `widgetData` = `localStorageOverrides` so opencode's SPA talks to the proxied URL.
6. Tails a log file the LLM writes to (keyword-prefixed) and feeds:
   - `STATUS:` → `ctx.report_progress(percent, message)`
   - `DELIVERABLE: <path>` → SFTP the file back; invoke consumer callback with decoded text
   - `DONE [summary]` → clean completion; summary passed through the log channel
   - `ERROR [message]` → failure
7. Cleans up (workdir removed, SSH closed) on normal teardown.

**In scope (MVP).**

- Local and remote-via-SSH modes (indistinguishable from optio's side).
- File-based deliverables via `DELIVERABLE:` keyword + SFTP.
- Fail-fast on SSH drop.

**Out of scope / deferred.**

- Phase-2: post-disconnect cleanup child-process that retries SSH to remove leftover files.
- SSH reconnect with backoff; consumer-configurable failure policies.
- Version-pinned opencode; auto-install in local mode.
- ssh-agent, inline keys, password auth for SSH.
- Multiple deliverables directories; binary deliverables.
- Idle-timeout-based termination.
- Subclass / mixin consumer-interface variants.

---

## 2. Architecture Overview

**Layering.** Three layers, already established in the widget-extensions spec:

```
optio-core        — ProcessContext, TaskInstance, widget primitives
optio-opencode    — this spec: orchestrates opencode web via optio-core's primitives
windage (and other end consumers) — compose optio-opencode with domain behavior
```

`optio-opencode` depends only on `optio-core` (Python) + `asyncssh` (remote mode). It does not depend on `optio-api`, `optio-ui`, or `optio-contracts`; everything it talks to is via `ProcessContext` and the generic iframe widget already shipped in optio-ui.

**Operating modes (indistinguishable from optio's side).**

- **Local subprocess.** `asyncio.create_subprocess_exec("opencode", "web", "--port=0", "--hostname=127.0.0.1", cwd=workdir, env={OPENCODE_SERVER_PASSWORD: <random>, ...})`. Workdir is a fresh `tempfile.mkdtemp()` directory. Upstream URL passed to optio is `http://127.0.0.1:<port>`.
- **Remote via SSH.** Single `asyncssh` connection multiplexing: command exec (install / launch / teardown / `tail -F`), local port forward (`forward_local_port("127.0.0.1", 0, "127.0.0.1", <remote_port>)`), SFTP (write AGENTS.md, write opencode.json, fetch deliverables). Workdir is a fresh `/tmp/optio-opencode-<uuid>/` on the remote. Upstream URL passed to optio is still `http://127.0.0.1:<worker_local_port>` — the SSH tunnel is invisible to optio-api.

**Single long-running asyncio task per session.** The task function runs a state machine: provision → write config files → install-if-missing → launch → wait-for-ready → set upstream + widget data → run log-tailer loop + watch-subprocess loop → teardown. The log-tailer does SFTP + callback invocation as `DELIVERABLE:` lines arrive. Termination conditions: `DONE` line seen, `ERROR` line seen, opencode subprocess exits on its own, or `ctx.should_continue()` returns False.

---

## 3. Consumer Interface

The public surface is a **task factory** that returns a `TaskInstance`. Consumers build it with a config object; that config is the only thing a consumer needs to understand.

```python
# optio_opencode/__init__.py

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

DeliverableCallback = Callable[[str, str], Awaitable[None]]
# (remote_path, decoded_text) -> None.
# Invoked once per successfully-fetched DELIVERABLE. remote_path is the
# path the LLM wrote in the log line (typically under the workdir's
# deliverables subdir); text is the file's contents decoded as UTF-8.


@dataclass
class SSHConfig:
    host: str
    user: str
    key_path: str                    # path on the worker's FS
    port: int = 22
    # known_hosts verification is disabled for MVP.


@dataclass
class OpencodeTaskConfig:
    # Prompting
    consumer_instructions: str
        # What the LLM should actually do. Appended to optio-opencode's base
        # prompt (the STATUS / DELIVERABLE / DONE / ERROR conventions + a
        # framing section) and written to AGENTS.md in the workdir.

    opencode_config: dict[str, Any] = field(default_factory=dict)
        # Serialized verbatim to `opencode.json` in the workdir. Consumer
        # controls model, agents, tools, permissions, MCP, provider, plugins.
        # optio-opencode does not interpret this.

    # Where opencode runs
    ssh: SSHConfig | None = None     # None = local subprocess

    # Deliverables
    on_deliverable: DeliverableCallback | None = None

    # Install behavior
    install_if_missing: bool = True  # remote only; local always expects pre-install


def create_opencode_task(
    process_id: str,
    name: str,
    config: OpencodeTaskConfig,
    description: str | None = None,
) -> TaskInstance:
    """Return a TaskInstance that runs opencode web per `config`.

    The returned TaskInstance has ui_widget="iframe" and an execute function
    that owns the full opencode lifecycle described in Sections 4-5.
    """
    ...
```

**Notes on the shape.**

- **Text-only deliverables.** Callback signature is `(str, str)`. If a deliverable isn't UTF-8-decodable, optio-opencode logs an error via `ctx.report_progress(None, ...)` and skips the callback. No binary variant in MVP.
- **One callback per task instance.** No multiplexing across paths; the consumer discriminates inside the callback if needed. Keeps the interface narrow.
- **`opencode_config` is a passthrough dict.** optio-opencode does not validate it; opencode will. API keys for a provider can go here; see Section 7 for the security tradeoff.
- **`ui_widget="iframe"`** is baked in by the factory. A future consumer that wants a custom widget can register it and still use most of optio-opencode's machinery, but that's not an MVP knob.
- **No per-task `workdir` override.** optio-opencode picks a fresh workdir per session (Section 4).

**Example usage (pretend-windage):**

```python
from optio_opencode import OpencodeTaskConfig, SSHConfig, create_opencode_task

async def on_file(path: str, text: str) -> None:
    print(f"got {path}: {len(text)} chars")

config = OpencodeTaskConfig(
    consumer_instructions="You are a research agent. Explore X and write a summary.",
    opencode_config={"model": "anthropic/claude-sonnet-4-6"},
    ssh=SSHConfig(host="worker-42.example.com", user="runner", key_path="/secrets/id_ed25519"),
    on_deliverable=on_file,
)

task = create_opencode_task(
    process_id="research-opencode",
    name="Research session (opencode)",
    config=config,
    description="Run opencode on worker-42 for the research task",
)
```

---

## 4. Workdir Layout & Filesystem Contract

**Where the workdir lives.**

- **Local mode:** `tempfile.mkdtemp(prefix="optio-opencode-")`. System tmp-cleanup rules apply; we also clean explicitly on teardown.
- **Remote mode:** `/tmp/optio-opencode-<uuid>/` on the remote host, created via SFTP `mkdir` during setup.

**What's inside the workdir.**

```
<workdir>/
├── AGENTS.md                # optio-opencode base prompt + consumer_instructions
├── opencode.json            # JSON-serialized config.opencode_config
├── optio.log                # keyword-prefixed log file (LLM appends to this)
└── deliverables/            # deliverable files (LLM writes here by convention)
```

**How opencode learns these paths.**

Via the base prompt in `AGENTS.md`. optio-opencode prepends a boilerplate block like:

```markdown
# Coordination protocol with the host (optio-opencode)

You are running inside a coordination harness. Follow these conventions
throughout the session.

## Log channel

Append one line per entry to `./optio.log` in this directory. Each line
must start with one of:

- `STATUS:` — progress update for the human. Optional leading percent,
  e.g. `STATUS: 50% counting my fingers`.
- `DELIVERABLE:` — absolute or workdir-relative path to a file you've
  just produced, e.g. `DELIVERABLE: ./deliverables/summary.md`.
- `DONE` — you have finished the task. May be followed by an optional
  summary on the same line: `DONE: wrote the report`.
- `ERROR` — you cannot continue. May be followed by an optional
  message: `ERROR: provider auth failed`.

After writing `DONE` or `ERROR`, the session will terminate. Do not
write further lines.

## Deliverables

Place files you want to hand back to the host under `./deliverables/`.
For each file, write a `DELIVERABLE:` log line *after* the file exists
and its contents are final. The host fetches files by reading these
log lines.

## Task

Here comes the description of your actual task to complete. Throughout
the task, you are encouraged to narrate progress — both on the normal
UI and in parallel using the `STATUS:` messages explained above — and
you are free to ask questions and dialogue with the human. They are
also working on the same task and will cooperate with you on achieving
the same goals. So:
```

Then a blank line and the consumer's `consumer_instructions` follows verbatim.

**Path handling for `DELIVERABLE:`.**

- Interpreted relative to the workdir if not absolute.
- The resolved path is validated to live inside `<workdir>/` (refuses paths escaping via `..` or pointing elsewhere on the filesystem) — small defense against prompt-induced filesystem shenanigans.
- Paths outside `<workdir>/deliverables/` but inside `<workdir>/` are still fetched; the "deliverables dir" is a convention for the LLM, not an enforcement boundary. Detection is by keyword, not by directory.

**Cleanup scope.**

On teardown, optio-opencode removes `<workdir>` entirely. This includes `optio.log`, `AGENTS.md`, `opencode.json`, and `deliverables/`. opencode's own SQLite session DB lives under XDG config on the host, is shared across sessions, and is **not** touched by optio-opencode.

---

## 5. Runtime Lifecycle

Concrete sequence inside the task's `execute` function:

```
1. Setup
   - Connect SSH (remote) or prepare local state.
   - mkdir <workdir>.
   - Generate OPENCODE_SERVER_PASSWORD (secrets.token_urlsafe(32)).
   - Write AGENTS.md (base prompt + consumer_instructions).
   - Write opencode.json (config.opencode_config, JSON-serialized).
   - mkdir <workdir>/deliverables.
   - touch <workdir>/optio.log.

2. Install (remote, if install_if_missing and opencode not on PATH)
   - Run `curl -fsSL opencode.ai/install | bash` via SSH exec.
   - Verify `opencode --version` succeeds; abort if not.

3. Launch
   - Start `opencode web --port=0 --hostname=127.0.0.1` in the workdir,
     with OPENCODE_SERVER_PASSWORD in env.
   - Local: asyncio.create_subprocess_exec(..., cwd=workdir, env=...).
   - Remote: conn.create_process(
       "cd <workdir> && OPENCODE_SERVER_PASSWORD=... opencode web --port=0 --hostname=127.0.0.1"
     ).

4. Detect readiness
   - Read the subprocess's stdout line-by-line with a timeout (default 30s).
   - Parse the listening URL from the first line that looks like an HTTP URL.
   - On timeout, transition to failed with a specific error.

5. Register with optio
   - Remote only: open local port forward —
       worker_port, _ = await conn.forward_local_port(
           "127.0.0.1", 0, "127.0.0.1", remote_port
       ).
     Local mode reuses the local subprocess port directly; call it worker_port
     uniformly in what follows.
   - await ctx.set_widget_upstream(
         f"http://127.0.0.1:{worker_port}",
         inner_auth=BasicAuth(username="opencode", password=<token>),
     )
   - await ctx.set_widget_data({
         "localStorageOverrides": {
             "opencode.settings.dat:defaultServerUrl": "{widgetProxyUrl}",
         },
     })
     (The iframe widget substitutes `{widgetProxyUrl}` at mount time;
     see Section 5a.)

6. Run
   - Spawn three asyncio tasks:
     a. Log-tail loop: read optio.log line-by-line (SSH-exec `tail -F -n 0`
        for remote; aiofiles poll loop for local). For each line, dispatch
        by keyword; DELIVERABLE lines are enqueued onto the fetch queue
        (see Section 6).
     b. Deliverable-fetch loop: consume the bounded fetch queue, SFTP-GET
        each file, decode UTF-8, invoke on_deliverable (Section 6).
     c. Subprocess watcher: await opencode's exit; on exit, signal the
        main loop to finish.
   - Also watch ctx.should_continue() via an asyncio.wait on the
     cancellation flag.

7. Finish
   - A DONE line, an ERROR line, opencode exiting, or cancellation all
     lead to teardown.
   - DONE with 0 subprocess exit → done.
   - ERROR → failed with the ERROR message as the error string.
   - Subprocess exited non-zero before DONE → failed.
   - ctx cancellation → cancelled (optio-core handles the state transition).

8. Teardown (finally block; always runs on completion or failure)

   Two modes:

   a. Normal (not cancellation-driven):
      - Terminate opencode subprocess (SIGTERM, wait 5s, SIGKILL).
        [Lets opencode flush its final bytes to optio.log first.]
      - Cancel log-tail task; drain any remaining lines.
      - rm -rf <workdir> (SFTP removal for remote; shutil.rmtree for local).
      - Close port forward, SFTP, and SSH connection.

   b. Cancellation (ctx.should_continue() returned False):
      - SIGKILL opencode immediately — skip the polite SIGTERM + wait.
      - Local: shutil.rmtree(workdir) (fast, filesystem-local).
      - Remote: fire-and-forget `rm -rf <workdir>` (SSH exec); close the SSH
        connection without waiting for the rm to complete. asyncssh's session
        cleanup handles any tail end.
      - optio-core's shutdown grace period (default 5 s) is the budget;
        aggressive teardown is designed to fit inside it.

   Either way, optio-core clears widgetUpstream and widgetData per its
   normal terminal-state rules.
```

### 5a. The `widgetProxyUrl` wrinkle

Opencode's SPA uses `location.origin` to talk to its server (per `entry.tsx` — see the companion notes file). When served under `/api/widget/<db>/<prefix>/<processId>/`, `location.origin` strips the subpath, and requests are lost. The fix is `localStorage["opencode.settings.dat:defaultServerUrl"]` set to the full widget proxy URL *before* the iframe loads.

The worker does not know `apiBaseUrl`, `database`, `prefix`, or `processId` at runtime. The iframe widget (optio-ui) already receives `widgetProxyUrl` in `WidgetProps`. Solution:

- **Extend `IframeWidget.tsx` with simple template substitution on `widgetData.localStorageOverrides` values.** Any occurrence of `{widgetProxyUrl}` in a value is replaced with `props.widgetProxyUrl` before `localStorage.setItem`. Other tokens pass through unchanged.
- The worker writes `{"opencode.settings.dat:defaultServerUrl": "{widgetProxyUrl}"}` and the UI resolves it at mount time.
- Marimo is unaffected: it doesn't use `localStorageOverrides` at all because its client uses relative URLs.

This is a ~10-line change to an existing optio-ui component plus a component test. It lives in `optio-ui` but is prerequisite for optio-opencode end-to-end. Tracked as in-scope for the implementation plan (see Section 11a).

---

## 6. Log-Tail & Deliverable Fetch Mechanics

**Tailing — remote.** `tail -F -n 0 <workdir>/optio.log` executed over the single asyncssh connection as a secondary exec channel. `-F` (capital) survives log-rotation / file recreation; `-n 0` starts at EOF so we don't re-process bytes from a truncated earlier run. Lines are read from the process's stdout stream (`await process.stdout.readline()` in a loop).

**Tailing — local.** `aiofiles` open + read-at-offset poll loop with a small sleep (default 100 ms). Same line-parsing logic.

**Line parsing.** Each line is stripped and matched against a small set of regexes, in order:

```python
RE_STATUS      = re.compile(r"^STATUS:\s*(?:(\d{1,3})%\s+)?(.*)$")
RE_DELIVERABLE = re.compile(r"^DELIVERABLE:\s*(.+?)\s*$")
RE_DONE        = re.compile(r"^DONE(?::\s*(.*))?\s*$")
RE_ERROR       = re.compile(r"^ERROR(?::\s*(.*))?\s*$")
```

Dispatch:

- **STATUS** → `ctx.report_progress(int(pct) if pct else None, msg)`. Percent >100 clamps to 100; <0 is treated as `None`.
- **DELIVERABLE** → enqueue `(remote_path, seq)` onto the fetch queue (see below). Also `ctx.report_progress(None, f"Deliverable: {path}")` so the user sees the notification in the log channel.
- **DONE** → set `done_flag`; optional summary recorded via `ctx.report_progress(None, summary)`.
- **ERROR** → set `failed_flag` with optional message; main loop raises after teardown.
- **Unmatched line** → forward verbatim via `ctx.report_progress(None, line)`. The LLM occasionally writes stray text; better surfaced than lost.

**Deliverable fetching.** A single background coroutine consumes a bounded `asyncio.Queue` of paths to fetch. Per entry:

1. Resolve path against workdir (already validated on-parse).
2. SFTP-GET the file into an in-memory buffer (remote) or `aiofiles`-read (local).
3. Try `content.decode("utf-8")`. If decode fails, log an error (`ctx.report_progress(None, f"Deliverable {path}: not valid UTF-8, skipping callback")`) and skip step 4.
4. `await config.on_deliverable(path, text)`. Exceptions from the callback are caught and logged at error level; they do not fail the task (the LLM keeps running; the consumer's callback failing is not a fatal session failure).

**Ordering guarantee.** Fetch happens in log-line order. Callbacks are awaited sequentially (one at a time), so the consumer can assume `on_deliverable` invocations for a single session do not overlap.

**Backpressure.** The fetch queue is bounded (default 64). If opencode emits deliverables faster than the fetch loop can handle, the log-tail coroutine blocks on `queue.put`, slowing log draining. Acceptable for MVP; the alternative would be unbounded growth, which is worse.

---

## 7. Provider Credentials & opencode-on-Host Auth

opencode itself needs provider credentials (Anthropic, OpenAI, etc.) to call an LLM. Two paths, both supported:

- **Consumer pre-authenticates opencode on the host.** E.g., they run `opencode auth login` out-of-band on each worker. Credentials live in opencode's own keyring / file. optio-opencode does nothing — opencode picks them up on launch.
- **Consumer passes provider credentials in `opencode_config`.** The passthrough `opencode_config` dict (Section 3) can include a `provider` block with API keys. optio-opencode serializes it into `opencode.json` in the workdir. The key lives on disk for the session's duration; workdir is removed on teardown so it's ephemeral there.

**Security note for path 2:** the API key ends up in the process params → the MongoDB process document → anywhere optio-core logs task params. A consumer nervous about that picks path 1. This is the consumer's call; optio-opencode is not in the business of managing credentials.

optio-opencode does **not** manage opencode's own auth state (XDG config, opencode's keyring). That's considered host-managed.

---

## 8. Failure Modes

| Failure | optio-opencode behavior | Required from consumer |
|---|---|---|
| SSH connect fails at launch | Transition to `failed` with clear error ("SSH connect to X failed: Y"). No workdir created. | Check credentials / host reachability. |
| opencode not installed and install fails (remote) | Transition to `failed`. Install script stderr captured into the log channel before fail. | Pre-install on the host or fix install. |
| opencode launched but does not print a URL within readiness timeout | SIGKILL opencode subprocess; attempt workdir cleanup; transition to `failed`. | Usually a config problem (bad `opencode.json`). Captured stdout/stderr is surfaced as log entries for debugging. |
| opencode exits non-zero before `DONE` is seen | Final log lines drained; workdir cleaned; transition to `failed`. Exit code included in the error message. | Usually a crash in opencode; user inspects the captured logs. |
| LLM writes `ERROR:` | Transition to `failed` with the `ERROR:` message as the error string. Normal teardown (terminate opencode, cancel tail, cleanup workdir). | The LLM self-reported failure; consumer decides whether to relaunch. |
| LLM writes `DONE` then opencode exits 0 | `done`. | Nothing. |
| LLM writes `DONE` but opencode stalls on termination | optio-opencode sends SIGTERM; if the subprocess doesn't exit within 5 s, SIGKILL. Still transitions to `done`. | Nothing. |
| Unknown keyword in a log line | Forwarded verbatim via `ctx.report_progress(None, line)`. Not an error. | — |
| `DELIVERABLE:` path escapes workdir | Log entry "invalid deliverable path X, skipping"; task continues. | — |
| `DELIVERABLE:` file not UTF-8-decodable | Log entry "deliverable X is not valid UTF-8, skipping callback"; task continues; no callback invocation. | If binary deliverables are needed, that's a future config option. |
| Consumer's `on_deliverable` raises | Exception caught; logged at error level. Task continues. | Consumer-side bug; the session goes on. |
| SSH connection drops mid-session | Fail-fast. Transition to `failed`. Remote workdir likely not cleaned (leftover on remote host). Phase-2 deferred feature will spawn a cleanup child-process. | Phase 2: workdir persists until cleanup retry succeeds. MVP: consumer may manually clean the remote. |
| optio process cancelled by user | `ctx.should_continue()` returns False; main loop breaks to aggressive teardown (Section 5, step 8b). Process state → `cancelled` per optio-core. | Nothing. |
| Worker shuts down gracefully (SIGTERM), task unwinds within grace | Cancellation-teardown path; SIGKILL opencode, fire-and-forget `rm -rf` + close SSH, transition to `cancelled`. Workdir usually removed on remote, but not guaranteed over slow links. | Nothing. |
| Worker shuts down gracefully, task exceeds grace period | optio-core's `_force_finalize_stuck_processes` transitions the row to `failed` and clears `widgetUpstream`. optio-opencode's teardown may be partial when the event loop stops; workdir may be orphaned on the remote. | Phase 2 sweeps leftovers; MVP: sysadmin. |
| Worker hard-killed (SIGKILL / OOM / power) | No teardown runs. DB row stays in its active state. Next server start, `_reconcile_interrupted_processes` transitions to `failed` and clears `widgetUpstream`. Remote workdir + remote opencode process are **orphaned**. | Phase 2 or sysadmin. |

**Grace-period note.** optio-opencode sessions may routinely exceed optio-core's default 5 s shutdown grace. If that becomes a problem in practice, the host app can call `shutdown(grace_seconds=30)` when wiring up signal handlers. This is deployment guidance; no change to optio-opencode itself.

---

## 9. Testing Approach

### Unit tests (pure Python, no subprocess, no network)

- **Line parser** — `parse_log_line()` round-trips for every keyword variant, percent parsing, unmatched-line fallback, path-escape-workdir guard.
- **Prompt composer** — `compose_agents_md(consumer_instructions)` produces exactly the expected text (coordination boilerplate + `## Task` framing + consumer content). Golden-string test.
- **Workdir layout planner** — `plan_workdir_paths(workdir)` returns the correct filenames for log, deliverables dir, AGENTS.md, opencode.json.
- **Config serialization** — `opencode_config` dict serializes to JSON identical to what we'd write. Trivial but cheap insurance.

### Local-mode integration test (no SSH, no real opencode)

A test double replaces `opencode web`:

```python
# A tiny Python script that optio-opencode launches instead of real opencode.
# It binds to 127.0.0.1 on --port, prints the URL to stdout, then appends a
# scripted sequence of lines to optio.log at intervals and touches files in
# deliverables/ to exercise DELIVERABLE fetching.
```

Scenarios:

- **Happy path.** `STATUS:` → `DELIVERABLE: <path>` → `DONE`. Assert: `ctx.report_progress` received the statuses, `on_deliverable` was called once with the right text, task transitions to `done`, workdir is removed.
- **ERROR path.** Task transitions to `failed`; error message surfaces. Workdir cleaned.
- **Cancellation.** `ctx.should_continue()` returns False mid-session; aggressive teardown path taken; workdir cleaned. Callback not invoked for unseen deliverables.
- **Subprocess exits 0 before DONE.** `failed` with clear error.
- **Invalid deliverable path (escapes workdir).** Logged; task continues; `on_deliverable` not invoked for that one.
- **Non-UTF-8 deliverable.** Logged; task continues; `on_deliverable` not invoked.
- **Consumer's `on_deliverable` raises.** Caught; logged; task continues.

### Remote-mode integration test

Spin up an SSH server in a Docker container (`linuxserver/openssh-server` or equivalent) exposed on localhost. Same `asyncssh` client connects as if remote. Re-runs the happy-path + cancellation scenarios through the SSH path (port forward, exec, SFTP). Much smaller than a real remote worker; enough to catch "does the SSH code path work at all."

**Cleanup property.** Every test that spins up a subprocess must clean up even on failure. Tests use `tempfile.mkdtemp` fixtures that auto-delete; the test-double `opencode` script handles SIGKILL cleanly.

---

## 10. Reference Demo Task

### Purpose

A human-driven, end-to-end validation of the optio-opencode stack, buildable using only optio-opencode + optio-demo. Ships in `optio-demo` alongside the existing marimo reference task. A human launches it from optio-dashboard and confirms the whole stack works: workdir setup, opencode launch, widget registration, iframe embed through the proxy (with the `{widgetProxyUrl}` localStorage override resolving), keyword-prefixed log interpretation, `on_deliverable` callback delivery, `DONE` termination, and cleanup.

This is **not** an automated smoke test. It is a minimal reference consumer demonstrating how a real consumer (e.g., windage) would wire up optio-opencode. It is exercised manually by the human, who also has to interact with opencode through the iframe.

### Shape of the task

Lives in `packages/optio-demo/src/optio_demo/tasks/opencode.py`, next to `marimo.py`.

```python
@task(name="opencode-demo", ui_widget="iframe")  # factory-produced TaskInstance
async def opencode_demo(ctx: ProcessContext) -> None:
    # Built via optio_opencode.create_opencode_task(...) with:
    # - consumer_instructions: "Ask the human about their favorite color,
    #   then ship a deliverable containing the number 42 and the designated
    #   color. Then signal that you have finished."
    # - opencode_config: {"model": <sensible default, e.g., anthropic/claude-sonnet-4-6>}
    # - ssh: None  (local mode; matches marimo's simplicity)
    # - on_deliverable: prints (path, text) to the optio log via ctx
    ...
```

Local mode keeps the demo's prerequisites minimal: the developer must already have opencode installed and authenticated on their machine (`opencode auth login` run at least once for the chosen provider).

### Primitives exercised

- **Widget registry + generic iframe widget** — the factory sets `ui_widget="iframe"`, so optio-ui's generic iframe widget renders opencode.
- **Widget upstream proxy** — optio-opencode registers a worker-local upstream (loopback in local mode); optio-api proxies HTTP + SSE + WS.
- **Widget data + `{widgetProxyUrl}` template substitution** — optio-opencode publishes `localStorageOverrides` containing the template token; the iframe widget resolves it at mount time. Without this, opencode's SPA would hit the wrong origin and the demo would not work.
- **Inner auth injection** — optio-opencode generates `OPENCODE_SERVER_PASSWORD` and registers `BasicAuth` as `innerAuth`; optio-api injects it on every proxied request and WebSocket upgrade.
- **Log tail + keyword vocabulary** — `STATUS:` lines surface as progress updates in the dashboard log pane; `DELIVERABLE:` triggers the callback; `DONE` drives clean termination.
- **Deliverable fetch + callback** — the callback receives the LLM's produced text file and prints it back into the optio log channel, giving the human visual confirmation that the round-trip worked.

### User-verifiable walkthrough

1. Start MongoDB (Docker), optio-dashboard, and optio-demo locally.
2. Authenticate in the dashboard.
3. Launch the `opencode-demo` task.
4. Open the process detail view; the iframe widget mounts and opencode's UI loads (via the proxy, with the subpath routing resolved).
5. The LLM asks in the iframe: "What is your favorite color?" (or equivalent phrasing).
6. Answer through opencode's input in the iframe.
7. Watch `STATUS:` log lines flow into the dashboard's log pane as the LLM works.
8. When the LLM writes the deliverable, confirm `on_deliverable` was invoked by observing the callback-produced log line in the dashboard. The deliverable text should contain both `42` and the color the human supplied.
9. The LLM writes `DONE`; the task transitions to `done`; the iframe shows the "session ended" banner; the workdir is cleaned up.
10. Dismiss the process; widgetUpstream and widgetData are cleared per optio-core's lifecycle rules.

### Why local-only for the demo

Remote mode requires a second host + SSH key setup per demo user, making the demo significantly less friendly. Local mode exercises all four primitives except the SSH-specific transport. The remote-mode integration test in Section 9 covers the SSH code path on its own; the demo's job is to prove the user-facing stack works end-to-end, which local mode does without the extra prerequisites.

---

## 11. Deferred / Phase-2 & Other Future Work

Explicitly out of MVP; tracked here so nothing is forgotten:

- **Post-disconnect cleanup child process.** Spawned by optio-opencode on unplanned SSH drop; owns the remote workdir path + credentials, retries SSH on a backoff, runs `rm -rf <workdir>` on success, exits. Uses optio-core's child-process support so it has its own row and log.
- **SSH reconnect with backoff for the live session.** Currently fail-fast. Add when a consumer demonstrably needs resilience over flaky links. Resume-tail-from-offset is the hard part.
- **Consumer-configurable failure policy.** A small policy object in `OpencodeTaskConfig` once there's more than one failure-handling mode.
- **Version-pinned opencode.** An `opencode_version` field honored by install + a runtime check. Not in MVP — any version on the host is accepted.
- **Local-mode auto-install.** Symmetric with remote. Today local expects pre-install.
- **ssh-agent / inline-key / password SSH auth.** Expand `SSHConfig.auth` to a union when needed.
- **Binary deliverables.** Second callback (`on_deliverable_bytes`) or a richer callback taking a dataclass with `content: bytes` + `decoded: str | None`.
- **Idle-timeout termination.** "No log activity for N minutes → treat as DONE (or failed)." Useful for unattended batch use.
- **Consumer-specified workdir.** Adding an override is trivial; waiting for a consumer need.
- **Multiple deliverables directories.** Today's `DELIVERABLE:` paths can point anywhere inside the workdir; that covers most needs.
- **Subclass / mixin interface.** If a consumer needs to hook in more deeply (e.g., modify launch args, customize install), a base class can be introduced. MVP is factory-only.

### 11a. Adjacent optio-ui change (in-scope for the implementation plan)

Because the URL wrinkle (Section 5a) is load-bearing for opencode, the implementation plan that follows this spec must include:

- **Extend `packages/optio-ui/src/widgets/IframeWidget.tsx`** to perform simple template substitution on `widgetData.localStorageOverrides` values — if a value contains `{widgetProxyUrl}`, it's replaced with `WidgetProps.widgetProxyUrl` at mount time. Other template tokens are left untouched. No impact on existing consumers (marimo passes empty `localStorageOverrides`).

This is a ~10-line change plus a component test. It lives in `optio-ui` but is prerequisite for optio-opencode functioning end-to-end. Recording it here so it isn't forgotten between spec and plan.
