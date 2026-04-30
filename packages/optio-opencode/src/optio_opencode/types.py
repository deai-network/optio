"""Public data types for optio-opencode consumers.

The generic ``DeliverableCallback`` / ``HookCallback`` types and
``SSHConfig`` are owned by ``optio-host`` (since they describe the
log/deliverables protocol and SSH config in general). This module
re-exports them so existing ``from optio_opencode.types import ...``
imports keep working unchanged.
"""

from dataclasses import dataclass, field
from typing import Any

from optio_host.protocol.session import DeliverableCallback, HookCallback
from optio_host.types import SSHConfig


__all__ = ["DeliverableCallback", "HookCallback", "SSHConfig", "OpencodeTaskConfig"]


@dataclass
class OpencodeTaskConfig:
    """Configuration for one optio-opencode task instance."""
    consumer_instructions: str
    opencode_config: dict[str, Any] = field(default_factory=dict)
    ssh: SSHConfig | None = None
    on_deliverable: DeliverableCallback | None = None
    install_if_missing: bool = True
    workdir_exclude: list[str] | None = None
    supports_resume: bool = True
    before_execute: HookCallback | None = None
    after_execute: HookCallback | None = None
