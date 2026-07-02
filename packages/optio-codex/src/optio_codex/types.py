"""Public data types for optio-codex consumers."""

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
    "CodexTaskConfig",
    "IframeMode",
    "ApprovalPolicy",
    "SandboxMode",
    "SeedProvider",
    "SeedUnavailableError",
]


IframeMode = Literal["iframe"]
_VALID_MODES = {"iframe"}

ApprovalPolicy = Literal["untrusted", "on-failure", "on-request", "never"]
_VALID_APPROVAL_POLICIES = {"untrusted", "on-failure", "on-request", "never"}

SandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]
_VALID_SANDBOX_MODES = {"read-only", "workspace-write", "danger-full-access"}


# A seed provider resolves a usable seed_id at launch time (e.g. leasing one
# from a pool). Mirrors optio-grok's SeedProvider; the callable/lease path
# is exercised by the Stage-4 wiring — a static string seed_id carries no
# lease.
SeedProvider = Callable[[str], Awaitable[str]]


class SeedUnavailableError(Exception):
    """Raised by a seed provider when no usable seed is available; the
    message is surfaced as the process failure."""


@dataclass
class CodexTaskConfig:
    """Configuration for one optio-codex task instance (Stage 0).

    Stages 0-2 cover iframe/ttyd mode on local and SSH-remote hosts with
    resume/snapshots. Seeds, conversation mode, and filesystem isolation
    arrive in later stages.
    """

    consumer_instructions: str

    env: dict[str, str] | None = None
    scrub_env: list[str] | None = None

    model: str | None = None
    # Interactive iframe defaults: unattended launch in ttyd (mirrors claudecode
    # bypassPermissions for embedded sessions nobody is watching).
    ask_for_approval: ApprovalPolicy = "never"
    sandbox: SandboxMode = "workspace-write"

    ssh: SSHConfig | None = None

    install_if_missing: bool = True
    install_ttyd_if_missing: bool = True
    codex_install_dir: str | None = None
    ttyd_install_dir: str | None = None

    auto_start: bool = True

    before_execute: HookCallback | None = None
    after_execute: HookCallback | None = None
    on_deliverable: DeliverableCallback | None = None

    # --- seed surface (start fresh from a stored environment) -----------
    # Consumed (default/fallback): merge this seed's environment (codex
    # auth.json + config.toml) into a fresh workdir before launch, beginning
    # a NEW session already logged-in; the workdir is pre-trusted right
    # after the merge. A plain string is used as-is; a SeedProvider callable
    # is awaited at launch to resolve one (lease path — holder is the
    # process_id). Ignored on resume.
    seed_id: "str | SeedProvider | None" = None
    # Capture intent: a (sync or async) callback fired on teardown of a
    # fresh session after a successful capture, with two args:
    # (seed_id, info). ``info`` is a human-readable account summary (None
    # for now; resolved in a later stage). Its presence is what enables
    # seed capture. Ignored on resume.
    on_seed_saved: "Callable[[str, str | None], Awaitable[None] | None] | None" = None

    mode: IframeMode = "iframe"
    host_protocol: bool = True

    # Resume/snapshots (Stage 2). When True (default) the session captures a
    # workdir snapshot — plus the codex sessionId — at teardown, and a later
    # run with ctx.resume=True restores it and relaunches via
    # `codex resume <id>`.
    supports_resume: bool = True
    # Snapshot exclude list. None (default) resolves to
    # optio_codex.snapshots.CODEX_WORKDIR_EXCLUDE_DEFAULT (framework defaults
    # + CODEX_HOME junk: packages/, *.sqlite*, cache/, tmp/, …). MUST NOT be
    # set to exclude home/.codex/sessions — that is the resume source.
    workdir_exclude: list[str] | None = None

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
        if self.ask_for_approval not in _VALID_APPROVAL_POLICIES:
            raise ValueError(
                f"CodexTaskConfig.ask_for_approval={self.ask_for_approval!r} "
                f"is not one of {sorted(_VALID_APPROVAL_POLICIES)}"
            )
        if self.sandbox not in _VALID_SANDBOX_MODES:
            raise ValueError(
                f"CodexTaskConfig.sandbox={self.sandbox!r} "
                f"is not one of {sorted(_VALID_SANDBOX_MODES)}"
            )
        for field_name in ("codex_install_dir", "ttyd_install_dir"):
            val = getattr(self, field_name)
            if val is not None and not val.startswith("/") and not val.startswith("~"):
                raise ValueError(
                    f"CodexTaskConfig.{field_name}={val!r} must be an "
                    f"absolute path (start with '/' or '~')."
                )