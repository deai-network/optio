"""Core data models for optio."""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Literal, Union, TypeAlias
from datetime import datetime


@dataclass(frozen=True)
class LaunchOutcome:
    """Result of Optio.launch. ok=False carries a typed reason.

    `proc` is the post-action process doc snapshot when ok=True (lifecycle
    resolves it after the state transition; an ephemeral record already
    deleted by then, its run having ended, is the one read before the
    action); None on failure. A TaskFactory's launch answers its child's
    record. `message` is the refusal's text for "invalid-parameters"
    ("<id>: <what>"); None otherwise.
    """
    ok: bool
    reason: Literal[
        "not-found", "not-launchable", "launch-blocked", "no-resume-support",
        "shutting-down", "parameters-required", "not-a-factory",
        "invalid-parameters",
    ] | None = None
    proc: dict[str, Any] | None = None
    message: str | None = None


@dataclass(frozen=True)
class CancelOutcome:
    """Result of Optio.cancel.

    `proc` is the post-action process doc snapshot when ok=True (an
    ephemeral record already deleted by then, its run having ended on the
    cancel or been settled as an orphan, is the one read before it); None
    on failure.
    """
    ok: bool
    reason: Literal["not-found", "not-cancellable"] | None = None
    proc: dict[str, Any] | None = None


@dataclass(frozen=True)
class DismissOutcome:
    """Result of Optio.dismiss.

    `proc` is the post-action process doc snapshot when ok=True; None on
    failure.
    """
    ok: bool
    reason: Literal["not-found", "not-dismissable"] | None = None
    proc: dict[str, Any] | None = None


@dataclass(frozen=True)
class ResurrectOutcome:
    """Result of Optio.resurrect. ok=True means the save was started in the
    background; `proc` is the process doc at that moment."""
    ok: bool
    reason: Literal[
        "not-found", "not-resurrectable", "no-resurrect-support",
        "resurrect-in-progress", "launch-blocked", "shutting-down",
    ] | None = None
    proc: dict[str, Any] | None = None


@dataclass
class TaskInstanceCore:
    """The subset of TaskInstance fields that apply to child execution.

    Children inherit metadata/cancellation-policy/ttl from their parent and
    don't have schedules or top-level UI markers — so the fields here are
    exactly what ProcessContext.run_child_task needs to run a TaskInstance
    as a child process.
    """
    execute: Callable[..., Awaitable[None]]
    process_id: str
    name: str
    description: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionCrypto:
    """A task's at-rest cipher for its own encrypted blob channels.

    Each member is `Callable[[bytes], bytes] | None`; `None` means "no
    encryption configured" (the blob is stored/read as plaintext). Filled by
    an engine wrapper's task factory from its config's
    `session_blob_encrypt` / `session_blob_decrypt` (see
    `optio_agents.config_types.BlobCryptoConfigMixin`); read back by code
    that resolves a process's `TaskInstance` outside of a running task (e.g.
    a deferred migration — see `optio_core.deferred_migrations`) and needs
    its cipher to transform a blob it is rewriting. Spec:
    docs/2026-10-11-conversation-buffer-encryption-design.md §4.
    """
    encrypt: Callable[[bytes], bytes] | None = None
    decrypt: Callable[[bytes], bytes] | None = None


@dataclass
class TaskInstance(TaskInstanceCore):
    """A unit of work provided by the application's task generator."""
    metadata: dict[str, Any] = field(default_factory=dict)
    schedule: str | None = None
    special: bool = False
    warning: str | None = None
    cancellable: bool = True
    ui_widget: str | None = None
    supports_resume: bool = False
    ttl_seconds: int | None = None
    auto_cancel_children: bool = True
    # When True, a *top-level* process of this task that is interrupted by an
    # engine shutdown and that gracefully saved its state is re-launched
    # (resume=True) automatically after the next engine start, post-delay.
    # Requires supports_resume=True (validated at task-sync time).
    auto_resume: bool = False
    # Optional "resurrect" hook: saves the work a failed run left on its host
    # (snapshot + mark_has_saved_state) and removes the host leftovers. Run by
    # Optio.resurrect outside `execute`, followed by a resume. Raises
    # NothingToResurrect when nothing is left to save. Requires
    # supports_resume=True (validated at task-sync time).
    resurrect: Callable[..., Awaitable[None]] | None = None
    # Second (0-59) within each matching minute at which `schedule` fires;
    # None fires at second 0. Lets an application spread many minute-aligned
    # schedules (e.g. one heartbeat per item) across the minute.
    schedule_offset_seconds: int | None = None
    # This task's at-rest cipher for its own encrypted blob channels (e.g.
    # the session blob). None means the task configures no encryption.
    # Filled by engine-wrapper task factories (e.g. optio-claudecode's, from
    # its config's session_blob_encrypt/decrypt); read by code that resolves
    # a process's TaskInstance outside a running task, such as a deferred
    # migration. Spec: docs/2026-10-11-conversation-buffer-encryption-design.md §4.
    session_crypto: SessionCrypto | None = None
    # Optional "read conversation" hook: returns the conversation record a
    # suspended process saved, for a read-only view, without resuming it. Called
    # by the engine's conversation archive listener (optio_core.conversation_archive)
    # only for a suspended process (cancelled + supportsResume + hasSavedState),
    # with a conversation_archive.ReadOnlyProcessContext: a view of the detached
    # ProcessContext Optio.resurrect also builds, exposing only process_id, _db,
    # _prefix and load_blob (anything else raises AttributeError). Returns
    # either a list of [seq, event] entries (capturedAt is then null), or a dict
    # {"events": [[seq, event], ...], "capturedAt": <ISO string or datetime of
    # the record, or None>}; None (or a dict whose "events" is None) means "no
    # record". It runs in the engine, so it may decrypt; it must not write. At
    # most 4 run at once.
    # Spec: docs/2026-10-12-suspended-conversation-view-design.md §1.
    read_conversation: Callable[..., Awaitable[list | dict | None]] | None = None


