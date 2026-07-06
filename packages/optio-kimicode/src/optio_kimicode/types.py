"""Public data types for optio-kimicode consumers.

The generic ``DeliverableCallback`` / ``HookCallback`` types are owned by
``optio-agents`` and ``SSHConfig`` by ``optio-host``. This module
re-exports them alongside the package-specific ``KimiCodeTaskConfig``.
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
    "KimiCodeTaskConfig",
    "PermissionMode",
    "ConversationMode",
    "Effort",
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

    kimi is confined via the claustrum Landlock sandbox (Task 5.5), which
    covers execution under its read/write grants, so there is no separate
    execute bit here. A leading ``~/`` in ``path`` is expanded against the
    REAL host home at launch time (the kimi process runs under an isolated
    ``$KIMI_CODE_HOME``/``$HOME``, so grants cannot rely on its shell
    expansion).
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


# kimi's own permission modes (``PermissionModeSchema`` in kimi-code:
# agent-core/config/schema.ts). ``yolo`` = auto-approve every action (blanket
# permissions); ``auto`` = auto permission mode; ``manual`` = prompt. Wired to the
# task's ``config.toml`` ``default_permission_mode`` (host_actions.write_kimi_config),
# which the daemon applies to every session it creates — iframe and conversation
# alike (core-impl createSession: ``options.permission ?? config.defaultPermissionMode``).
PermissionMode = Literal["manual", "auto", "yolo"]
_VALID_PERMISSION_MODES = {"manual", "auto", "yolo"}

# "iframe" = the native ``kimi web`` SPA (``kimi server run``) in the browser
# (Stages 0-5). "conversation" = a headless ``kimi acp`` (ACP over stdio)
# session; the task publishes a live KimiCodeConversation via
# ctx.publish_result (Stage 6).
ConversationMode = Literal["iframe", "conversation"]

# Reasoning effort passed through as ``--effort``. kimi exposes a fixed enum
# (unlike a free-form vendor string); ``model`` stays an unvalidated alias.
Effort = Literal["low", "medium", "high", "xhigh", "max"]
_VALID_EFFORTS = {"low", "medium", "high", "xhigh", "max"}

# Verbosity of tool-call rendering in the conversation widget (conversation_ui
# only). Mirrors optio-claudecode; consumed by the dashboard reducer.
ToolVerbosity = Literal["silent", "description-only", "verbose"]
_VALID_TOOL_VERBOSITY = {"silent", "description-only", "verbose"}

# Visibility of reasoning/thinking traces (kimi's ACP reasoning) in the
# conversation widget. Task-level, mirrors ToolVerbosity; the UI never decides.
ThinkingVerbosity = Literal["hidden", "visible"]
_VALID_THINKING_VERBOSITY = {"hidden", "visible"}


@dataclass
class KimiCodeTaskConfig:
    """Configuration for one optio-kimicode task instance.

    Stage 0 covers iframe (``kimi web``) mode on the local host. Resume,
    seeds, conversation mode, and filesystem isolation arrive in later stages.
    """

    consumer_instructions: str

    env: dict[str, str] | None = None
    # Glob patterns (fnmatch) of env var NAMES to strip from the kimi
    # subprocess, so inherited provider creds don't override the task's
    # own configuration. e.g. ["*_API_KEY", "*_TOKEN"].
    scrub_env: list[str] | None = None

    permission_mode: PermissionMode | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    # ``model`` is a kimi model ALIAS (not a raw id) passed through as
    # ``-m``; it is not enum-validated (the alias catalog changes). ``effort``
    # is passed through as ``--effort`` and IS validated against the fixed
    # low..max enum.
    model: str | None = None
    effort: Effort | None = None

    ssh: SSHConfig | None = None

    install_if_missing: bool = True
    # Override for where the ``kimi`` binary is resolved/cached on the host.
    kimi_install_dir: str | None = None

    # When True, a fresh launch kicks off the first turn itself — iframe mode
    # types a trailing positional prompt, conversation mode sends the
    # AUTO_START_PROMPT ("Read AGENTS.md and execute the task it describes"). This
    # is for UNATTENDED task execution; a task must opt in. Defaults to False
    # (parity with claudecode): a conversation/chat task must NOT auto-fire a
    # kickoff, or kimi starts an agentic loop on launch and blocks the
    # operator's first real prompt (queued behind it as task_already_running).
    auto_start: bool = False

    before_execute: HookCallback | None = None
    after_execute: HookCallback | None = None
    on_deliverable: DeliverableCallback | None = None

    # Optional pair of synchronous bytes->bytes transforms wrapping the kimi
    # session subtree tar at GridFS write/read (the two-blob snapshot's
    # sessionBlobId, mirrors optio-opencode/optio-claudecode). When both are
    # set, the session blob is encrypted AT REST; when both are None (default),
    # plaintext is used (backward-compatible). Setting only one raises a config
    # error: asymmetric usage is always a mistake.
    session_blob_encrypt: "Callable[[bytes], bytes] | None" = None
    session_blob_decrypt: "Callable[[bytes], bytes] | None" = None

    # --- seed surface (start fresh from a stored environment) -----------
    # Consumed (default/fallback): merge this seed's environment (kimi
    # credentials/kimi-code.json) into a fresh workdir before launch, beginning
    # a NEW session already logged-in. A plain string is used as-is; a
    # SeedProvider callable is awaited at launch to resolve one (Stage 4
    # lease path). Baked at task-creation time; ignored on resume.
    seed_id: "str | SeedProvider | None" = None
    # Capture intent: a (sync or async) callback fired on teardown of a fresh
    # session after a successful capture, with two args: (seed_id, info).
    # ``info`` is a human-readable account summary. Its presence is what enables
    # seed capture. Both default None, so existing consumers are unaffected.
    # Both are ignored on resume.
    on_seed_saved: "Callable[[str, str | None], Awaitable[None] | None] | None" = None

    # Resume machinery (Stage 2). ON by default: kimi persists its session
    # under <KIMI_CODE_HOME>/sessions inside the workdir, so restoring the
    # workdir tar + passing --continue rehydrates the conversation.
    supports_resume: bool = True
    # fnmatch patterns of workdir paths to omit from the resume snapshot tar.
    # None → the framework defaults (see optio_host.archive). Keep the kimi
    # session state that --continue needs IN the snapshot.
    workdir_exclude: list[str] | None = None

    # "iframe" = native kimi web SPA (Stages 0-5). "conversation" = headless
    # ACP (kimi acp stdio); the task publishes a live KimiCodeConversation
    # (Stage 6).
    mode: ConversationMode = "iframe"
    # Opt-out for the optio.log keyword channel (STATUS/DELIVERABLE/DONE/…).
    # iframe mode requires True (it is the only completion signal there);
    # conversation mode may set it False (the Conversation drives completion).
    host_protocol: bool = True

    # --- conversation surface (Stage 6) ---------------------------------
    # Conversation mode only: route kimi's ACP session/request_permission to
    # the published conversation's on_permission_request handler (the caller
    # registers one). When False, kimi launches so tools run without prompting.
    permission_gate: bool = False
    # Opt-in dashboard conversation UI: the task starts a per-task listener and
    # publishes a live chat widget (wired in Group 5). Conversation mode only.
    conversation_ui: bool = False
    # How much tool-call detail the conversation widget renders; only affects
    # conversation_ui rendering.
    tool_verbosity: ToolVerbosity = "description-only"

    # Whether the conversation widget shows kimi's reasoning/thinking traces.
    # Default hidden — thinking is noisy; opt in per task.
    thinking_verbosity: ThinkingVerbosity = "hidden"

    # --- conversation frontend parity (Stage 7) -------------------------
    # Model preselected in the widget's model picker. Requires
    # mode="conversation" and conversation_ui=True. Defaults to the live ACP
    # current model when unset. (config.model still drives the launch -m flag;
    # this only controls the picker's initial value.)
    default_model: str | None = None
    # Show the engine-neutral session-controls bar (model + thinking + mode) in
    # the conversation widget. kimi switches inline over ACP (session/set_model
    # for the model, session/set_config_option for thinking/mode — no process
    # restart). Requires mode="conversation" and conversation_ui=True.
    show_session_controls: bool = False
    # Replace the generic working-spinner with kimicode's on-brand native
    # spinner in the conversation widget. Requires mode="conversation" and
    # conversation_ui=True.
    native_spinner: bool = False
    # Show the file-upload control. Uploaded bytes are written under
    # <workdir>/uploads and referenced to kimi via a System: path line so it
    # reads them with its own tools. Requires mode="conversation" and
    # conversation_ui=True.
    show_file_upload: bool = False
    # Upper bound (bytes) on a single uploaded file; the listener rejects
    # anything larger with HTTP 413. Mirrored to the widget via widgetData.
    max_upload_bytes: int = 10_000_000
    # Offer download links for files kimi marks with the optio-file: sentinel.
    # The listener serves GET /download for paths confined under <workdir>.
    # Requires mode="conversation" and conversation_ui=True.
    file_download: bool = False
    # Upper bound (bytes) on a single downloaded file; the listener rejects
    # anything larger with HTTP 413. Mirrored to the widget via widgetData.
    max_download_bytes: int = 10_000_000

    # --- filesystem isolation (Stage 8) ---------------------------------
    # Confine the kimi process (and every tool/subprocess it spawns) to the
    # task workdir + temp dirs + explicit grants, kernel-enforced via the
    # claustrum Landlock sandbox (Task 5.5). Fail-CLOSED: if claustrum cannot
    # be provisioned or the kernel lacks Landlock, the task refuses to launch
    # rather than run unconfined. Requires Landlock (kernel >= 5.13) on the
    # worker. Default-ON.
    fs_isolation: bool = True
    # Additional path grants beyond the workdir + temp dirs. ``~/`` expands
    # against the real host home at launch. Ignored when fs_isolation=False.
    extra_allowed_dirs: list[AllowedDir] | None = None

    def __post_init__(self) -> None:
        e = self.session_blob_encrypt is not None
        d = self.session_blob_decrypt is not None
        if e != d:
            raise ValueError(
                "KimiCodeTaskConfig: session_blob_encrypt and "
                "session_blob_decrypt must be set together (both callables) "
                "or both left as None; one without the other is a config error."
            )
        if (
            self.permission_mode is not None
            and self.permission_mode not in _VALID_PERMISSION_MODES
        ):
            raise ValueError(
                f"KimiCodeTaskConfig.permission_mode={self.permission_mode!r} "
                f"is not one of {sorted(_VALID_PERMISSION_MODES)}"
            )
        if self.effort is not None and self.effort not in _VALID_EFFORTS:
            raise ValueError(
                f"KimiCodeTaskConfig.effort={self.effort!r} is not one of "
                f"{sorted(_VALID_EFFORTS)}"
            )
        if self.mode not in ("iframe", "conversation"):
            raise ValueError(
                f"KimiCodeTaskConfig.mode={self.mode!r} is not one of "
                "['iframe', 'conversation']"
            )
        if self.mode == "iframe" and not self.host_protocol:
            raise ValueError(
                "KimiCodeTaskConfig: host_protocol=False requires "
                "mode='conversation' (in iframe mode the optio.log keyword "
                "channel is the only completion signal)."
            )
        if self.permission_gate and self.mode != "conversation":
            raise ValueError(
                "KimiCodeTaskConfig: permission_gate=True requires "
                "mode='conversation'."
            )
        if self.conversation_ui and self.mode != "conversation":
            raise ValueError(
                "KimiCodeTaskConfig: conversation_ui=True requires "
                "mode='conversation'."
            )
        if self.tool_verbosity not in _VALID_TOOL_VERBOSITY:
            raise ValueError(
                f"KimiCodeTaskConfig.tool_verbosity={self.tool_verbosity!r} "
                f"is not one of {sorted(_VALID_TOOL_VERBOSITY)}"
            )
        if self.thinking_verbosity not in _VALID_THINKING_VERBOSITY:
            raise ValueError(
                f"KimiCodeTaskConfig.thinking_verbosity={self.thinking_verbosity!r} "
                f"is not one of {sorted(_VALID_THINKING_VERBOSITY)}"
            )
        # Frontend-parity features are opt-in flags that only make sense with
        # the conversation UI wired (mirrors optio-claudecode).
        conv_ui = self.mode == "conversation" and self.conversation_ui
        if self.default_model is not None and not conv_ui:
            raise ValueError(
                "KimiCodeTaskConfig: default_model requires mode='conversation' "
                "and conversation_ui=True."
            )
        if self.show_session_controls and not conv_ui:
            raise ValueError(
                "KimiCodeTaskConfig: show_session_controls=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.native_spinner and not conv_ui:
            raise ValueError(
                "KimiCodeTaskConfig: native_spinner=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.show_file_upload and not conv_ui:
            raise ValueError(
                "KimiCodeTaskConfig: show_file_upload=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.file_download and not conv_ui:
            raise ValueError(
                "KimiCodeTaskConfig: file_download=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        for ad in self.extra_allowed_dirs or []:
            if ad.mode not in ("ro", "rw"):
                raise ValueError(
                    f"KimiCodeTaskConfig.extra_allowed_dirs: mode={ad.mode!r} "
                    f"must be one of 'ro', 'rw' (path={ad.path!r})."
                )
        for field_name in ("kimi_install_dir",):
            val = getattr(self, field_name)
            if val is not None and not val.startswith("/") and not val.startswith("~"):
                raise ValueError(
                    f"KimiCodeTaskConfig.{field_name}={val!r} must be an "
                    f"absolute path (start with '/' or '~')."
                )
