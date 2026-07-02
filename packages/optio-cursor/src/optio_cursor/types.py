"""Public data types for optio-cursor consumers.

The generic ``DeliverableCallback`` / ``HookCallback`` types are owned by
``optio-agents`` and ``SSHConfig`` by ``optio-host``. This module
re-exports them alongside the package-specific ``CursorTaskConfig``.
"""

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
    "CursorTaskConfig",
    "SandboxMode",
    "ConversationMode",
]


# Passed through as ``--sandbox``. cursor-agent's own filesystem sandbox
# toggle; None omits the flag (cursor's default applies).
SandboxMode = Literal["enabled", "disabled"]
_VALID_SANDBOX_MODES = {"enabled", "disabled"}

# "iframe" = ttyd TUI in the browser (Stage 0). Later stages add
# "conversation" (headless ACP); keep the Literal single-valued until then.
ConversationMode = Literal["iframe"]


@dataclass
class CursorTaskConfig:
    """Configuration for one optio-cursor task instance (Stage 0).

    Stage 0 covers iframe/ttyd mode on the local host. Resume, seeds,
    conversation mode, and filesystem isolation arrive in later stages.
    """

    consumer_instructions: str

    env: dict[str, str] | None = None
    # Glob patterns (fnmatch) of env var NAMES to strip from the cursor-agent
    # subprocess, so inherited provider creds don't override the task's
    # own configuration. e.g. ["*_API_KEY", "*_TOKEN"].
    scrub_env: list[str] | None = None

    # Permission rules are config-planted, not argv: cursor-agent has no
    # --allow/--deny flags. These map to ``permissions.allow`` /
    # ``permissions.deny`` in ``<home>/.cursor/cli-config.json``.
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    # ``--force``: skip cursor's interactive confirmation prompts.
    force: bool = False
    # ``--auto-review``: cursor reviews its own edits before applying.
    auto_review: bool = False
    # ``--sandbox enabled|disabled``: cursor-agent's native sandbox toggle.
    sandbox: SandboxMode | None = None
    # Passed through as ``--model``. Not validated — vendor strings change.
    model: str | None = None
    # Injected as ``CURSOR_API_KEY`` in the launch env, never argv.
    api_key: str | None = None

    ssh: SSHConfig | None = None

    install_if_missing: bool = True
    install_ttyd_if_missing: bool = True
    # Override for where the ``cursor-agent`` binary is resolved on the host.
    cursor_install_dir: str | None = None
    ttyd_install_dir: str | None = None

    # When True, a fresh launch passes a trailing positional prompt
    # ("Read AGENTS.md and execute the task it describes") so cursor-agent
    # starts the task unattended.
    auto_start: bool = True

    before_execute: HookCallback | None = None
    after_execute: HookCallback | None = None
    on_deliverable: DeliverableCallback | None = None

    # Resume machinery (Stage 2). ON by default: cursor persists its chat
    # state under $HOME/.cursor inside the workdir, so restoring the workdir
    # tar + passing --continue rehydrates the conversation.
    supports_resume: bool = True
    # fnmatch patterns of workdir paths to omit from the resume snapshot tar.
    # None → the framework defaults (see optio_host.archive). Keep home/.cursor
    # OUT of this list: it carries the cursor chat state --continue needs.
    workdir_exclude: list[str] | None = None

    # "iframe" = ttyd TUI (Stage 0). Later stages add "conversation".
    mode: ConversationMode = "iframe"
    # Opt-out for the optio.log keyword channel (STATUS/DELIVERABLE/DONE/…).
    # iframe mode requires True (it is the only completion signal there).
    host_protocol: bool = True

    def __post_init__(self) -> None:
        if self.sandbox is not None and self.sandbox not in _VALID_SANDBOX_MODES:
            raise ValueError(
                f"CursorTaskConfig.sandbox={self.sandbox!r} "
                f"is not one of {sorted(_VALID_SANDBOX_MODES)}"
            )
        if self.mode != "iframe":
            raise ValueError(
                f"CursorTaskConfig.mode={self.mode!r} is not one of ['iframe']"
            )
        if self.mode == "iframe" and not self.host_protocol:
            raise ValueError(
                "CursorTaskConfig: host_protocol=False requires a "
                "non-iframe mode (in iframe mode the optio.log keyword "
                "channel is the only completion signal)."
            )
        for field_name in ("cursor_install_dir", "ttyd_install_dir"):
            val = getattr(self, field_name)
            if val is not None and not val.startswith("/") and not val.startswith("~"):
                raise ValueError(
                    f"CursorTaskConfig.{field_name}={val!r} must be an "
                    f"absolute path (start with '/' or '~')."
                )
