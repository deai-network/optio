# Claudecode Crash-Orphan Rescue on Resume

This spec was written against the following baseline:

**Base revision:** `dd4906385b72ed3544a0738f64ff4c64e3cbe6b6` on branch `main` (as of 2026-06-08T00:07:19Z)

## Summary

A claudecode task runs claude inside a detached tmux session, fronted by `ttyd`, on a
per-task private tmux socket. When the engine shuts down **gracefully**, the task's teardown
kills that whole tree and captures an encrypted session snapshot, so a later resume restores
clean state.

When the host dies **non-gracefully** (disk-full, OOM, power loss, `SIGKILL`), no teardown
runs and **no snapshot is captured** — but the tmux/ttyd/claude sub-tree is detached from its
parent and **survives the crash**, re-parented to init. Its live, post-last-snapshot state
sits intact on disk in the task workdir. This was confirmed in production on 2026-06-08: after
a disk-full crash and host restart, a manual restart of the task failed with
`tmux new-session failed (exit 1): duplicate session: optio`, because the orphaned tmux server
still held the deterministic session name.

Today, resuming such a task is **destructive**: the resume path restores the last (stale,
pre-crash) snapshot and the driver wipes the workdir — bulldozing the orphan's unsaved work.

This feature makes a resume **detect the surviving orphan, harvest its live state into a fresh
snapshot, then kill it** — all *before* the destructive workdir wipe. The unchanged resume
path then restores that fresh snapshot. Net effect: the work the crash "lost" is recovered.

## Goals

- On resume, automatically detect a crash-surviving claudecode orphan on the task's
  deterministic tmux socket.
- Harvest its live workdir state into a fresh snapshot that is byte-for-byte equivalent to a
  normal teardown capture (same artifacts, same sensitive-data handling) — nothing more, nothing
  less.
- Kill the orphan tree reliably, reusing the existing debug-hardened teardown kill sequence
  rather than a hand-rolled one.
- Be safe under partial failure: never destroy the orphan's on-disk state before a durable
  fresh snapshot exists, and survive a mid-rescue failure so a retried resume still recovers.
- Resolve the `duplicate session: optio` failure as a side effect (the orphan is killed before
  the driver relaunches).

## Non-Goals

- **Live reattach without restart.** Keeping the running claude process and merely reconnecting
  the viewer (no `--continue`, no lost in-flight turn) is the more elegant end-state but is
  deferred to a v2. This spec restarts claude from the freshly-captured snapshot.
- **opencode.** opencode tasks use the same tmux/ttyd pattern and will want the same rescue, as
  a separate later change. This spec covers claudecode only.
- **Orchestration / auto-eligibility.** Whether a hard-crashed (`failed`) task should be made
  *automatically* eligible for resume so rescue fires without human action is the orchestration
  layer's concern — see "Relationship to auto-resume-on-restart" below. This spec only makes a
  resume, however it is triggered (manual today), non-destructive.
- **Idle-wait before capture.** We do not poll for the orphan to be idle before capturing. See
  Decision D2.

## Background — relevant existing machinery

All file:line references are against the base revision, in `packages/optio-claudecode` unless
otherwise noted.

- **Session entry** — `src/optio_claudecode/session.py`. The public session function calls
  `run_log_protocol_session(...)` (session.py:345) with `prepare=_prepare` and `body=_claudecode_body`.
- **Driver lifecycle** — `packages/optio-agents/src/optio_agents/protocol/session.py:107`
  `run_log_protocol_session`. It calls `host.setup_workdir()` (**the destructive wipe**,
  session.py:184) and *then* `prepare(...)` (session.py:186). The driver doc (lines 174-183)
  is explicit: workdir **setup/wipe** is centralized in the driver so no caller can hand-sequence
  it; **teardown** (snapshot capture) is deliberately left as "the caller's bracket around this
  call". This is the seam: the rescue is the symmetric **startup** caller-bracket, running
  *before* the driver call, hence before the wipe.
- **Resume snapshot selection** — `session.py:135-142`. Resume calls
  `load_latest_snapshot(process_id=ctx.process_id)` — **latest wins**. So inserting a fresher
  snapshot before the driver runs is picked up by the unchanged resume path automatically.
