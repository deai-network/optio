"""Public data types for optio-opencode consumers."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from optio_host.types import SSHConfig

if TYPE_CHECKING:
    from optio_host.context import HookContext


# DeliverableCallback receives the same HookContext as before/after_execute,
# so callbacks no longer need to close over ctx. Breaking change vs. the
# pre-hooks signature `Callable[[str, str], Awaitable[None]]`.
DeliverableCallback = Callable[["HookContext", str, str], Awaitable[None]]
"""Consumer callback invoked per fetched DELIVERABLE.

Arguments: ``(hook_ctx, deliverable_path, decoded_text)``.

``deliverable_path`` is the path of the deliverable file relative to
``<workdir>/deliverables/`` (e.g. ``"summary.md"`` or
``"sub/summary.md"``). It is the same value that appears in the
auto-emitted ``"Deliverable: <path>"`` progress message.
"""


HookCallback = Callable[["HookContext"], Awaitable[None]]
"""Hook callback receiving a HookContext. Used by before_execute and after_execute."""


# Re-export SSHConfig for back-compat: existing optio_opencode consumers
# (excavator engine, others) import SSHConfig from optio_opencode.types.
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