@dataclass
class TaskFactory:
    """A task launched with values, provided by the application's task
    generator beside its TaskInstances.

    `parameters` are vultus-core field definitions as plain data (checked at
    task sync; see optio_core.task_factory). The factory is synced as a
    process record with `taskFactory: true` and its `parameters`, and never
    runs itself: a launch with values defines an ad-hoc child of that record
    whose TaskInstance has `execute`, `params` = the values and the fields
    below, and launches it. A launch is ephemeral unless `ttl_seconds` is set.
    A factory has no schedule, resurrect or auto-resume, and its own record
    never resumes; its launches may (below).
    Exactly one of `execute` and `task_for` is set (sync refuses otherwise):
    `execute` is every launch's task, reading its values from ctx.params;
    `task_for` builds a launch's whole TaskInstance from its values. A kept
    launch whose task supports resume (an `execute` factory's
    `supports_resume`, a `task_for` instance's) is resumed as a task is,
    after an engine restart too: init() rebuilds its task from the factory
    and its stored values. A factory changed by a re-sync keeps its
    launches' existing tasks until the next restart, which rebuilds them
    from the new definition.
    Spec: docs/2026-10-20-task-factory-design.md §1,
    docs/2026-10-22-task-factory-launch-options-design.md §1.4, §1.5.
    """
    process_id: str
    name: str
    parameters: list[dict[str, Any]]
    # A launch's task; reads ctx.params.
    execute: Callable[..., Awaitable[None]] | None = None
    description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    special: bool = False
    # Shown before the launch form, as a task's warning before its launch.
    warning: str | None = None
    cancellable: bool = True
    # With `execute`: a launch's task supports resume (TaskInstance's
    # supports_resume). With `task_for` the instance's rules, and sync
    # refuses True here.
    supports_resume: bool = False
    # A launch's widget (the factory's own record shows none).
    ui_widget: str | None = None
    # None: a launch is ephemeral (deleted when it ends); set: a launch is
    # kept that long after it ends.
    ttl_seconds: int | None = None
    auto_cancel_children: bool = True
    # Beyond the fields' own checks: a returned message ("<id>: <what>")
    # refuses the values.
    validate: Callable[[dict[str, Any]], str | None] | None = None
    # A launch's name, from its values; default `name`.
    name_for: Callable[[dict[str, Any]], str] | None = None
    # Instead of `execute`: `(values, process_id) -> TaskInstance`, a launch's
    # task, called after the values are checked (without the no-values) with
    # the launch's process id. optio sets the instance's process_id, params,
    # metadata (the factory's over the instance's, plus `taskFactory`), name
    # (`name_for`'s, if any) and ttl_seconds; the rest is the instance's.
    task_for: Callable[[dict[str, Any], str], TaskInstance] | None = None


@dataclass
class ChildResult:
    """Result of a child process execution."""
    process_id: str
    state: str  # "done", "failed", "cancelled"
    error: str | None = None
    name: str = ""
    original_exception: BaseException | None = None


@dataclass
class ChildOutcome:
    """Return value of ProcessContext.run_child.

    state: "done" | "failed" | "cancelled".
    original_exception: the exception object raised inside the child's
        execute function, if any. None for state in {"done", "cancelled"}
        and for "failed" only when the child failed via the no-execute-fn
        early-fail path (no real exception was raised).
    """
    state: str
    original_exception: BaseException | None = None


