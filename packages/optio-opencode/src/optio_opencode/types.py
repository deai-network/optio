"""Public data types for optio-opencode consumers."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from optio_opencode.hook_context import HookContext


# DeliverableCallback now receives the same HookContext as before/after_execute,
# so callbacks no longer need to close over ctx. Breaking change vs. the
# pre-hooks signature `Callable[[str, str], Awaitable[None]]`.
DeliverableCallback = Callable[["HookContext", str, str], Awaitable[None]]
"""Consumer callback invoked per fetched DELIVERABLE.

Arguments: (hook_ctx, remote_path, decoded_text).
"""


HookCallback = Callable[["HookContext"], Awaitable[None]]
"""Hook callback receiving a HookContext. Used by before_execute and after_execute."""


@dataclass
class SSHConfig:
    """SSH connection parameters for remote-mode optio-opencode.

    Known-hosts verification is disabled in MVP; asyncssh's
    ``known_hosts=None`` equivalent is used by the host layer.
    """
    host: str
    user: str
    key_path: str
    port: int = 22


@dataclass
class OpencodeTaskConfig:
    """Configuration for one optio-opencode task instance."""
    consumer_instructions: str
    opencode_config: dict[str, Any] = field(default_factory=dict)
    ssh: SSHConfig | None = None
    on_deliverable: DeliverableCallback | None = None
    install_if_missing: bool = True
    workdir_exclude: list[str] | None = None
    before_execute: HookCallback | None = None
    after_execute: HookCallback | None = None
