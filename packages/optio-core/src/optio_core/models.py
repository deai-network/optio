"""Core data models for optio."""

from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Literal, Union, TypeAlias
from datetime import datetime


@dataclass(frozen=True)
class LaunchOutcome:
    """Result of Optio.launch. ok=False carries a typed reason.

    `proc` is the post-action process doc snapshot when ok=True (lifecycle
    resolves it after the state transition); None on failure.
    """
    ok: bool
    reason: Literal[
        "not-found", "not-launchable", "launch-blocked", "no-resume-support",
        "shutting-down",
    ] | None = None
    proc: dict[str, Any] | None = None


@dataclass(frozen=True)
class CancelOutcome:
    """Result of Optio.cancel.

    `proc` is the post-action process doc snapshot when ok=True; None on
    failure.
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
    get_task_definitions: Callable[..., Awaitable[list[TaskInstance]]] | None = None
    cancel_grace_seconds: float = 5.0
    # One-shot post-boot delay before auto-resume re-launches stamped
    # processes. The wait lets the environment settle (dev-mode code edits
    # cause rapid restart bursts) so we don't thrash re-launches.
    auto_resume_delay_seconds: float = 300.0


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
