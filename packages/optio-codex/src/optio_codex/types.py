"""Public data types for optio-codex consumers."""

from dataclasses import dataclass
from typing import Literal

from optio_agents.protocol.session import (
    DeliverableCallback,
    HookCallback,
)
from optio_host.types import SSHConfig


__all__ = [
    "DeliverableCallback",
    "HookCallback",
    "SSHConfig",
    "CodexTaskConfig",
    "IframeMode",
]


IframeMode = Literal["iframe"]
_VALID_MODES = {"iframe"}


@dataclass
class CodexTaskConfig:
    """Configuration for one optio-codex task instance (Stage 0).

    Stage 0 covers iframe/ttyd mode on the local host. Remote SSH,
    resume, seeds, conversation mode, and filesystem isolation arrive in
    later stages.
    """

    consumer_instructions: str

    env: dict[str, str] | None = None
    scrub_env: list[str] | None = None

    model: str | None = None

    ssh: SSHConfig | None = None

    install_if_missing: bool = True
    install_ttyd_if_missing: bool = True
    codex_install_dir: str | None = None
    ttyd_install_dir: str | None = None

    auto_start: bool = True

    before_execute: HookCallback | None = None
    after_execute: HookCallback | None = None
    on_deliverable: DeliverableCallback | None = None

    mode: IframeMode = "iframe"
    host_protocol: bool = True

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(
                f"CodexTaskConfig.mode={self.mode!r} is not one of "
                f"{sorted(_VALID_MODES)}"
            )
        if self.mode == "iframe" and not self.host_protocol:
            raise ValueError(
                "CodexTaskConfig: host_protocol=False requires "
                "mode='conversation' (not implemented in Stage 0; iframe "
                "mode's only completion signal is the optio.log keyword "
                "channel)."
            )
        for field_name in ("codex_install_dir", "ttyd_install_dir"):
            val = getattr(self, field_name)
            if val is not None and not val.startswith("/") and not val.startswith("~"):
                raise ValueError(
                    f"CodexTaskConfig.{field_name}={val!r} must be an "
                    f"absolute path (start with '/' or '~')."
                )