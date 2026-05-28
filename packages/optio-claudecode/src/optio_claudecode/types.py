"""Public data types for optio-claudecode consumers.

The generic ``DeliverableCallback`` / ``HookCallback`` types and
``SSHConfig`` are owned by ``optio-host``. This module re-exports them
alongside the package-specific ``ClaudeCodeTaskConfig``.
"""

from dataclasses import dataclass
from typing import Any, Literal

from optio_host.protocol.session import DeliverableCallback, HookCallback
from optio_host.types import SSHConfig


__all__ = [
    "DeliverableCallback",
    "HookCallback",
    "SSHConfig",
    "ClaudeCodeTaskConfig",
    "PermissionMode",
]


PermissionMode = Literal["default", "plan", "acceptEdits", "bypassPermissions"]
_VALID_PERMISSION_MODES = {"default", "plan", "acceptEdits", "bypassPermissions"}


@dataclass
class ClaudeCodeTaskConfig:
    """Configuration for one optio-claudecode task instance.

    See ``docs/2026-05-28-optio-claudecode-design.md`` for full field
    semantics.
    """

    consumer_instructions: str

    credentials_json: dict[str, Any] | bytes | str | None = None
    claude_config: dict[str, Any] | None = None
    env: dict[str, str] | None = None

    permission_mode: PermissionMode | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None

    ssh: SSHConfig | None = None

    install_if_missing: bool = True
    install_ttyd_if_missing: bool = True
    claude_install_dir: str | None = None
    ttyd_install_dir: str | None = None

    before_execute: HookCallback | None = None
    after_execute: HookCallback | None = None
    on_deliverable: DeliverableCallback | None = None

    def __post_init__(self) -> None:
        if self.permission_mode is not None and self.permission_mode not in _VALID_PERMISSION_MODES:
            raise ValueError(
                f"ClaudeCodeTaskConfig.permission_mode={self.permission_mode!r} "
                f"is not one of {sorted(_VALID_PERMISSION_MODES)}"
            )
        for field_name in ("claude_install_dir", "ttyd_install_dir"):
            val = getattr(self, field_name)
            if val is not None and not val.startswith("/") and not val.startswith("~"):
                raise ValueError(
                    f"ClaudeCodeTaskConfig.{field_name}={val!r} must be an "
                    f"absolute path (start with '/' or '~')."
                )
