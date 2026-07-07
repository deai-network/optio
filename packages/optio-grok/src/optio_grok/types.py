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
from optio_agents.uploads import UploadCallback
from optio_host.types import SSHConfig


__all__ = [
    "DeliverableCallback",
    "HookCallback",
    "UploadCallback",
    "SSHConfig",
    "GrokTaskConfig",
    "PermissionMode",
    "ConversationMode",
    "ToolVerbosity",
    "ThinkingVerbosity",
    "SeedProvider",
    "SeedUnavailableError",
    "AllowedDir",
]


@dataclass
class AllowedDir:
    """A caller-supplied extra path grant for filesystem isolation (Stage 8).

    ``mode`` is one of ``"ro"`` (read-only) or ``"rw"`` (read-write). Grants
    are additive: callers may widen the sandbox allowlist but never mask the
    security baseline (the workdir + temp dirs are always writable).

    grok's native sandbox is Landlock-only here (no ``deny`` list, so no
    bubblewrap dependency), so unlike optio-claudecode there is no separate
    execute bit — Landlock read/write grants cover execution. A leading
    ``~/`` in ``path`` is expanded against the REAL host home at launch time
    (the grok process runs under an isolated ``$HOME``, so grants cannot rely
    on its shell expansion).
    """

    path: str
    mode: Literal["ro", "rw"]

    def __post_init__(self) -> None:
        if self.mode not in ("ro", "rw"):
            raise ValueError(
                f"AllowedDir.mode={self.mode!r} must be one of 'ro', 'rw' "
                f"(path={self.path!r})."
            )


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

# "iframe" = ttyd TUI in the browser (Stages 0-5). "conversation" = a headless
# ``grok agent … stdio`` (ACP) session; the task publishes a live
# GrokConversation via ctx.publish_result (Stage 6).
ConversationMode = Literal["iframe", "conversation"]

# Verbosity of tool-call rendering in the conversation widget (conversation_ui
# only). Mirrors optio-claudecode; consumed by the dashboard reducer.
ToolVerbosity = Literal["silent", "description-only", "verbose"]
_VALID_TOOL_VERBOSITY = {"silent", "description-only", "verbose"}