class ChildHandle:
    """Returned by ProcessContext.run_child_with_result /
    run_child_task_with_result: the child's published result object,
    available while the child keeps running, plus access to the child's
    eventual outcome.

    ``outcome()`` awaits the underlying child execution; it returns the
    ChildOutcome or raises ChildProcessFailed exactly as a plain
    ``run_child`` call would have. It may be awaited multiple times.
    """

    def __init__(self, result: Any, task: "asyncio.Task[ChildOutcome]"):
        self.result = result
        self._task = task

    async def outcome(self) -> "ChildOutcome":
        return await self._task


@dataclass
class ChildProgressInfo:
    """Progress snapshot of a child process, delivered to parent callbacks."""
    process_id: str
    name: str
    state: str  # "scheduled", "running", "done", "failed", "cancelled"
    percent: float | None = None
    message: str | None = None


@dataclass
class ProcessStatus:
    """Runtime status of a process."""
    state: str = "idle"
    error: str | None = None
    running_since: datetime | None = None
    done_at: datetime | None = None
    duration: float | None = None
    failed_at: datetime | None = None
    stopped_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "error": self.error,
            "runningSince": self.running_since,
            "doneAt": self.done_at,
            "duration": self.duration,
            "failedAt": self.failed_at,
            "stoppedAt": self.stopped_at,
        }


@dataclass
class Progress:
    """Progress of a running process. percent=None means indeterminate."""
    percent: float | None = 0.0
    message: str | None = None

    def to_dict(self) -> dict:
        return {"percent": self.percent, "message": self.message}


@dataclass
class OptioConfig:
    """Configuration for optio initialization."""
    mongo_db: Any  # motor AsyncIOMotorDatabase
    prefix: str = "optio"
    redis_url: str | None = None
    services: dict[str, Any] = field(default_factory=dict)
    get_task_definitions: Callable[
        ..., Awaitable[list[TaskInstance | TaskFactory]],
    ] | None = None
    cancel_grace_seconds: float = 5.0
    # One-shot post-boot delay before auto-resume re-launches stamped
    # processes. The wait lets the environment settle (dev-mode code edits
    # cause rapid restart bursts) so we don't thrash re-launches.
    auto_resume_delay_seconds: float = 300.0
    # Timing knobs for the cancel/wait machinery. Defaults reproduce the
    # historical hard-coded behaviour; tests inject tiny values so the
    # deadline/cancel/shutdown suites don't spend real wall-clock seconds
    # (which also makes them safe to run under pytest-xdist).
    #
    # Extra budget added on top of cancel_grace_seconds before *_and_wait
    # gives up and raises TimeoutError (the internal ceiling).
    cancel_wait_ceiling_margin_seconds: float = 25.0
    # Extra budget on top of the cancel grace during shutdown drain.
    shutdown_drain_margin_seconds: float = 5.0
    # Poll interval while waiting for a process to reach a terminal state.
    terminal_poll_interval_seconds: float = 0.1
    # How long force_cancel shields the task after issuing Task.cancel()
    # before giving up on a clean unwind.
    force_cancel_shield_seconds: float = 2.0


@dataclass(frozen=True)
class MongoStore:
    """The (db, prefix) pair identifying where optio's Mongo data lives.

    Exposed by an initialized Optio instance (``optio.store``) so consumers can
    hand the whole binding to helpers that need db+prefix (e.g. seed APIs)
    instead of threading the two through their own config. ``db`` is the motor
    AsyncIOMotorDatabase; ``prefix`` is the collection/stream namespace.
    """
    db: Any
    prefix: str


@dataclass
class BasicAuth:
    username: str
    password: str

    def to_dict(self) -> dict:
        return {"kind": "basic", "username": self.username, "password": self.password}


@dataclass
class QueryAuth:
    name: str
    value: str

    def to_dict(self) -> dict:
        return {"kind": "query", "name": self.name, "value": self.value}


@dataclass
class HeaderAuth:
    name: str
    value: str

    def to_dict(self) -> dict:
        return {"kind": "header", "name": self.name, "value": self.value}


InnerAuth = Union[BasicAuth, QueryAuth, HeaderAuth]


ProcessMetadataFilter: TypeAlias = dict[str, Any]


def matches_filter(
    metadata: dict[str, Any],
    metadata_filter: ProcessMetadataFilter | None,
) -> bool:
    """Return True iff every key in `metadata_filter` is present and equal in `metadata`.

    A `None` or empty `metadata_filter` matches anything (used to mean "no filter").
    """
    if not metadata_filter:
        return True
    return all(metadata.get(k) == v for k, v in metadata_filter.items())


class LaunchBlocked(RuntimeError):
    """Raised when a launch is rejected by an active launch block.

    The exception message includes both the matching filter and the
    task metadata so the rejection is traceable from logs alone.
    """