- **Capture** — `session.py:569` `_capture_snapshot(ctx, host, *, end_state, workdir_exclude,
  session_blob_encrypt)`. Already fully **workdir-based** (no launch handle / tmux dependency).
  Its ordering is the load-bearing detail:
  1. credentials-present guard (skips degenerate snapshots);
  2. tar `home/.claude` → encrypt → store **session blob** (sensitive storage, **happens first**);
  3. `rm -rf home/.claude` (**expunge**, so the plaintext workdir tar cannot carry secrets);
  4. tar the workdir → store **workdir blob** (plaintext, with `workdir_exclude`);
  5. `insert_snapshot(...)` under `ctx.process_id`; 6. prune.
- **Teardown kill sequence** — `session.py:361-395`, currently **inline** in the `finally`
  block. Four best-effort steps, each annotated with the bug it fixes:
  1. `host.terminate_subprocess(launched_handle, aggressive=cancelled)` — kills ttyd (needs the
     launch handle);
  2. `host_actions._kill_tmux_session(host, tmux_path, tmux_socket, tmux_session)` — SIGHUPs the pane;
  3. `host_actions.kill_claude_processes(host, claude_path)` — **the critical fix**: claude
     ignores the pane SIGHUP, so it must be killed explicitly via a host-side anchored `pkill`;
  4. `host_actions.await_claude_gone(host, claude_path)` — waits for quiescence so the capture
     tar does not race a dying claude ("file changed as we read it").
- **Kill helpers** — `host_actions.py`: `_kill_tmux_session` (kill-session on the private socket),
  `kill_claude_processes` (anchored `pkill -KILL -f` via `_claude_pgrep_pattern`),
  `await_claude_gone` (anchored `pgrep` poll, bounded), `tmux_session_alive` (`has-session`).
- **Deterministic socket** — `host_actions.py:524` `_tmux_socket_path(host)` returns
  `/tmp/optio-cc-{sha256(workdir)[:16]}.sock`; the session name is the fixed default `"optio"`
  (`launch_ttyd_with_claude(..., session_name="optio")`, host_actions.py:574). The same task
  workdir therefore maps to the same socket and session name across resumes — which is exactly
  why the post-crash restart collided with `duplicate session: optio`.
- **pasta wrapping is conditional** — `host_actions.py:428-445`. The netns wrapper
  (`OPTIO_CLAUDECODE_NETNS`, e.g. `pasta --config-net --`) is applied **only** when the env var is
  set *and* `local_mode` is True; otherwise (unset, or remote/SSH) claude runs bare. The orphan
  tree shape is therefore `tmux→bash→pasta→claude` *or* `tmux→bash→claude`. The kill helpers are
  pasta-agnostic by construction: the anchored host-side `pkill` on claude's `argv[0]` reaches it
  in both shapes (pasta isolates only the *network* namespace, not PID).

## Design

### Overview

A new caller-side **startup bracket** `_rescue_orphan_if_present(ctx, host, config)` runs at the
top of the claudecode session entry, **before** `run_log_protocol_session(...)`. It is a no-op
unless a crash orphan is detected. When one is found it: writes a durable marker, kills the
orphan tree (reusing the extracted teardown helper), captures the orphan's live state via the
existing `_capture_snapshot`, then clears the marker. The unchanged resume path inside the
driver subsequently restores that fresh snapshot.

Because everything the rescue needs is derivable pre-driver — `socket = _tmux_socket_path(host)`,
`tmux_path = _require_tmux(host)`, `session = "optio"`, `claude_path =
<workdir>/home/.local/bin/claude` — it does **not** depend on `_prepare` having run.

### Step 1 — Extract the teardown kill sequence into a reusable helper

The inline `finally` kill block (session.py:361-395) is extracted into one function in
`host_actions.py`:

```python
async def teardown_session_tree(
    host, *, tmux_path, tmux_socket, tmux_session, claude_path,
    ttyd_handle=None, aggressive,
):
    # 1. ttyd: tracked launch handle (normal teardown) OR detached orphan (rescue)
    if ttyd_handle is not None:
        await host.terminate_subprocess(ttyd_handle, aggressive=aggressive)
    else:
        await _kill_ttyd_by_socket(host, tmux_socket)      # NEW
    # 2. SIGHUP the pane
    await _kill_tmux_session(host, tmux_path, tmux_socket, tmux_session)
    # 3. claude-under-(maybe-)pasta reap — ignores the pane SIGHUP
    await kill_claude_processes(host, claude_path)
    # 4. wait quiescent so a subsequent capture tar does not race a dying claude
    await await_claude_gone(host, claude_path)
```

Each step is wrapped best-effort (`try/except` + `_LOG.exception`), preserving the current
inline semantics. Both call sites adopt it:

- **Teardown** (`session.py` `finally`): `teardown_session_tree(..., ttyd_handle=launched_handle,
  aggressive=cancelled)` replaces the inline steps 1-4. No behavior change.
- **Rescue**: `teardown_session_tree(..., ttyd_handle=None, aggressive=True)`.

The only genuinely new code is `_kill_ttyd_by_socket(host, socket_path)` — the orphan ttyd has no
tracked launch handle and is re-parented to init, so it is reaped by an **anchored** host-side
`pkill -f` on the socket path it carries in its cmdline (anchored to avoid matching the rescue's
own command, mirroring `_claude_pgrep_pattern`'s `[c]` trick).

Socket-file cleanup follows existing teardown semantics: with the only session killed, the
private tmux server self-exits and removes its socket; no explicit `rm` is added (none exists in
teardown today, and `new-session -S` tolerates a stale socket).

### Step 2 — The rescue bracket

`_rescue_orphan_if_present(ctx, host, config)`, called before `run_log_protocol_session`:

1. **Detect.** Derive `socket`, `tmux_path`, `session="optio"`, `claude_path`. Trigger if
   `tmux_session_alive(...)` is true **OR** the marker file `<workdir>/.optio-rescue-pending`
   exists. If neither → return immediately (normal resume, fully unchanged).
2. **Mark.** Write `<workdir>/.optio-rescue-pending` (durable, on the task workdir).
3. **Kill first.** `teardown_session_tree(..., ttyd_handle=None, aggressive=True)`. Kill before
   capture so the workdir is static and dead during capture (see Decision D3).
4. **Capture.** `_capture_snapshot(ctx, host, end_state="rescued",
   workdir_exclude=config.workdir_exclude, session_blob_encrypt=config.session_blob_encrypt)` —
   the *same* function and semantics as a normal teardown capture, producing identical artifacts
   and performing the same sensitive harvest-then-expunge.
5. **Clear.** On `_capture_snapshot` success, delete the marker.
6. **Return.** The driver runs normally: `setup_workdir` wipes the workdir, `_prepare` loads and
   restores the freshly-inserted snapshot (latest-wins), claude relaunches with `--continue`.

A new `end_state` value `"rescued"` is added alongside the existing `"done"`/`"cancelled"` for
audit/forensics; it has no effect on resume selection (which ignores `end_state`).

### Step 3 — Comment-accuracy cleanup (in-scope, code we are already touching)

While extracting the kill sequence, correct the two comments that state pasta wrapping
unconditionally — session.py:384 ("claude runs under pasta in its own process group") and
host_actions.py:684 ("claude runs under pasta") — to reflect that pasta is conditional
(`OPTIO_CLAUDECODE_NETNS` + `local_mode`) and that the anchored host-side pkill reaches claude
regardless of whether it is wrapped. This prevents a future "fix" that wrongly couples the kill
to pasta and breaks the no-netns path. No code behavior changes.

## Safety and error handling

- **Sensitive data never leaks into the plaintext workdir blob.** Capture runs *after* the kill,
  so no live claude can repopulate `home/.claude/*` between the expunge (capture step 3) and the
  plaintext workdir tar (capture step 4). A live process would defeat the expunge; a dead one
  cannot.
- **The only copy is never destroyed before a durable snapshot exists.** `_capture_snapshot`
  stores the encrypted sensitive blob *before* it expunges `home/.claude`, and the plaintext
  workdir blob before `insert_snapshot`. The kill itself deletes *processes*, not files — the
  workdir stays on disk.
- **Mid-rescue failure is recoverable.** Kill-first removes the `has-session` signal, so the
  marker is the retry guard. If rescue dies anywhere between step 2 and step 5, the marker
  persists; the retried resume re-enters rescue (detect-by-marker), re-captures from the intact
  on-disk workdir, and only then clears the marker and lets the driver wipe. A normal graceful
  resume leaves neither session nor marker → the bracket is a no-op.
- **Capture-failure policy.** If `_capture_snapshot` raises, the rescue re-raises (aborting the
  resume) and leaves the marker in place, rather than letting the driver proceed to wipe intact
  unsaved state. This mirrors the existing capture-failure care; the operator's retry recovers.
- **Credentials guard.** `_capture_snapshot`'s existing credentials-present guard applies
  unchanged; a crash orphan that was running successfully has credentials on disk, so the guard
  passes.
- **Transcript race.** Eliminated by kill-first (the workdir is quiescent before the tar);
  `await_claude_gone` inside `teardown_session_tree` is the backstop, and the strict tar-exit
  check in `_archive_home_claude` remains the final guard.

## Relationship to auto-resume-on-restart

`docs/superpowers/specs/2026-06-06-auto-resume-on-restart-design.md` covers the **orchestration**
layer in optio-core: an `auto_resume` flag, a post-restart timer, and the rule that only
gracefully-saved (`cancelled`) processes are auto-resumed — hard-crashed processes left
`running` are reconciled to `failed` and **not** resumed, on the assumption that they have no
saved state.

This spec is the complementary **claudecode-backend mechanism**, and it refines that assumption:
a hard-crash *does* leave recoverable state — live, on disk, in the surviving orphan. Rescue is
what turns "resume a crash survivor" from destructive into recovering.

The two are independent and compose cleanly:
- **Today (manual):** an operator manually resumes the crash-`failed` task; this rescue makes
  that resume non-destructive. This already solves the 2026-06-08 production incident.
- **Future (automatic):** if the orchestration layer later chooses to make hard-crash survivors
  auto-eligible for resume, rescue makes that automation safe. **Designing that eligibility
  change is out of scope here** — it belongs to the orchestration spec.

## Testing

Unit (`packages/optio-claudecode/tests`):
- `_rescue_orphan_if_present` is a no-op when neither a live session nor a marker is present.
- Detect-by-marker path triggers rescue even when `has-session` is false (mid-rescue retry).
- `teardown_session_tree` invokes all four steps; the `ttyd_handle=None` branch calls
  `_kill_ttyd_by_socket`, the handle branch calls `terminate_subprocess`.
- `_kill_ttyd_by_socket` issues an anchored pkill on the socket path (and does not self-match).
- Marker is written before kill and cleared only after capture success; on simulated capture
  failure the marker persists and the rescue re-raises.
- Refactor guard: the teardown `finally` path still drives the same four kill steps via the
  extracted helper (no behavior change).

Integration (local host):
- Launch a claudecode session; drop the owning process without teardown (simulating a hard
  crash) so the tmux/ttyd/claude orphan survives. Run a resume and assert: a fresh `"rescued"`
  snapshot is inserted, the orphan tree (tmux server, ttyd, claude, socket) is gone, the
  `duplicate session` error does not occur, and the resumed conversation continues from the
  post-crash state (not the stale pre-crash snapshot).
- Run the same with `OPTIO_CLAUDECODE_NETNS` unset (no-pasta tree shape) to confirm the kill is
  pasta-agnostic.

## Decisions

- **D1 — Rescue-snapshot, not live-reattach.** Capture the orphan's state and restart claude from
  it, rather than keeping the running process and reconnecting the viewer. Smallest surface;
  reuses the entire battle-tested resume path; only adds a socket probe + a kill + one capture
  call. Live-reattach is the better end-state but carries more edge cases (ttyd port rediscovery,
  lease re-acquire, monitor re-entry) and is deferred to v2.
- **D2 — Always capture; no idle wait.** Do not poll for the orphan to be idle before capturing.
  Kill-first makes the workdir static regardless, so an idle wait buys nothing; a mid-turn orphan
  simply has its (by-definition incomplete) in-flight turn dropped, which `--continue` tolerates.
- **D3 — Kill before capture.** Two reasons: (a) **leak prevention** — a live claude could
  repopulate `home/.claude` after the expunge and before the plaintext workdir tar, leaking
  secrets into the plaintext blob; a dead one cannot; (b) **consistency** — a static workdir
  yields a clean, race-free tar. This matches teardown's own ordering, which already ends with
  `await_claude_gone` before capture for the same reason.
- **D4 — Durable marker for retry-safety.** Kill-first removes the `has-session` signal that
  otherwise protects a retried resume, so a marker file makes rescue re-entrant under a
  mid-rescue failure. Chosen over keeping the orphan alive during capture (which would
  re-introduce the D3 leak/consistency problems).
- **D5 — Auto-rescue, no operator confirmation.** Any orphan found on the per-task socket is, by
  construction, this task's — a resume only fires once the orchestration layer has disowned the
  task. No ownership-race guard is built for a signal the orchestration layer already enforces;
  the rescue logs loudly for auditability.
- **D6 — Reuse, don't hand-roll, the kill sequence.** The teardown kill took several debug rounds
  to get right (claude-under-pasta reaping, anchored patterns, await-quiescence). Extracting it
  into `teardown_session_tree` and reusing it in both teardown and rescue prevents the two paths
  from drifting.
