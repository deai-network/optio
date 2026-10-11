# optio-claudecode — Agent Cheatsheet

Run Anthropic Claude Code as an optio task — local subprocess or remote
host via SSH — with the interactive TUI exposed in the dashboard via a
ttyd-served iframe.

Full design: `docs/2026-05-28-optio-claudecode-design.md`.

## Public API

```python
from optio_claudecode import ClaudeCodeTaskConfig, create_claudecode_task

create_claudecode_task(
    process_id="my-task",
    name="My task",
    config=ClaudeCodeTaskConfig(
        consumer_instructions="...",
        credentials_json=...,        # opaque dict/bytes/str → ~/.claude/.credentials.json
        claude_config=...,           # dict, deep-merged into ~/.claude/settings.json
        env={"ANTHROPIC_BASE_URL": "..."},
        permission_mode=None,        # default | plan | acceptEdits | bypassPermissions
        allowed_tools=None,
        disallowed_tools=None,
        ssh=None,
        install_if_missing=True,
        install_ttyd_if_missing=True,
        claude_install_dir=None,     # default ~/.local/bin (per host)
        ttyd_install_dir=None,
        before_execute=None,
        after_execute=None,
        on_deliverable=None,
    ),
)
```

`TaskInstance` returned has `ui_widget="iframe"` and `supports_resume`
tracking the config field (defaults to `True`); when `supports_resume` it
also carries the `resurrect` hook (see Resurrect below). Its
`session_crypto` is always `SessionCrypto(encrypt=config.session_blob_encrypt,
decrypt=config.session_blob_decrypt)` (members None without session
encryption), so the engine can reach the cipher of a process that is not
running. Resume snapshots the
`<workdir>/home/.claude/` subtree (encryptable session blob) plus a plaintext
workdir blob; on resume the workdir is restored and `--continue` is appended to
claude's argv. A launch that continues a transcript also passes `--model`: the
configured model, else the `default` alias (`models.launch_model`, both modes).
`--continue` alone keeps the full model id the transcript ended on (even over a
settings.json `model`; verified on CLI 2.1.268), while an alias runs the newest
model it names, so a resumed session moves to a newer model by itself; a pinned
full id stays pinned. A fresh launch passes only a configured model. A model
or effort pick never relaunches: it switches the running claude live (see the
session controls below). Every launch keeps passing `--model` / `--effort`:
bare `--continue` restores the model but not the effort (CLI 2.1.296).
The operator's picks in the conversation widget (model, reasoning effort,
permission mode) are kept across an optio resume (`picks.py`: a file in
`home/.claude`, so in the session blob; only an optio resume reads it back, not
`session_restore_from` or a seed). Before the resumed launch a short-lived
claude (same env and isolation, no `--continue`, no turn, no transcript;
`--model` the configured model (or a custom control's default) or `default`,
never the saved one, so the list
holds no echo of it) is asked only `initialize`, and each pick is checked
against that list: a model it no longer lists moves to the newest entry of its
family (`models.restore_model`: the
`claude-<family>-<version>` of the full id saved with the pick, its `[variant]`
kept when offered, never `default`), else the configured model, else
`default` (a model a custom control's option names stays as is); an effort
level the model lacks gives way to its default; a
permission mode the task does not offer is dropped. When that probe cannot
tell, claude is launched on the configured model (else `default`), its list is
used, and a correction is switched to live, before the first message. See
`docs/2026-05-29-optio-claudecode-resume-design.md`.

Every resume that continues a transcript tells claude so with
`System: you have been resumed` (`RESUME_NOTICE`), with or without
`host_protocol`: the positional after `--continue` in iframe mode, the first
stdin message in conversation mode. CLAUDE.md is therefore always composed
with `check_resume_log_every_message=False` (fresh start and the on-resume
refresh): claude reads `resume.log` when that notice arrives, for its
`REFRESHED:` suffix, not at the start of every message. A resume without a
transcript launches without `--continue` and sends no notice; that is a new
conversation, which reads CLAUDE.md anew.

## Snapshot capture and unsaved work

`session._capture_snapshot` runs at teardown (only when `supports_resume`
and claude came up) and in crash-orphan rescue. It returns `True` when it
inserted a snapshot record and `False` when its credentials guard refused
(teardown ignores the result; rescue logs which one happened). Its first half: the credentials guard (no
`home/.claude/.credentials.json` → no snapshot), the session blob (tar of
`home/.claude`, through `session_blob_encrypt`), the conversation blob when
its `conversation: bytes | None` kwarg is given (`_store_conversation_blob`:
GridFS name `conversation`, through `session_blob_encrypt` too; the
conversation-mode teardown passes the listener's `export_buffer()` JSON,
exported in memory before `stop()` and never written to the host), or,
with `conversation_blob_id: ObjectId | None` instead (not both), a
conversation blob already stored, which the record only references, then
`rm -rf home/.claude` and the regenerable mozilla dirs. A refused capture
stores neither blob. Crash-orphan rescue and Resurrect have no listener to
export from, so they carry the conversation record forward:
`session._carry_conversation_forward(ctx) -> ObjectId | None` copies the
latest snapshot's record into a new blob, the stored bytes as they are (no
decrypt, no re-encrypt; `_latest_conversation_bytes(ctx) -> bytes | None`
reads them, None without a record, never raising), since pruning deletes
per snapshot. Rescue passes that copy as `conversation_blob_id` and deletes
it when the credentials guard refuses. The teardown capture of a resumed run
does the same when it has no export (the export raised, or the run failed
after claude came up but before the listener started), so the record it
resumed from is kept. Its second half is
`session._store_workdir_snapshot(ctx, host, *, end_state, workdir_exclude,
session_blob_id, conversation_blob_id=None)`, callable on its own. Every
claudecode workdir tar is made there, and it archives with
`_effective_workdir_excludes(workdir_exclude)`: the given list (the archive
defaults when None) plus `.optio-conversation-buffer.json` (the file older
versions wrote the buffer to), whatever the caller passes (unanchored: a
file of that name is dropped at any depth). It records
`{processId, sessionBlobId, workdirBlobId, conversationBlobId?, startedAt}`
in `{prefix}_claudecode_pending_captures` (one document per processId,
upserted; `conversationBlobId` only when the capture stored one;
`optio_claudecode.pending_captures`: `record_pending_capture`,
`load_pending_capture`, `pending_conversation_blob_id(record)`,
`delete_pending_capture`, `discard_pending_workdir_blob`,
`discard_pending_conversation_blob`), streams the workdir into a
GridFS blob opened with that pre-generated `workdirBlobId`, inserts the
snapshot record (with `conversationBlobId` when given), deletes the pending
record, calls `mark_has_saved_state()` then `clear_unsaved_work()`, and
prunes (a pruned snapshot's session, workdir and conversation blobs are
deleted). The flags come before the pruning: the
snapshot is complete once its record is in, and a force-cancel past the grace
can cut the capture off during the pruning (seen 2026-10-10: 3 s into it); the
process then already offers Resume rather than a Resurrect of saved work. A
capture cut off while
streaming the workdir leaves the pending record behind, naming the partial
workdir blob. One cut off between inserting the snapshot record and deleting
the pending record leaves a record naming that snapshot's complete workdir
blob. Hence the referenced-blob guard: `discard_pending_workdir_blob(db,
prefix, *, process_id, record, delete_blob)` deletes the recorded workdir blob
only when no `{prefix}_claudecode_session_snapshots` document of the process
has it as `workdirBlobId`, and returns whether one does; it never deletes
the record. A record must not outlive the next launch, or a later Resurrect
would take it for that run's capture: `run_claudecode_session` calls
`pending_captures.settle_pending_capture(ctx, *, discard_session_blob=False)
-> bool` with `discard_session_blob=True` right after
connecting, before crash-orphan rescue, on every launch (fresh or resume).
It discards the recorded workdir blob (guarded), deletes the record, and,
when a snapshot references the blob (that capture had committed), calls
`mark_has_saved_state()`; it does not touch `hasUnsavedWork` (optio-core
clears it at launch). With `discard_session_blob` (the launch path only),
for a capture that had not committed it also deletes the record's session
blob unless a snapshot has it as `sessionBlobId`, and its conversation blob
unless a snapshot has it as `conversationBlobId`
(`discard_pending_conversation_blob(db, prefix, *, process_id, record,
delete_blob, keep=None)`): after a launch they are garbage, and a later
session-blob fallback could adopt them. The Resurrect
hook settles without it (its fallback needs those blobs). Before recording its own, `_store_workdir_snapshot`
also discards the workdir blob and the conversation blob (unless it is the
one this capture stores) of any record still present, each kept when a
snapshot references it (the upsert would orphan them).

`hasUnsavedWork`: `session._on_agent_live` calls `ctx.mark_unsaved_work()`
when claude goes live (the iframe launch, after the tmux path is resolved so a
failed write cannot make teardown skip the session-tree kill; the
conversation launch; not the conversation's model/effort respawn), only when
`supports_resume`.
optio-core ignores it, with a warning, unless the task has a resurrect hook.
It is cleared by `clear_unsaved_work()` after a completed save, and by the
credentials guard when it refuses the snapshot (a later save would be refused
too, so nothing is left to resurrect).

Teardown: a capture that raises is logged as "snapshot capture failed"
(with the exception); then `session._cleanup_after_capture` keeps the task
directory on the host (logged as "snapshot capture failed; keeping task
directory <taskdir> for Resurrect") and `hasUnsavedWork` stays set, so
Resurrect can retry. Otherwise `cleanup_taskdir(aggressive=cancelled)` runs
as before.

Deferred migration `claudecode_conversation_buffer_out_of_workdir`
(`migrations.py`, registered on `optio_core.deferred_migrations` when
`optio_claudecode` is imported; runs at engine start after the initial sync,
blocking; spec §6): captures before the fix left the replay buffer in the
plaintext workdir blob as `.optio-conversation-buffer.json`. It visits every
snapshot document without `conversationBufferMigrated: true`, oldest first,
logging counts every 25 documents and at the end. A document that has a
`conversationBlobId` is only marked. Otherwise its workdir blob is streamed
through `tar_strip.strip_member` into a new `workdir` blob without the
top-level member (`./` or not). `strip_member` runs tarfile on a thread
behind bounded queues, with its own gunzip and level-1 gzip, and keeps
headers. It raises `ArchiveError` unless the input is intact: every gzip
member complete with matching CRC and length, an all-zero end-of-archive
block followed only by zeros (a bad or truncated header is not an end), and
only copyable members (no unknown or sparse types, no data on non-regular
ones; any other error raised while reading or copying the archive, e.g. a
`ValueError` for a header field the PAX copy cannot hold, is an
`ArchiveError` too). Buffer found: with a task (`ctx.task_for`) that has a
`session_crypto`, the bytes become a `conversation` blob, through its
`encrypt` when set (plaintext only for `SessionCrypto(encrypt=None)`);
without a task, or with `session_crypto` None (cipher unknown), they are
dropped. One compare-and-set `update_one` (the document must
still name the old blob and be unmarked) sets the new `workdirBlobId`, the
`conversationBlobId`, the marker and `migratedFromWorkdirBlobId: <old>`; no
match deletes the new blobs and leaves the document alone. Then
`{prefix}_claudecode_pending_captures` records of the process naming the old
blob are repointed to the new one, the old blob is deleted, and
`migratedFromWorkdirBlobId` is removed. Every run first finishes documents
still carrying that field (a failed delete, an update that raised after being
applied). No buffer: the new blob is deleted and the document only marked. A
missing workdir blob, a corrupt one (`gridfs.errors.CorruptGridFile`) or an
`ArchiveError` marks the document as it is, blob kept, with a warning (no
retry would fix it). New blobs' metadata is `{processId: <process doc _id as
str, else the snapshot's processId>, prefix, name}`. An interruption before
the update leaves orphan new blobs and an unmarked document, redone on the
next start.

## Resurrect

`create_claudecode_task` sets `TaskInstance.resurrect` when
`config.supports_resume` (else `None`). optio-core runs it through
`Optio.resurrect` for a failed process with `hasUnsavedWork`, then resumes
from the snapshot it stored. The hook is
`resurrect.resurrect_claudecode_session(ctx, config)`, on the host from
`session._build_host`:

1. Connect. Workdir missing or empty: raise
   `NothingToResurrect("workdir no longer on the host (or empty)")`. The
   host probes (this one and step 4's credentials test) run
   `<test> && echo YES || true`; a non-zero exit means the probe did not run
   (connection, shell) and raises `RuntimeError` with the exit code and
   stderr, never "absent": an error keeps `hasUnsavedWork`, so the
   resurrect can be retried.
2. Progress "Resurrecting: stopping leftovers of the failed run…". When
   `host_actions.find_tmux` finds tmux and the tmux session `optio` on the
   task's socket is alive, `host_actions.teardown_session_tree(aggressive=True)`
   (as crash-orphan rescue does); no tmux on the worker (conversation mode)
   means no session. `find_tmux` returns None only when the lookup
   succeeds with empty output; a failing lookup (non-zero exit) raises.
   Then, always,
   `pkill -f -- '^tail -F -n \+1 <workdir>/optio.log$'` with the path
   regex-escaped: anchored, so it matches only the failed run's log reader
   and never the shell running pkill.
3. `settle_pending_capture` (the partial blob goes, chunks too; the
   `fs.files` document may not exist). Since every launch settles records,
   one found here was written by the failed run's own capture. Short-circuit
   only when that capture had committed (a snapshot references the blob)
   AND the workdir has no credentials (step 4's test; the capture removes
   `home/.claude` before recording): the work is in that snapshot, so the
   hook calls `clear_unsaved_work()` (settle already called
   `mark_has_saved_state()`), removes the task directory and returns. With
   credentials present the workdir holds newer work: go on to step 4.
4. Save. Excludes: `session._excludes_with_rescue_marker(config)`, the
   effective `workdir_exclude` (the archive defaults when unset) plus the
   rescue marker, as crash-orphan rescue uses. The branch is chosen by
   `home/.claude/.credentials.json` being non-empty (`test -s`), the same
   test as the capture's credentials guard.
   - Present (crash while running, or the capture failed before its session
     step): `_capture_snapshot(end_state="resurrected")`. If its own guard
     refuses after all (the credentials disappeared since the hook's test),
     it returns `False` and the hook raises
     `NothingToResurrect("credentials disappeared during the save")`, leaving
     the host as it is.
   - Absent (the capture was cut off after its session step, including part
     way through its `rm -rf home/.claude`; also every run that failed on
     code before Resurrect): the session-blob fallback.
     `resurrect.find_unreferenced_session_blob(db, prefix, *, process_oid,
     process_id)` returns the newest GridFS blob with `metadata.processId` =
     `str(process_oid)`, `metadata.prefix`, `metadata.name: "session"`,
     uploaded after the latest snapshot's `capturedAt` and referenced by no
     snapshot's `sessionBlobId`. Found: any leftover `home/.claude` is
     removed (it must not enter the plaintext workdir blob), then
     `_store_workdir_snapshot(end_state="resurrected")` archives the workdir
     with it. Not found: if a snapshot exists, `mark_has_saved_state()` (a
     capture cut off before flagging it leaves it unflagged, and Resume needs
     the flag), then raise `NothingToResurrect("session state not found")`;
     the host is left untouched.
   Both branches give the new snapshot a conversation record
   (`conversation_blob_id`). The hook reads the pending record before step
   3's settle deletes it: when that capture did not commit and named a
   conversation blob that still exists and no snapshot references
   (`resurrect._adoptable_conversation_blob`), the new snapshot references
   that blob itself, since it holds the newest history; the hook's settle
   keeps it and `_store_workdir_snapshot`'s stale-record discard skips the
   blob it records (`keep=`). Otherwise `_carry_conversation_forward` copies
   the latest snapshot's record (after a committed capture, the copy keeps
   its snapshot's blob singly referenced). A copy is deleted again when the
   capture's guard refuses; an adopted blob is left.
   Partial chunks that no pending record names (old code) are left alone.
5. `cleanup_taskdir(aggressive=False)`, only after a snapshot was inserted;
   disconnect.

A failed capture keeps the task directory (see Teardown above), so a plain
Resume can also find it: crash-orphan rescue (`_rescue_orphan_if_present`)
uses the same `find_tmux` lookup and, without tmux, probes no session (a
leftover rescue marker still triggers a rescue, minus the tmux-tree
teardown). Its capture uses the same excludes as the hook
(`_excludes_with_rescue_marker`), so with `workdir_exclude` unset a rescued
snapshot leaves out `.git`, `node_modules`, `.venv` etc. like a normal one.

`hasUnsavedWork` is set by `_on_agent_live` and cleared by a completed save
or the credentials guard (above), and by the hook's step-3 short-circuit;
optio-core also clears it when a launch starts,
when the hook returns, and when it raises `NothingToResurrect`.

## ClaudeCodeTaskConfig field semantics

* `credentials_json` — opaque payload; planted at
  `<workdir>/home/.claude/.credentials.json` with mode 0600. dict →
  JSON-encoded; bytes → UTF-8 decoded verbatim; str → written
  verbatim.
* `claude_config` — deep-merged into
  `<workdir>/home/.claude/settings.json` via a post-seed
  read-modify-write (`host_actions.apply_claude_settings`): applied
  AFTER the seed (fresh) or restored snapshot (resume) on all paths, so
  the caller's keys win per key while every other key the seed/snapshot
  carried is preserved.
* `permission_mode` — the starting mode, forwarded verbatim to `claude
  --permission-mode`: `manual` (Claude Code's current name; `default` still
  accepted, and is what the stream reports), `acceptEdits`, `plan`, `auto`,
  `dontAsk`, `bypassPermissions`. Validation happens in `__post_init__`; a
  conversation without `permission_gate` must start in one that never waits
  for an answer (`acceptEdits`, `auto`, `dontAsk`, `bypassPermissions`) or
  have `allowed_tools`.
* `allow_bypass_permissions: bool | None = None` — whether bypassPermissions is
  reachable at all (Claude Code decides at launch; passed as
  `--allow-dangerously-skip-permissions`). None: only when starting in it;
  True: also from another starting mode; False: never (contradicts starting in
  it, a config error). `bypass_allowed` resolves it.
* `tools: list[str] | None = None` — the built-in tools claude has at all
  (`--tools <comma-separated>`). None: claude's default set; `[]`: `--tools ""`,
  no built-in tools (unlike `allowed_tools`, where an empty list means unset).
* `mcp_servers` (`optio_agents.McpConfigMixin`: a list of `McpServer`, or a
  provider awaited with the process id) — resolved on every launch, fresh and
  resume, right after the session content is planted
  (`session._plant_mcp_servers`), and written to
  `<workdir>/home/.claude/optio-mcp.json` (`host_actions.plant_mcp_config`:
  `{"mcpServers": {<name>: {"type": "http", "url", "headers"}}}`, mode 0600,
  created empty before the headers go in). Claude gets `--mcp-config <path>
  --strict-mcp-config`, so no other MCP source applies (an `mcpServers` key in
  settings.json is ignored by Claude Code anyway). No servers: a file an older
  snapshot carried is removed, no flags. The file never enters a snapshot: the
  session capture leaves it out (`session._archive_home_claude` excludes
  `host_actions.MCP_CONFIG_REL`; the session blob is encrypted only when
  `session_blob_encrypt` is set), the plaintext workdir tar runs after
  home/.claude is removed, and the seed manifests are allowlists that do not
  name it. The model probe gets neither the file nor the flags. A provider's exception fails the launch with its
  message; header values (credentials) are never logged.
* Positional prompts (the auto_start kickoff, the resume notice) go after `--`
  (`host_actions.positional_prompt_args`): `--tools`, `--mcp-config` and
  `--allowed-tools` take several values and would swallow a prompt after them.
* `SessionControlsConfigMixin` — the conversation widget's controls bar:
  `show_session_controls` (requires conversation mode + `conversation_ui`)
  and the optional `session_controls` allowlist of control ids (None: all).
  Claude Code's controls (`controls.build_controls`): `model`,
  `reasoning_effort` and `permission_mode`, all switched live, without a
  restart, by `ClaudeCodeConversation.apply_settings(model=, effort=,
  permission_mode=)` (None: unchanged; `set_control` routes to it). A model
  and/or effort change is ONE stream-json control request
  `apply_flag_settings {settings: {model, effortLevel}}`, always with both
  keys and explicit values (claude falls a missing or null one back to the
  account's default, not the launch flags): the effort sent with a model
  change is the running level when the new model lists it (or lists none),
  else the model's preselected one; an effort change carries the running
  model (`default` when none was set). A permission mode is
  `set_permission_mode`, sent first and switched back if claude then refuses
  the model/effort change. Claude takes an unknown model as given and drops
  an unknown level silently, so each value is checked first against the
  CLI's model list (`conversation.models_offered`, `models.offers`;
  the running model always passes; while the CLI cannot list its models,
  each switch asks it again and takes a model as given): `ValueError` (→ /control 400
  `bad-value`), nothing sent. Claude's error answer raises `ControlRejected`
  (→ 409 `rejected` with its message) and leaves the values as they were. On
  success `conversation.model` / `effort` hold the new values, the picks are
  saved and an `x-optio-control-update` re-emitted (the body's
  `conversation.on_settings_change` hook; after a refusal the hook re-emits
  the unchanged values, replacing the widget's optimistic one). Claude
  answers a model change made while a turn runs after that turn; it applies
  from the next turn, whose `system/init` names the new model (until then
  `runtime_model` is None). The permission_mode select always
  lists all six modes with a description each
  (`controls.permission_mode_options`); without `permission_gate` Manual and
  Plan (they ask the operator) are disabled with the reason, and Bypass is
  disabled unless `bypass_allowed`. Only enabled modes can be set. The select
  follows the mode the stream reports (`system/init`, `system/status`).
  Every snapshot is narrowed by the
  allowlist, and the listener's /control refuses an id outside
  `settable_controls` (403 `not-allowed`; all of them while the bar is off).
* `WelcomeConfigMixin` — `welcome: WelcomeCard | None = None`: the card the
  conversation widget shows atop the transcript (title, markdown
  description, both plain strings or language maps; icon URL relative to the
  web app root; `min_height` in CSS pixels). The conversation UI publishes
  `config.welcome_widget_data` (`{title, description, icon, minHeight}`,
  texts unresolved, or None) as widgetData `welcome`, next to
  `thinkingVerbosity`; the iframe widget ignores it.
* The model select lists what the running CLI answers to the stream-json
  control request `initialize` (`ClaudeCodeConversation.initialize`, whose
  answer is not fanned out to on_event; `models.fetch_cli_models`), asked once
  claude is up, before the first message: the list Claude Code's own /model
  picker shows: the current models in its order, then the older versions (an
  entry whose family, `claude-<family>-<version>` of its `resolvedModel`, has a
  newer entry) under the heading "Older versions" (`ControlOption.group`;
  `models.with_older_versions_last`), by family, newest first. Option value =
  the alias (`default`,
  `opus[1m]`, `sonnet`, `haiku`, ...; `--model` takes these), label =
  `displayName`, description = `description`. The reasoning_effort slider
  offers the selected model's `supportedEffortLevels` (no slider when it does
  not `supportsEffort`), preselecting `high` (else the highest level). The
  value shown (`models.shown_model`): the configured or picked value, else
  `default`; the full id system/init reports (e.g. `claude-opus-5[1m]`) moves
  it only when the shown entry's `resolvedModel` is something else (e.g. a
  settings.json `model`), to the alias resolving to it, else to the id itself.
  A pinned full id is launched as given and shown as the alias that resolves
  to it today (e.g. `claude-sonnet-5` shows Sonnet), else as its own entry:
  the CLI lists one for a `--model` that no alias names (e.g.
  `claude-opus-4-8`, "Newer version available"), and a value the list lacks
  gets a bare option. The list is kept from the first process (so a pinned
  entry stays offered after a switch); when the CLI cannot tell (an error
  answer, no models, no answer in `models.INITIALIZE_TIMEOUT_S`) the select
  offers the bare aliases default/opus/sonnet/haiku without descriptions or
  effort levels, and each live switch asks again.
* `CustomControlsConfigMixin` (`custom_controls`, `optio_agents.custom_controls`)
  — the task's own selectors, each option a fixed `ControlSettings` (model /
  reasoning effort / permission mode). Requires conversation mode +
  `conversation_ui`; independent of `show_session_controls` and the
  `session_controls` allowlist. On top of the shared checks the config refuses
  an option whose permission mode this task cannot switch to
  (`controls.permission_mode_unusable`: Manual / Plan without
  `permission_gate`, Bypass unless `bypass_allowed`) and two options of one
  control that do not differ in a setting both set (both could match, so the
  selection would be ambiguous). Launch: each control's default option
  replaces the config's `model` / `reasoning_effort` / `permission_mode`
  (`controls.custom_defaults`; the session's `base_*`), fresh and on a resume
  that saved no pick; saved picks win over them, and a permission mode is
  saved while it differs from that launch mode. The models the options name
  (`controls.task_models`, `conversation.task_models`) are the task's own,
  like a configured model: switched to and restored as given, though the
  CLI's list (its picker's entries, e.g. no plain `opus`) may not name them.
  widgetData `customControls` and every `x-optio-control-update`
  (`controls.custom_controls_snapshot`): per control `{id, label,
  description, options: [{value, label, description}], value}`, labels and
  descriptions as defined (a string or a language map; a missing description
  is null), `value` the option whose settings all match the running values
  (`controls.custom_control_value`; the model compared as the model select
  shows it), else null (e.g. after a manual built-in change). `[]` without
  custom controls. `POST /control {id: "custom:<control id>", value: <option
  value>}` (`controls.CUSTOM_CONTROL_PREFIX`; accepted whatever
  `settable_controls` says) applies the option's settings in ONE
  `apply_settings` (one `apply_flag_settings`, plus `set_permission_mode`
  when it sets a mode); the hook saves the picks and re-emits. 400 `bad-id` /
  `bad-value` for a control / option the task does not define; 409 with
  nothing changed: `closed`, `busy` while a turn runs (the listener's
  `_turn_running`: claude would apply a model change only after the turn),
  `rejected` + `error` when claude refuses or the session cannot apply the
  option's values (`ValueError`). After `busy` / `rejected` the listener's
  `refresh_controls` re-emits the running values, replacing the widget's
  optimistic selection.
* `session_blob_encrypt/decrypt`, `seed_blob_encrypt/decrypt` —
  inherited from `optio_agents.config_types.BlobCryptoConfigMixin`
  (alongside the `ClaustrumConfigMixin` triad). Optional synchronous
  bytes→bytes transforms at GridFS write/read: the `session_blob` pair
  wraps this process's home/.claude session tar (ds-scoped key), the
  `seed_blob` pair wraps the shared pool-account SEED tar (pool-scoped
  key). Per pair both-or-neither (validated by `_validate_blob_crypto`
  in `__post_init__`); the seed pair falls back to the session pair
  when unset — seed ops read the transforms only through the mixin's
  `seed_encrypt` / `seed_decrypt` accessors.
* HOME isolation: every task sees `HOME=<workdir>/home` so concurrent
  tasks on one host never share `~/.claude/` state.

## Conversation mode

Full design: `docs/2026-06-10-claudecode-conversation-gate-design.md`.

A finished assistant `TodoWrite` replaces the checklist and updates the
process percent and status line (`optio_agents.todos`). `TaskCreate`
adds a pending row: the id comes from the tool result, `activeForm`
is taken from the tool call, and a non-empty description is stored
with the row. `TaskUpdate` changes only the status, subject,
`activeForm`, or description it sends, and `deleted` removes the row.
`TaskList` replaces membership and keeps a description the list line
does not carry, and `No tasks found` clears the list. A rejected
call (`is_error`, including a status Claude does not accept) is ignored.
On resume, a transcript that accepted one of those results rebuilds the
list, and otherwise the saved checklist is kept. `TodoWrite` still applies.
Streaming argument fragments do not.

Three config fields control it (all defaults preserve today's behavior):

* `mode: Literal["iframe", "conversation"] = "iframe"` —
  `"conversation"` runs claude headlessly (no tmux, no ttyd, no iframe
  widget) over its bidirectional stream-json stdio protocol and
  publishes a live `Conversation` object via `ctx.publish_result()`.
* `host_protocol: bool = True` — opt-out for the optio.log keyword
  channel (STATUS / DELIVERABLE / DONE / ERROR / …). With `False` the
  keyword docs are also omitted from the composed CLAUDE.md.
  `mode="iframe"` **requires** `host_protocol=True` (it is the only
  completion signal there); validated in `__post_init__`.
* `permission_gate: bool = False` — only valid with
  `mode="conversation"`. Routes claude's `can_use_tool` control
  requests to the caller's `on_permission_request` handler instead of
  pre-deciding via `--permission-mode` / `--allowed-tools`. With
  `permission_gate=False` in conversation mode, `permission_mode` must
  be one of `{"acceptEdits", "bypassPermissions", "dontAsk"}` or
  `allowed_tools` must be non-empty (headless claude cannot show a
  permission dialog).

The `Conversation` surface (abstract Protocol in
`optio_agents.conversation`; concrete `ClaudeCodeConversation` here):

* `await send(text)` — queue one user message (claude queues stdin
  messages natively); raises `ConversationClosed` after session end.
* `on_event(handler) -> unsubscribe` — transparent passthrough of every
  stdout NDJSON object as a dict, unmodified. Live events only.
  Synthetic `{"type": "x-optio-unparseable", ...}` /
  `{"type": "x-optio-closed", ...}` events use the `x-optio-` prefix.
  The conversation listener keeps `x-optio-closed` out of the persisted
  replay buffer and marks a resume with its own `x-optio-resumed` instead
  (see Replay-buffer semantics).
* `on_message(handler) -> unsubscribe` — simplified tier: one final
  answer text per completed turn (the `result` event's `result` field).
* `on_permission_request(handler) -> unsubscribe` — the permission
  gate; at most one handler, second registration replaces the first.
  Requests arriving before registration are queued (the turn blocks),
  so register promptly when `permission_gate=True`. With the gate off,
  a stray `can_use_tool` is answered with a defensive deny. Note:
  sandbox-safe commands (e.g. a bare `echo`) execute without consulting
  the gate at all — the handler only sees non-sandboxable calls.
* `is_pending()` — `True` while a sent message has no `result` yet. A
  message sent mid-turn joins that turn, so two sends can share one
  `result`. `system/session_state_changed` resyncs it rather than
  zeroing it unconditionally: `idle` sets it to the number of sends
  written since the last `result` (0 for a genuinely finished turn, but
  not for a stale idle that races a send), and `running` raises it to at
  least 1. **These resyncs depend on the CLI actually emitting
  `session_state_changed`, which since CLI 2.1.270 only happens with
  `CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS=1`** (set by
  `conversation_launch_env`, below). Without it a merged turn leaves
  `_pending` stuck above 0 forever. `_route` logs one warning per
  conversation (on the first `result` seen with no prior state event)
  naming the variable, rather than drifting silently.
* `emit_event(event)` — put a synthetic `x-optio-*` event into the event
  stream, in order with native events (the steering scaffold's hook).
* `await interrupt()` — abort the current turn; no-op when idle.
  Verified live: the CLI acks with a `control_response` and ends the
  turn with `result` subtype `error_during_execution` (`result: null`,
  so no `on_message` fires for the aborted turn). Messages queued
  behind the interrupted turn are processed normally afterwards.
  `interrupt(cancel_queued=True)` (Fix 19) makes the CLI drop them
  instead: each gets a `command_lifecycle` `cancelled` and no `started`.
* `begin_session_end()` / `await interrupt_for_session_end()` (Fix 19,
  session end; see "Session end" below): the first makes `send()` raise
  `ConversationClosed` and `closed` read `True`; the second interrupts
  the running turn with `cancel_queued` and returns once its `result`
  was dispatched (a no-op when idle). The session bounds it at 3 s.
* `await close()` — cooperative shutdown of the whole task; idempotent.
* `closed` (property) — `True` once the session has ended, or is ending
  (`begin_session_end`).

Caller-side usage (publish/await via optio-core):

```python
conv = await optio.launch_and_await_result("my-task", session_id=None)
conv.on_message(lambda text: print("claude:", text))
await conv.send("hello")
...
await conv.close()
```

Task completion semantics in conversation mode:

| Trigger | Task outcome |
|---|---|
| Caller `close()` | cooperative shutdown → **done** |
| optio cancel | existing route → **cancelled** (aggressive teardown) |
| Claude exits on its own before `close()` | **failed** ("claude exited unexpectedly (exit N)") |
| `host_protocol=True` alongside (legal combo) | optio.log DONE/ERROR terminate exactly as today; a caller `close()` emits a harness-side DONE so the task still resolves **done** |

### Conversation UI (`conversation_ui`)

Full design: `docs/2026-06-10-claudecode-conversation-ui-design.md`.
Browser widget: the engine-neutral `optio-conversation-ui` package
(`packages/optio-conversation-ui`) — register it in the host app via
`registerConversationWidget()` (serves both claudecode and opencode;
each task self-declares its engine via `widgetData.protocol`).

* `conversation_ui: bool = False` — strictly opt-in; requires
  `mode="conversation"` (validated in `__post_init__`). The published
  `Conversation` object stays the default gate; this is a deliberate
  parallel path for dashboard monitoring/control.

When `True`:

* Two extra argv flags are appended to the conversation argv:
  `--include-partial-messages` (live partial text on the stream) and
  `--replay-user-messages` (user turns echoed back on stdout — without
  it the stream carries only the assistant side). Gated on the flag so
  in-process-only consumers don't pay for events nobody reads.
* `create_claudecode_task` sets `ui_widget="claudecode-conversation"`
  (instead of `None` in plain conversation mode).
* After `ctx.publish_result(conversation)` the task body starts a
  per-task `ConversationListener` (aiohttp, sibling of
  `input_listener.py`, OS-assigned port, `OPTIO_WIDGET_TUNNEL_BIND`
  interface logic), registers it via
  `ctx.set_widget_upstream(url, inner_auth)` with a per-task random
  basic-auth credential, and calls `ctx.set_widget_data({})`. The
  listener is shut down in the session teardown bracket.
* The listener is reachable from `set_widget_upstream` on, but holds its
  input (`ConversationListener(..., hold_input=True)`): `POST /send` and
  `POST /steer` wait until the session has written its opening messages
  (the kickoff prompt, or the "you have been resumed" notice and
  `requeue_undelivered()`) and calls `listener.open_input()`. A message sent
  while the session starts (the suspended replay posts the moment the
  listener is reachable) so reaches Claude after them, in arrival order. A
  request still held when the listener stops answers 409 closed.

Endpoints (reached through the optio-api widget proxy, which injects
the inner basic-auth credential; GET = viewer role, POST = operator):

| Endpoint | Behavior |
|---|---|
| `GET /events` | SSE. On connect: replay buffer contents, then live tail. Each event's SSE `id:` is its monotonic `seq`; `Last-Event-ID` honored, so reconnects resume without duplicates. |
| `POST /send` | `{text}` → Send when ready (`Steering.send_when_ready`). Returns `{ok, id, queued}`; `queued` is true when a turn was running: the message waits its turn and reaches Claude as its own user message (one at a time, Fix 17: see Steering events below). 409 when closed. |
| `POST /steer` | `{text, upTo?}` → Interrupt and send (`Steering.interrupt_and_send`): queue `text` behind anything already queued, interrupt the running turn and wait for its `result` (at most 15 s). Claude then runs the message in flight, and the rest follow one at a time, so `text` arrives last, as its own message. Empty `text` = Send now: only interrupt. `upTo` is the queued id Send now was clicked on; since Fix 17 it needs nothing beyond the interrupt (no cancel, no re-send). Returns `{ok, id}` (`id` null for empty text). 409 when closed. |
| `POST /interrupt` | `{}` → `Steering.interrupt()`: stop only; emits `x-optio-interrupt` while a turn runs. No-op when idle. |
| `POST /permission` | `{request_id, behavior: "allow"\|"deny", updated_input?, message?}` → resolves the pending permission future. 404 for unknown/already-answered request_id. |

Replay-buffer semantics:

* `collections.deque(maxlen=1000)` of raw events, stamped with a
  monotonic `seq`. Nothing goes to Mongo while the task runs; with
  `supports_resume` the teardown capture stores `export_buffer()` as the
  snapshot's `conversation` blob (see Snapshot capture), else the
  conversation view is gone after the task ends.
* Mechanical type filter, not interpretation: events of type
  `stream_event` (the partial-message deltas) and `system` events of
  subtype `thinking_tokens` (a reasoning-token count the widget ignores,
  which filled up to 60% of the buffer; owner request 2026-10-08) are
  forwarded live but never buffered. A re-primed buffer drops them too,
  for buffers persisted before. Everything else — `system`, `user`,
  `assistant`, `result`, `control_request`, `x-optio-*` — is buffered.
* The engine channels raw stream-json events through untouched; all
  interpretation happens client-side in `optio-conversation-ui`. The
  listener adds synthetic events of its own:
  `{"type": "x-optio-permission-answered", "request_id": ..., "behavior": ...}`,
  broadcast (and buffered) when a permission is answered so every
  viewer sees the card resolve; `{"type": "x-optio-resumed"}` (below);
  `{"type": "x-optio-message-start", "id", "ts"}` (Fix 12); and, at a
  session end mid-turn (Fix 19, below), `{"type": "x-optio-partial", "id",
  "text"}` and `{"type": "x-optio-interrupt", "by": "session"}`.
* `x-optio-message-start` (Fix 12, owner ruling 2026-09-14: an assistant
  message's time label becomes a "HH:MM - HH:MM" interval when streaming
  crosses a minute, so the UI needs an exact start). `stream_event` frames
  carry no time and are never buffered (above), so a streamed message's true
  start would otherwise only exist live, at the moment `message_start`
  passes through, and be lost to replay. The listener stamps it instead: for
  every `stream_event` whose inner event is `message_start`, it broadcasts
  `{"type": "x-optio-message-start", "id": <message.id>, "ts": <epoch ms,
  server clock at receipt>}` — buffered and persisted like a native event —
  immediately BEFORE forwarding that `stream_event` (still unbuffered, as
  today). `id` matches the assistant event's own `message.id`. The clock is
  injectable (`ConversationListener(..., clock=...)`) for deterministic
  tests; only the listener ever stamps this, so live and a later replay of
  the persisted buffer show the exact same value.
* Steering events (`optio_claudecode.steering`, `busy_send` =
  `joins-next-step` via `BUSY_SEND`): `{"type": "x-optio-queued", "id",
  "text"}` for a Send when ready (or an Interrupt and send's text) that
  arrived mid-turn, emitted when it is sent, not when it reaches Claude
  (a Send when ready that waits adds `"waiting": true`, which makes the
  widget pin it as queued even before its stream shows Claude busy, e.g.
  right after the resume notice),
  and `{"type": "x-optio-interrupt", "by": "user"}` before every interrupt
  optio sends while a turn runs. Both enter through `emit_event`, so they
  are buffered, replayed and persisted like native events. `{"type":
  "x-optio-requeued", "id", "new_id"}` is no longer emitted for Fix 13a's
  `upTo` re-send (Fix 17); since Fix 19 it announces each message a
  resumed run re-sends (see Resume below). Buffers recorded before Fix 17
  still contain the old kind, and the UI handles both.
* Fix 13a (owner rulings from manual testing, 2026-09-15): every stdin
  message `ClaudeCodeConversation.send(text, *, uuid=None)` writes carries a
  `uuid` (never a `priority`) — the ONLY thing that turns on the CLI's
  `command_lifecycle` events for that message. A message through Steering
  gets its own steering id as that uuid (bubble id, response id and CLI
  uuid are one value); a call outside Steering (session.py's agent
  feedback, the first prompt) omits it and gets a fresh `uuid4` minted
  inside `send()`. A message written directly onto Claude's own native
  queue (the busy path of `send_when_ready` and `interrupt_and_send`) gets
  a trailing blank line unless it already ends with one: several messages
  queued together otherwise reach the model run together with no
  separator, because the CLI folds them into one turn with one text block
  each (`x-optio-queued` itself still carries the ORIGINAL text). Idle
  sends are unchanged. `ClaudeCodeConversation.cancel_async_message(uuid)`
  sends `cancel_async_message` and returns the reply's `cancelled` bool (a
  transport capability; Steering no longer uses it since Fix 17).
  `optio_claudecode.steering.command_lifecycle(event)` extracts
  `(command_uuid, state)` from a `command_lifecycle` event, and
  `make_steering` always wires it into `Steering` (it is a pure function).
* One at a time (Fix 17, owner ruling 2026-09-15;
  `docs/2026-09-15-steering-individual-delivery-design.md`). Claude Code
  folds every prompt waiting in its stdin queue into ONE user message when
  it starts a turn, but takes queued prompts one by one at tool
  boundaries, and no `priority` value changes that (`now` interrupts the
  turn and jumps the queue; `later` is never taken mid-turn and is still
  folded). So `Steering` keeps at most ONE optio message in the CLI queue:
  the others wait in its pending list, and the next is written when the
  CLI reports the one in flight `started`, or `completed` / `cancelled` /
  `discarded` / `refused` (each can arrive without `started`). With tool
  calls, each message joins the running turn at the next tool boundary; in
  a text-only turn, each becomes its own turn after the current one (N
  messages = N turns, a cost the owner accepted). After an interrupt the
  CLI runs the message in flight first (the interrupt reply lists it under
  `still_queued`), and the model reads the CLI's "[Request interrupted by
  user]" marker in that same user turn. A CLI that emits no lifecycle
  still advances: a conversation that is idle (`is_pending()` False) holds
  nothing in its queue, so the next message goes when the turn ends idle.
  Messages still pending when the session ends are never written.
* Relaunch (Fix 25, final-review-2 I3 / ledger line 399; latched and
  hardened by Fix 29/W1-W3, wave-2 re-review). Since model and effort switch
  live (custom session controls), `session.py` no longer relaunches; the
  transport support below stays for a future relaunch, and describes the
  relaunch arm `session.py` had. A relaunch kills
  `claude` mid-turn and relaunches it with `--continue`: the
  dead process takes its in-flight steering message with it, so neither its
  `command_lifecycle` nor the turn's own `result` will ever arrive for it.
  The relaunch arm calls `conversation.begin_restart()` AND
  `ConversationListener.begin_transport_reset()` (→
  `Steering.begin_transport_reset()`) BEFORE the kill: the latch stops the
  dying process's own last gasp — an abort `result` + trailing idle, or a
  shutdown sweep's `command_lifecycle` `cancelled`/`discarded`/`refused` —
  from clearing `in_flight_id` first and leaving `reset_transport()` below
  nothing to rescue (Fix 29/W1). `begin_restart()` also fails every
  outstanding `control_request` ack (e.g. an Interrupt awaiting its
  response) with `ConversationClosed`, so `Steering.interrupt()` — which,
  unlike `_interrupt_and_wait`, has no deadline of its own — cannot hang
  forever across the swap (Fix 29/W3). The kill is graceful
  (`aggressive=False`), and the reader task is DRAINED across the dying
  process's own real EOF (bounded by `GRACEFUL_INTERRUPT_TIMEOUT_S`, falling
  back to a hard cancel only on timeout) instead of being cancelled outright
  — a `reader_task.cancel()` would discard whatever the process wrote to the
  pipe that had not yet been read, e.g. a `command_lifecycle` `started` for
  the in-flight message, which is exactly what tells Steering not to write
  it a second time on top of what `--continue` already restored (Fix
  29/W2). `ClaudeCodeConversation.attach()` zeroes `_pending` and
  `_sends_since_result` (alongside `_restarting`), so `is_pending()` does
  not stay stuck True from the dead turn once the new process is attached.
  The relaunch arm then calls `ConversationListener.
  reset_transport()` (the same spot `requeue_undelivered()` is called from
  for a resume), which delegates to `Steering.reset_transport()`: it clears
  the `begin_transport_reset()` latch and, unless the drained events already
  showed the message taken, re-writes it (to the transport just attached,
  not the dead one) instead of leaving it stuck forever in `in_flight_id`,
  so the queue behind it (Fix 17) keeps draining. Queued bubbles keep their
  ids and order across the relaunch; nothing here relies on a
  `session_state_changed idle`.
* Session end (Fix 19, owner rulings 2026-09-15;
  `docs/2026-09-15-steering-session-end-design.md`). A cooperative cancel
  (stop, suspend, engine shutdown) while a turn runs
  (`is_pending()`): the body's cancel path calls
  `session._end_turn_gracefully`, which (1) `begin_session_end()`, so
  neither Steering nor `POST /send` writes anything more; (2)
  `listener.announce_session_end()`, which puts `{"type":
  "x-optio-interrupt", "by": "session"}` into the event stream after
  every event already read; (3) `interrupt_for_session_end()`, bounded
  by `GRACEFUL_INTERRUPT_TIMEOUT_S` (3 s), while the reader still runs:
  the CLI records the partial answer in its transcript, and its final
  partial `assistant` event, "[Request interrupted by user]", the
  `cancelled` of the message in flight and the aborted `result` are all
  buffered. Then the reader stops. Just before `x-optio-closed` passes,
  the listener adds `{"type": "x-optio-partial", "id", "text"}` if the
  open block of the message being streamed has text whose final
  `assistant` event never came (the listener accumulates `text_delta` and
  `thinking_delta` text per block, the latter only while the task's
  `thinking_verbosity` is hidden, reset at `message_start` and at each
  final `assistant` event), and the marker if it was not announced and a
  turn runs (`running` or a `message_start` seen without its
  `result`/`idle`, or `is_pending()`). Both are in `export_buffer()`.
  claude is then SIGKILLed and the snapshot taken as before. Idle: nothing
  happens. The wait loop of the body cancels its waiter tasks when it is
  cancelled (no "Task was destroyed but it is pending!").
* Resume: `export_buffer()` persists the buffer with the snapshot, minus
  the terminal `x-optio-closed` (replayed, it would close the live resumed
  session in the UI). A resumed run reads it back with
  `session._load_conversation_buffer(ctx, *, decrypt)` (`decrypt` is
  `session_blob_decrypt`): the latest snapshot's conversation blob, only
  `[seq: int, event: dict]` entries, `x-optio-closed` dropped; absent,
  undecryptable or malformed gives an empty buffer, logged, never fatal.
  The same record, via `session._read_conversation(ctx, *, decrypt)` (set as
  `TaskInstance.read_conversation` only when `conversation_ui`), backs the
  read-only suspended-process view for the engine's conversation archive
  listener, which gives it a read-only context (process_id, _db, _prefix,
  load_blob only). That read is strict: no snapshot or no record gives
  None (404 `no-conversation`), but a record that cannot be decrypted, is
  not UTF-8 JSON or is not a list of entries raises
  `session.UnreadableConversationRecord` (its message names the failure,
  never the content), and a Mongo / GridFS error (a blob gone included)
  raises as is, so the listener answers 500 `read-failed`. Both readers
  share `session._parse_conversation_record` (decrypt + `json.loads` +
  filtering), run in `asyncio.to_thread`; the GridFS read stays async.
  The workdir restore (`_restore_workdir_snapshot`) deletes a top-level
  `.optio-conversation-buffer.json` a pre-fix snapshot may carry, unread.
  When a resumed run re-primes the listener from the buffer,
  the listener appends exactly one `{"type": "x-optio-resumed"}` after the
  restored history. It marks where the earlier run ended, since nothing
  else does: the widget stops the rows that run left running (a background
  task, a call with no result) as `stopped`, at the latest wire timestamp.
  The marker is persisted like any event, so a later resume replays it in
  place and appends its own. Fix 19 (owner ruling 2026-09-15, finding 6
  #2): `optio_claudecode.steering.undelivered_queued(events)` computes,
  from the restored buffer, what the LAST run left queued and undelivered
  (an `x-optio-queued` with no `command_lifecycle` `started`/`completed`
  and no confirming echo; `x-optio-requeued` renames it; `discarded` and a
  `cancelled` without `started` leave it undelivered; an earlier
  `x-optio-resumed` drops what older runs left). The marker then carries
  `"requeued": [ids]`, and right after the "you have been resumed" notice
  the session calls `listener.requeue_undelivered()`: `Steering.requeue`
  re-sends them in order, each under a new uuid with one `{"type":
  "x-optio-requeued", "id", "new_id"}`, one at a time (Fix 17). Nothing
  the CLI already started is re-sent.

**Handler-slot rule**: `conversation_ui=True` occupies the single
`on_permission_request` slot (the listener registers the handler and
resolves it from `POST /permission`). Consumers that want programmatic
permission gating must not enable `conversation_ui` — or must accept
that the UI is the gate.

When `False` (default): behavior is byte-identical to plain
conversation mode (`ui_widget=None`, no listener, no extra argv flags).

## Hooks

`before_execute(hook_ctx)`, `after_execute(hook_ctx)`,
`on_deliverable(hook_ctx, relative_path, decoded_text)`. Identical
signatures and failure semantics to optio-opencode.

`before_execute` fires **after** AGENTS.md and HOME files are planted
and **before** ttyd launches.

`after_execute` fires after claude exits (or after cancellation), on
both success and ERROR paths.

## Log-file contract

Same as opencode. AGENTS.md tells claude to append to `./optio.log`:

- `STATUS: [N%] <msg>`
- `DELIVERABLE: <workdir-relative-or-absolute-path>` (must resolve
  under `<workdir>/deliverables/`)
- `DONE[: summary]`
- `ERROR[: message]`

DONE / ERROR terminate the session.

## Binary install

* claude — provisioned from a shared, optio-owned version cache
  (`${OPTIO_CLAUDECODE_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/optio-claudecode/versions}`);
  the per-task home gets `home/.local/share/claude/versions` symlinked at the
  cache and `home/.local/bin/claude` pointed at the newest cached version.
  Cache miss → vendor `curl -fsSL https://claude.ai/install.sh | bash`
  (unconfined, `HOME=<workdir>/home`) writes through the symlink into the
  cache. Cache hit → launch-time freshness check against the upstream
  `latest` endpoint install.sh consults
  (`https://downloads.claude.ai/claude-code-releases/latest`); upstream newer
  → same install.sh path refreshes the cache; probe failure keeps the cached
  binary (best-effort). Sessions run with `DISABLE_AUTOUPDATER=1` — under
  claustrum the cache is `--rox`, so in-session self-update can only EACCES;
  freshness is owned by this unconfined provisioning path. See the addendum
  in `docs/2026-05-31-optio-claudecode-runtime-cache-design.md`.
  Both the iframe and conversation launches also pin
  `CLAUDE_CODE_ENABLE_TODO_TOOLS=1` and `CLAUDE_CODE_ENABLE_TASKS=1`, so
  Claude Code gets TaskCreate, TaskGet, TaskList, and TaskUpdate on models
  that omit the checklist family (Opus 5 and later). The composed
  CLAUDE.md names those four tools, and asks Claude to fill a task description
  with what it plans to do or what happened, including why a step failed,
  because that text is shown to the person supervising the process.
  `CLAUDE_CODE_ENABLE_TASKS=0` selects the legacy `TodoWrite` tool instead.
  `ClaudeCodeTaskConfig.env` is applied after these pins and can override
  either value.
  Conversation mode also pins `CLAUDE_CODE_THINKING_DISPLAY_UPDATES=1`
  (`conversation_launch_env`, set before `extra_env`, which can still
  override it). Since CLI 2.1.267 the model's between-tool narration arrives
  as text-bearing thinking blocks ("thinking updates"), which the widget
  renders as ordinary replies; the pin keeps a CLI default change from
  silently dropping that channel. See
  `docs/2026-09-12-claudecode-conversation-rendering-design.md`.
  With `thinking_verbosity="visible"` the conversation launch also passes
  `--thinking-display summarized` (`build_claude_flags(show_thinking=True)`):
  in `-p` mode the CLI omits the thinking text unless the display is set
  explicitly, so the model's reasoning reaches the stream only then (CLI
  2.1.296, measured 2026-10-12; narration still arrives as text). The widget
  then shows text-bearing thinking blocks as reasoning rows, not replies.
  Conversation mode also pins `CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS=1`
  (same function): since CLI 2.1.270 the CLI only emits
  `system/session_state_changed` (idle/running) with this set, and
  conversation steering's `is_pending()` resync (see above) depends on
  those events. Owner ruling, 2026-09-14 manual test finding (conversation
  steering stage 1, fix 3).
* ttyd — downloaded from `tsl0922/ttyd` GitHub Releases (pinned
  version). Linux x86_64/aarch64/armv7l only in v1.

Override install locations via `claude_install_dir` /
`ttyd_install_dir` (absolute paths).

## Testing

```
pytest packages/optio-claudecode/tests/
```

Needs MongoDB via Docker for the integration tests.

Fake binaries (`claude-shim.sh`, `ttyd-shim.sh`, `fake_claude.py`) live
in `tests/` and substitute the real ones during integration tests. The
ttyd shim prints a fake "Listening on http://127.0.0.1:N/" banner so
the framework's port discovery completes without opening a real socket.
The claude shim resolves its own symlink (via `readlink -f`) before
locating `fake_claude.py`, since the framework symlinks the shim into
a tmpdir.

Remote SSH automated tests are deferred to a follow-up plan. See the
design doc's "Open follow-ups" section.
