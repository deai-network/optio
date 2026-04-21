# optio-opencode — Seed Specification

**Base revision:** `aa1c234796be6096c66031f094be57f8b6223a9e` on branch `main` (as of 2026-04-21T01:47:06Z)

**Date:** 2026-04-21
**Status:** Seed

**Purpose of this document:** a jumping-off point for a later dedicated brainstorming session on optio-opencode. Intentionally light; many decisions are deferred to that session. Companion notes file `2026-04-21-optio-opencode-notes.md` captures points raised during brainstorming but deliberately left out of this seed.

---

## What it is

A reusable Python helper that orchestrates `opencode web` as a backing process for an optio task, exposing opencode's web UI as the task's widget via the optio extension points defined in `2026-04-21-optio-widget-extensions-design.md`.

Consumers (e.g., windage) supply the system prompt, any tool / capability constraints, and the logic for shipping deliverables back; optio-opencode handles install, launch, tunneling, monitoring, and teardown.

## Operating modes

Two modes; the consumer picks per task instance.

1. **Local subprocess.** opencode runs on the worker machine (installed ahead of time or on first use). Started via `asyncio.create_subprocess_exec`. Upstream URL passed to optio is `http://127.0.0.1:<port>`.
2. **Remote via SSH.** `asyncssh` connects to a consumer-provided host. opencode is installed there if missing, started with port bound to the remote's `127.0.0.1`, and a local port forward brings the remote port to the worker. The upstream URL passed to optio is still a worker-local `http://127.0.0.1:<localport>` — the SSH tunnel is invisible to optio-api.

From optio's perspective the two modes are indistinguishable.

## Core responsibilities

1. **Install** opencode on the target host if not present. Version pinning strategy TBD.
2. **Launch** `opencode web` with the consumer's system prompt and any CORS / binding flags. Auth token handling TBD (always-set + proxy-injected, or never-set + network isolation).
3. **Detect readiness** by watching opencode's stdout for its listening URL; fail fast with a timeout.
4. **Publish widget state** via `ctx.set_widget_upstream(url, inner_auth=...)` and `ctx.set_widget_data({...})`. The latter doubles as the iframe widget's go-live signal; its contents carry any `localStorageOverrides` opencode's client needs (notably base-URL override for subpath routing).
5. **Monitor** opencode: propagate its exit to the optio process (clean → `done`; non-zero / crash → `failed`).
6. **Teardown** on optio cancellation or completion: terminate opencode; close the SSH tunnel and disconnect if remote. Upstream clearing itself is handled by optio-core's teardown path.
7. **Expose an interface** that lets consumers supply configuration and observe opencode's session for downstream processing.

## Interface sketch

Not committed to. Three candidates for the brainstorm:

- **Task factory.** `create_opencode_task(name, system_prompt, host=None, ...) -> TaskInstance`.
- **Base class / mixin.** Consumers subclass; override hooks for domain behavior.
- **Runner + callbacks.** One generic `TaskInstance` accepting a config object and a small callback set (`on_session_event`, `on_session_end`, …).

Choice depends on how windage ends up consuming optio-opencode.

## Dependencies

- **optio-core** — `ProcessContext`, `TaskInstance`, widget primitives.
- **asyncssh** — remote mode only.
- **opencode binary** — managed by optio-opencode (install + version pin).
- **Python 3.11+** (optio-core baseline).

## Questions to resolve in the brainstorming session

1. Install strategy: vendored binary, `npm install -g opencode-ai`, curl-installer, or require consumer to pre-install? Version pinning?
2. System-prompt delivery: `--prompt`, `--prompt-file`, stdin?
3. Port selection: `--port=0` + stdout parse, or pre-selected? Local-mode collisions?
4. SSH credentials: on-disk keys, agent, inline? How do consumers pass them through task params safely?
5. The process for shipping deliverables from opencode back to the process: REST polling against opencode's API? Reading its SQLite directly? A separate SSH exec of opencode's CLI against the same session? Push via a custom tool the LLM calls? Using SCP or SFTP to copy files back from the remote host to the worker over the existing SSH connection (piggybacks on the asyncssh connection already open for the tunnel)?
6. SSH / tunnel failure policy: consumer-configurable vs. opinionated default (reconnect-with-backoff vs. fail-fast)?
7. Auth token for opencode: always-set + proxy-injected (defense-in-depth) or never-set (network-isolation is the boundary)?
8. Install scope: does local mode even try to install opencode, or require it pre-installed?
9. Interface shape (factory / mixin / callbacks).

## Out of scope

- Any windage-specific behavior.
- The UI widget (uses optio-ui's generic iframe widget).
- optio-opencode's release / versioning cadence (deferred until first consumer ships).

## Next step

A dedicated brainstorming session turns this seed into a full design spec, followed by writing-plans and implementation. The reference marimo task in optio-demo validates the extension points it depends on; optio-opencode is the first "real" consumer.