# Visibility of reasoning/thinking traces (grok's agent_thought_chunk) in the
# conversation widget. Task-level, mirrors ToolVerbosity; the UI never decides.
ThinkingVerbosity = Literal["hidden", "visible"]
_VALID_THINKING_VERBOSITY = {"hidden", "visible"}


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

    # When True, a fresh launch kicks off the first turn itself — iframe mode
    # types a trailing positional prompt, conversation mode sends the
    # AUTO_START_PROMPT ("Read AGENTS.md and execute the task it describes"). This
    # is for UNATTENDED task execution; a task must opt in. Defaults to False
    # (parity with claudecode): a conversation/chat task must NOT auto-fire a
    # kickoff, or grok-build starts an agentic loop on launch and blocks the
    # operator's first real prompt (queued behind it as task_already_running).
    auto_start: bool = False
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

    # "iframe" = ttyd TUI (Stages 0-5). "conversation" = headless ACP
    # (grok agent stdio); the task publishes a live GrokConversation (Stage 6).
    mode: ConversationMode = "iframe"
    # Opt-out for the optio.log keyword channel (STATUS/DELIVERABLE/DONE/…).
    # iframe mode requires True (it is the only completion signal there);
    # conversation mode may set it False (the Conversation drives completion).
    host_protocol: bool = True

    # --- conversation surface (Stage 6) ---------------------------------
    # Conversation mode only: route grok's ACP session/request_permission to
    # the published conversation's on_permission_request handler (the caller
    # registers one). When False, grok launches with --always-approve so tools
    # run without prompting.
    permission_gate: bool = False
    # Opt-in dashboard conversation UI: the task starts a per-task listener and
    # publishes a live chat widget (wired in Group 6b). Conversation mode only.
    conversation_ui: bool = False
    # How much tool-call detail the conversation widget renders; only affects
    # conversation_ui rendering.
    tool_verbosity: ToolVerbosity = "description-only"

    # Whether the conversation widget shows grok's reasoning/thinking traces
    # (agent_thought_chunk). Default hidden — thinking is noisy; opt in per task.
    thinking_verbosity: ThinkingVerbosity = "hidden"

    # --- conversation frontend parity (Stage 7) -------------------------
    # Model preselected in the widget's model picker. Requires
    # mode="conversation" and conversation_ui=True. Defaults to the live ACP
    # current model when unset. (config.model still drives the launch --model
    # flag; this only controls the picker's initial value.)
    default_model: str | None = None
    # Show the engine-neutral session controls in the conversation widget.
    # Grok exposes only the model control, switched inline over ACP
    # (session/set_model) — no process restart. Requires mode="conversation"
    # and conversation_ui=True.
    show_session_controls: bool = False
    # Replace the generic working-spinner with grok's on-brand native spinner
    # in the conversation widget. Requires mode="conversation" and
    # conversation_ui=True.
    native_spinner: bool = False
    # Show the file-upload control. Uploaded bytes are written under
    # <workdir>/uploads and referenced to grok via a System: path line so it
    # reads them with its own tools. Requires mode="conversation" and
    # conversation_ui=True.
    show_file_upload: bool = False
    # Per-task upload hook, fired (additively to the System: path line) after an
    # uploaded file lands under <workdir>/uploads. Mirrors on_deliverable minus
    # the text arg: ``async on_upload(hook_ctx, "uploads/<name>")``. Optional;
    # the workdir write + System: reference happen whether or not it is set.
    on_upload: UploadCallback | None = None
    # Upper bound (bytes) on a single uploaded file; the listener rejects
    # anything larger with HTTP 413. Mirrored to the widget via widgetData.
    max_upload_bytes: int = 10_000_000
    # Offer download links for files grok marks with the optio-file: sentinel.
    # The listener serves GET /download for paths confined under <workdir>.
    # Requires mode="conversation" and conversation_ui=True.
    file_download: bool = False
    # Upper bound (bytes) on a single downloaded file; the listener rejects
    # anything larger with HTTP 413. Mirrored to the widget via widgetData.
    max_download_bytes: int = 10_000_000

    # --- filesystem isolation (Stage 8) ---------------------------------
    # Confine the grok process (and every tool/subprocess it spawns) to the
    # task workdir + temp dirs + explicit grants, kernel-enforced via grok's
    # NATIVE sandbox (Landlock on Linux). optio plants a CUSTOM
    # ``[profiles.optio]`` (extends="strict") under the per-task GROK_HOME and
    # launches with ``--sandbox optio``. Custom profiles are fail-CLOSED: if
    # the kernel can't apply them grok refuses to start (built-in profiles
    # fail-OPEN, which is why optio uses a custom one). Requires Landlock
    # (kernel >= 5.13) on the worker. Default-ON.
    fs_isolation: bool = True
    # Additional path grants beyond the workdir + temp dirs. ``~/`` expands
    # against the real host home at launch. Ignored when fs_isolation=False.
    extra_allowed_dirs: list[AllowedDir] | None = None

    def __post_init__(self) -> None:
        if (
            self.permission_mode is not None
            and self.permission_mode not in _VALID_PERMISSION_MODES
        ):
            raise ValueError(
                f"GrokTaskConfig.permission_mode={self.permission_mode!r} "
                f"is not one of {sorted(_VALID_PERMISSION_MODES)}"
            )
        if self.mode not in ("iframe", "conversation"):
            raise ValueError(
                f"GrokTaskConfig.mode={self.mode!r} is not one of "
                "['iframe', 'conversation']"
            )
        if self.mode == "iframe" and not self.host_protocol:
            raise ValueError(
                "GrokTaskConfig: host_protocol=False requires "
                "mode='conversation' (in iframe mode the optio.log keyword "
                "channel is the only completion signal)."
            )
        if self.permission_gate and self.mode != "conversation":
            raise ValueError(
                "GrokTaskConfig: permission_gate=True requires "
                "mode='conversation'."
            )
        if self.conversation_ui and self.mode != "conversation":
            raise ValueError(
                "GrokTaskConfig: conversation_ui=True requires "
                "mode='conversation'."
            )
        if self.tool_verbosity not in _VALID_TOOL_VERBOSITY:
            raise ValueError(
                f"GrokTaskConfig.tool_verbosity={self.tool_verbosity!r} "
                f"is not one of {sorted(_VALID_TOOL_VERBOSITY)}"
            )
        if self.thinking_verbosity not in _VALID_THINKING_VERBOSITY:
            raise ValueError(
                f"GrokTaskConfig.thinking_verbosity={self.thinking_verbosity!r} "
                f"is not one of {sorted(_VALID_THINKING_VERBOSITY)}"
            )
        # Frontend-parity features are opt-in flags that only make sense with
        # the conversation UI wired (mirrors optio-claudecode).
        conv_ui = self.mode == "conversation" and self.conversation_ui
        if self.default_model is not None and not conv_ui:
            raise ValueError(
                "GrokTaskConfig: default_model requires mode='conversation' "
                "and conversation_ui=True."
            )
        if self.show_session_controls and not conv_ui:
            raise ValueError(
                "GrokTaskConfig: show_session_controls=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.native_spinner and not conv_ui:
            raise ValueError(
                "GrokTaskConfig: native_spinner=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.show_file_upload and not conv_ui:
            raise ValueError(
                "GrokTaskConfig: show_file_upload=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.file_download and not conv_ui:
            raise ValueError(
                "GrokTaskConfig: file_download=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        for ad in self.extra_allowed_dirs or []:
            if ad.mode not in ("ro", "rw"):
                raise ValueError(
                    f"GrokTaskConfig.extra_allowed_dirs: mode={ad.mode!r} "
                    f"must be one of 'ro', 'rw' (path={ad.path!r})."
                )
        for field_name in ("grok_install_dir", "ttyd_install_dir"):
            val = getattr(self, field_name)
            if val is not None and not val.startswith("/") and not val.startswith("~"):
                raise ValueError(
                    f"GrokTaskConfig.{field_name}={val!r} must be an "
                    f"absolute path (start with '/' or '~')."
                )
