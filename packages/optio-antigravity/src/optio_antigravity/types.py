"""Public data types for optio-antigravity consumers.

The generic ``DeliverableCallback`` / ``HookCallback`` types are owned by
``optio-agents`` and ``SSHConfig`` by ``optio-host``. This module
re-exports them alongside the package-specific ``AntigravityTaskConfig``.

Mirrors ``optio_grok.types`` — the config surface is kept parallel so the
generic session/snapshot/seed machinery reads identically. Only the
backend-specific pieces diverge (``agy``'s binary permission surface, its
state tree under ``~/.gemini``, its transcript-driven conversation mode).
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
    "AntigravityTaskConfig",
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

    optio wraps ``agy`` in claustrum (Landlock-only kernel enforcement here,
    so no ``deny`` list and no bubblewrap dependency); Landlock read/write
    grants cover execution, so — unlike optio-claudecode — there is no separate
    execute bit. A leading ``~/`` in ``path`` is expanded against the REAL host
    home at launch time (the ``agy`` process runs under an isolated ``$HOME``,
    so grants cannot rely on its shell expansion).
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


# agy's native permission surface is binary: normal prompting ("default") or
# ``--dangerously-skip-permissions`` (auto-approve every tool). optio's generic
# callers pass the claudecode-style ``bypassPermissions``; we accept it as an
# alias and map it to the skip flag at launch (see host_actions, Task 0.3+).
PermissionMode = Literal["default", "dangerously-skip-permissions"]
_PERMISSION_MODE_ALIASES = {"bypassPermissions": "dangerously-skip-permissions"}
_VALID_PERMISSION_MODES = {
    "default",
    "dangerously-skip-permissions",
} | set(_PERMISSION_MODE_ALIASES)

# "iframe" = ttyd TUI in the browser (Stages 0-5). "conversation" = a
# synthetic, transcript-driven session (each turn driven by
# ``agy -p --conversation <id>`` under a PTY, events read from
# ``~/.gemini/antigravity/transcript.jsonl``); the task publishes a live
# AntigravityConversation via ctx.publish_result (Stage 6).
ConversationMode = Literal["iframe", "conversation"]

# Verbosity of tool-call rendering in the conversation widget (conversation_ui
# only). Mirrors optio-claudecode; consumed by the dashboard reducer.
ToolVerbosity = Literal["silent", "description-only", "verbose"]
_VALID_TOOL_VERBOSITY = {"silent", "description-only", "verbose"}

# Visibility of reasoning/thinking traces in the conversation widget.
# Task-level, mirrors ToolVerbosity; the UI never decides.
ThinkingVerbosity = Literal["hidden", "visible"]
_VALID_THINKING_VERBOSITY = {"hidden", "visible"}


@dataclass
class AntigravityTaskConfig:
    """Configuration for one optio-antigravity task instance (Stage 0).

    Stage 0 covers iframe/ttyd mode on the local host. Resume, seeds,
    conversation mode, and filesystem isolation arrive in later stages.
    """

    consumer_instructions: str

    env: dict[str, str] | None = None
    # Glob patterns (fnmatch) of env var NAMES to strip from the agy
    # subprocess, so inherited provider creds don't override the task's
    # own configuration. e.g. ["*_API_KEY", "*_TOKEN"].
    scrub_env: list[str] | None = None

    permission_mode: PermissionMode | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    # Passed through as ``--model``. Not validated — vendor strings change
    # (Gemini ids plus BYO Claude/GPT model ids exposed by ``agy models``).
    model: str | None = None
    effort: str | None = None
    reasoning_effort: str | None = None

    ssh: SSHConfig | None = None

    install_if_missing: bool = True
    install_ttyd_if_missing: bool = True
    # Override for where the ``agy`` binary is resolved on the host.
    agy_install_dir: str | None = None
    ttyd_install_dir: str | None = None

    # When True, a fresh launch kicks off the first turn itself — iframe mode
    # types a trailing positional prompt, conversation mode sends the
    # AUTO_START_PROMPT ("Read AGENTS.md and execute the task it describes").
    # This is for UNATTENDED task execution; a task must opt in. Defaults to
    # False (parity with claudecode): a conversation/chat task must NOT
    # auto-fire a kickoff, or agy starts an agentic loop on launch and blocks
    # the operator's first real prompt (queued behind it).
    auto_start: bool = False

    before_execute: HookCallback | None = None
    after_execute: HookCallback | None = None
    on_deliverable: DeliverableCallback | None = None

    # --- seed surface (start fresh from a stored environment) -----------
    # Consumed (default/fallback): merge this seed's environment (agy's Google
    # OAuth token store + ~/.gemini/antigravity-cli/settings.json) into a fresh
    # workdir before launch, beginning a NEW session already logged-in. A plain
    # string is used as-is; a SeedProvider callable is awaited at launch to
    # resolve one (Stage 4 lease path). Baked at task-creation time; ignored on
    # resume.
    seed_id: "str | SeedProvider | None" = None
    # Capture intent: a (sync or async) callback fired on teardown of a fresh
    # session after a successful capture, with two args: (seed_id, info).
    # ``info`` is a human-readable account summary (None in Stage 3; resolved
    # in a later stage). Its presence is what enables seed capture. Both
    # default None, so existing consumers are unaffected. Both are ignored on
    # resume.
    on_seed_saved: "Callable[[str, str | None], Awaitable[None] | None] | None" = None

    # Resume machinery (Stage 2). ON by default: agy persists its conversation
    # under ~/.gemini/antigravity inside the per-task home, so restoring the
    # workdir tar + passing --continue/--conversation rehydrates the session.
    supports_resume: bool = True
    # fnmatch patterns of workdir paths to omit from the resume snapshot tar.
    # None → the framework defaults (see optio_host.archive). Keep the
    # ~/.gemini state OUT of this list: it carries the transcript + settings
    # that --continue/--conversation need.
    workdir_exclude: list[str] | None = None

    # "iframe" = ttyd TUI (Stages 0-5). "conversation" = synthetic
    # transcript-driven turns; the task publishes a live AntigravityConversation
    # (Stage 6).
    mode: ConversationMode = "iframe"
    # Opt-out for the optio.log keyword channel (STATUS/DELIVERABLE/DONE/…).
    # iframe mode requires True (it is the only completion signal there);
    # conversation mode may set it False (the Conversation drives completion).
    host_protocol: bool = True

    # --- conversation surface (Stage 6) ---------------------------------
    # Conversation mode only: route a per-turn permission request to the
    # published conversation's on_permission_request handler. agy turns run
    # ``--dangerously-skip-permissions`` (turn-level, no interactive gate — see
    # design §7), so this is largely advisory; kept for surface parity.
    permission_gate: bool = False
    # Opt-in dashboard conversation UI: the task starts a per-task listener and
    # publishes a live chat widget (wired in Stage 6). Conversation mode only.
    conversation_ui: bool = False
    # How much tool-call detail the conversation widget renders; only affects
    # conversation_ui rendering.
    tool_verbosity: ToolVerbosity = "description-only"

    # Whether the conversation widget shows agy's reasoning/thinking traces.
    # Default hidden — thinking is noisy; opt in per task.
    thinking_verbosity: ThinkingVerbosity = "hidden"

    # --- conversation frontend parity (Stage 7) -------------------------
    # Model preselected in the widget's model picker. Requires
    # mode="conversation" and conversation_ui=True. Defaults to the launch
    # --model when unset. (config.model still drives the launch --model flag;
    # this only controls the picker's initial value.)
    default_model: str | None = None
    # Show the engine-neutral session controls in the conversation widget.
    # agy switches model by restarting the session with --model <new> +
    # --continue (claudecode precedent — no inline switch). Requires
    # mode="conversation" and conversation_ui=True.
    show_session_controls: bool = False
    # Replace the generic working-spinner with antigravity's on-brand native
    # spinner (agy's braille "dots") in the conversation widget. Requires
    # mode="conversation" and conversation_ui=True.
    native_spinner: bool = False
    # Show the file-upload control. Uploaded bytes are written under
    # <workdir>/uploads and referenced to agy via a System: path line so it
    # reads them with its own tools. Requires mode="conversation" and
    # conversation_ui=True.
    show_file_upload: bool = False
    # Upper bound (bytes) on a single uploaded file; the listener rejects
    # anything larger with HTTP 413. Mirrored to the widget via widgetData.
    max_upload_bytes: int = 10_000_000
    # Offer download links for files agy marks with the optio-file: sentinel
    # (deliverables also land in ~/.gemini/antigravity/artifacts/). The listener
    # serves GET /download for paths confined under <workdir>. Requires
    # mode="conversation" and conversation_ui=True.
    file_download: bool = False
    # Upper bound (bytes) on a single downloaded file; the listener rejects
    # anything larger with HTTP 413. Mirrored to the widget via widgetData.
    max_download_bytes: int = 10_000_000

    # --- filesystem isolation (Stage 8) ---------------------------------
    # Confine the agy process (and every tool/subprocess it spawns) to the
    # task workdir + temp dirs + explicit grants, kernel-enforced via claustrum
    # (Landlock on Linux), optionally combined with agy's native ``--sandbox``.
    # Default-ON, fail-CLOSED: if the kernel can't apply the sandbox the launch
    # refuses to start. Requires Landlock (kernel >= 5.13) on the worker.
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
                f"AntigravityTaskConfig.permission_mode={self.permission_mode!r} "
                f"is not one of {sorted(_VALID_PERMISSION_MODES)}"
            )
        if self.mode not in ("iframe", "conversation"):
            raise ValueError(
                f"AntigravityTaskConfig.mode={self.mode!r} is not one of "
                "['iframe', 'conversation']"
            )
        if self.mode == "iframe" and not self.host_protocol:
            raise ValueError(
                "AntigravityTaskConfig: host_protocol=False requires "
                "mode='conversation' (in iframe mode the optio.log keyword "
                "channel is the only completion signal)."
            )
        if self.permission_gate and self.mode != "conversation":
            raise ValueError(
                "AntigravityTaskConfig: permission_gate=True requires "
                "mode='conversation'."
            )
        if self.conversation_ui and self.mode != "conversation":
            raise ValueError(
                "AntigravityTaskConfig: conversation_ui=True requires "
                "mode='conversation'."
            )
        if self.tool_verbosity not in _VALID_TOOL_VERBOSITY:
            raise ValueError(
                f"AntigravityTaskConfig.tool_verbosity={self.tool_verbosity!r} "
                f"is not one of {sorted(_VALID_TOOL_VERBOSITY)}"
            )
        if self.thinking_verbosity not in _VALID_THINKING_VERBOSITY:
            raise ValueError(
                f"AntigravityTaskConfig.thinking_verbosity="
                f"{self.thinking_verbosity!r} is not one of "
                f"{sorted(_VALID_THINKING_VERBOSITY)}"
            )
        # Frontend-parity features are opt-in flags that only make sense with
        # the conversation UI wired (mirrors optio-claudecode).
        conv_ui = self.mode == "conversation" and self.conversation_ui
        if self.default_model is not None and not conv_ui:
            raise ValueError(
                "AntigravityTaskConfig: default_model requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.show_session_controls and not conv_ui:
            raise ValueError(
                "AntigravityTaskConfig: show_session_controls=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.native_spinner and not conv_ui:
            raise ValueError(
                "AntigravityTaskConfig: native_spinner=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.show_file_upload and not conv_ui:
            raise ValueError(
                "AntigravityTaskConfig: show_file_upload=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.file_download and not conv_ui:
            raise ValueError(
                "AntigravityTaskConfig: file_download=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        for ad in self.extra_allowed_dirs or []:
            if ad.mode not in ("ro", "rw"):
                raise ValueError(
                    f"AntigravityTaskConfig.extra_allowed_dirs: mode={ad.mode!r} "
                    f"must be one of 'ro', 'rw' (path={ad.path!r})."
                )
        for field_name in ("agy_install_dir", "ttyd_install_dir"):
            val = getattr(self, field_name)
            if val is not None and not val.startswith("/") and not val.startswith("~"):
                raise ValueError(
                    f"AntigravityTaskConfig.{field_name}={val!r} must be an "
                    f"absolute path (start with '/' or '~')."
                )
