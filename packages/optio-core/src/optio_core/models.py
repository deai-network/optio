"""Core data models for optio."""

from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Union
from datetime import datetime


@dataclass
class TaskInstance:
    """A unit of work provided by the application's task generator."""
    execute: Callable[..., Awaitable[None]]
    process_id: str
    name: str
    description: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schedule: str | None = None
    special: bool = False
    warning: str | None = None
    cancellable: bool = True
    ui_widget: str | None = None
    supports_resume: bool = False


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
class OptioConfig:
    """Configuration for optio initialization."""
    mongo_db: Any  # motor AsyncIOMotorDatabase
    prefix: str = "optio"
    redis_url: str | None = None
    services: dict[str, Any] = field(default_factory=dict)
    get_task_definitions: Callable[..., Awaitable[list[TaskInstance]]] | None = None


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
