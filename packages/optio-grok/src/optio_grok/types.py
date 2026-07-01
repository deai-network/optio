"""Public data types for optio-grok consumers.

The generic ``DeliverableCallback`` / ``HookCallback`` types are owned by
``optio-agents`` and ``SSHConfig`` by ``optio-host``. This module
re-exports them alongside the package-specific ``GrokTaskConfig``.
"""

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from optio_agents.protocol.session import (
    DeliverableCallback,
    HookCallback,
)
from optio_host.types import SSHConfig


__all__ = [
    "DeliverableCallback",
    "HookCallback",
    "SSHConfig",
    "GrokTaskConfig",
    "PermissionMode",
    "SeedProvider",
    "SeedUnavailableError",
]


# A seed provider resolves a usable seed_id at launch time (e.g. leasing one
# from a pool). Mirrors optio-claudecode's SeedProvider; the callable/lease
# path is exercised in Stage 4 — Stage 3 only needs a static string seed_id.
SeedProvider = Callable[[str], Awaitable[str]]


class SeedUnavailableError(Exception):
    """Raised by a seed provider when no usable seed is available; the message
    is surfaced as the process failure."""


PermissionMode = Literal[
    "default", "acceptEdits", "auto", "dontAsk", "bypassPermissions", "plan"
]
_VALID_PERMISSION_MODES = {
    "default", "acceptEdits", "auto", "dontAsk", "bypassPermissions", "plan"
}


@dataclass
class GrokTaskConfig:
    """Configuration for one optio-grok task instance (Stage 0).

    Stage 0 covers iframe/ttyd mode on the local host. Resume, seeds,
    conversation mode, and filesystem isolation arrive in later stages.
    """

    consumer_instructions: str

    env: dict[str, str] | None = None
    # Glob patterns (fnmatch) of env var NAMES to strip from the grok
    # subprocess, so inherited provider creds don't override the task's
    # own configuration. e.g. ["*_API_KEY", "*_TOKEN"].
    scrub_env: list[str] | None = None

    permission_mode: PermissionMode | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    # Passed through as ``--model``/``--effort``/``--reasoning-effort``.
    # Not validated — vendor strings change.
    model: str | None = None
    effort: str | None = None
    reasoning_effort: str | None = None

    ssh: SSHConfig | None = None

    install_if_missing: bool = True
    install_ttyd_if_missing: bool = True
    # Override for where the ``grok`` binary is resolved on the host.
    grok_install_dir: str | None = None
    ttyd_install_dir: str | None = None

    # When True, a fresh launch passes a trailing positional prompt
    # ("Read AGENTS.md and execute the task it describes") so grok starts
    # the task unattended.
    auto_start: bool = True
    # Always pass ``--no-leader`` so tasks never share a grok backend and
    # never touch ~/.grok/leader.sock.
    no_leader: bool = True

    before_execute: HookCallback | None = None
    after_execute: HookCallback | None = None
    on_deliverable: DeliverableCallback | None = None

    # --- seed surface (start fresh from a stored environment) -----------
    # Consumed (default/fallback): merge this seed's environment (grok
    # auth.json + config.toml) into a fresh workdir before launch, beginning
    # a NEW session already logged-in. A plain string is used as-is; a
    # SeedProvider callable is awaited at launch to resolve one (Stage 4
    # lease path). Baked at task-creation time; ignored on resume.
    seed_id: "str | SeedProvider | None" = None
    # Capture intent: a (sync or async) callback fired on teardown of a fresh
    # session after a successful capture, with two args: (seed_id, info).
    # ``info`` is a human-readable account summary (None in Stage 3; resolved
    # in a later stage). Its presence is what enables seed capture. Both
    # default None, so existing consumers are unaffected. Both are ignored on
    # resume.
    on_seed_saved: "Callable[[str, str | None], Awaitable[None] | None] | None" = None

    # Resume machinery (Stage 2). ON by default: grok persists its session
    # under <GROK_HOME>/sessions inside the workdir, so restoring the workdir
    # tar + passing --continue rehydrates the conversation.
    supports_resume: bool = True
    # fnmatch patterns of workdir paths to omit from the resume snapshot tar.
    # None → the framework defaults (see optio_host.archive). Keep home/.grok
    # OUT of this list: it carries the grok session state that --continue needs.
    workdir_exclude: list[str] | None = None

    # Stage 0 is iframe/ttyd only.
    mode: Literal["iframe"] = "iframe"
    # Opt-out for the optio.log keyword channel (STATUS/DELIVERABLE/DONE/…).
    # iframe mode requires True (it is the only completion signal there).
    host_protocol: bool = True

    def __post_init__(self) -> None:
        if (
            self.permission_mode is not None
            and self.permission_mode not in _VALID_PERMISSION_MODES
        ):
            raise ValueError(
                f"GrokTaskConfig.permission_mode={self.permission_mode!r} "
                f"is not one of {sorted(_VALID_PERMISSION_MODES)}"
            )
        for field_name in ("grok_install_dir", "ttyd_install_dir"):
            val = getattr(self, field_name)
            if val is not None and not val.startswith("/") and not val.startswith("~"):
                raise ValueError(
                    f"GrokTaskConfig.{field_name}={val!r} must be an "
                    f"absolute path (start with '/' or '~')."
                )
