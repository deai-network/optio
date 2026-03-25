"""Core data models for feldwebel."""

from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable
from datetime import datetime


@dataclass
class CancellationConfig:
    """How this process handles cancellation."""
    cancellable: bool = True
    propagation: str = "down"  # "down", "up", "both", "none"


@dataclass
class TaskInstance:
    """A unit of work provided by the application's task generator."""
    execute: Callable[..., Awaitable[None]]
    process_id: str
    name: str
    params: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schedule: str | None = None
    special: bool = False
    warning: str | None = None
    cancellation: CancellationConfig = field(default_factory=CancellationConfig)


@dataclass
class ChildResult:
    """Result of a child process execution."""
    process_id: str
    state: str  # "done", "failed", "cancelled"
    error: str | None = None


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
class FeldwebelConfig:
    """Configuration for feldwebel initialization."""
    mongo_db: Any  # motor AsyncIOMotorDatabase
    redis_url: str
    prefix: str
    services: dict[str, Any] = field(default_factory=dict)
    get_task_definitions: Callable[..., Awaitable[list[TaskInstance]]] | None = None
