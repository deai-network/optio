"""Settings SSOT for codex's NATIVE sandbox (Stage 8 filesystem isolation).

optio-codex confines the agent's TOOL SUBPROCESSES using codex's own
kernel-level sandbox (bundled bubblewrap primary, Landlock+seccomp fallback
on Linux; helper bins materialize to ``$CODEX_HOME/tmp/arg0/``) rather than
porting optio-claudecode's claustrum. Unlike grok there is no planted
profile file: one resolved :class:`SandboxSettings` renders to

* CLI surfaces (interactive TUI + ``codex exec``): ``--sandbox <mode>`` plus
  ``-c sandbox_workspace_write.writable_roots=[…]`` /
  ``-c sandbox_workspace_write.network_access=true`` overrides
  (:func:`build_sandbox_cli_args`); and
* the ``codex app-server`` launch (conversation mode): the mode travels
  out-of-band via ``thread/start``'s ``sandbox`` field (a kebab-case
  ``SandboxMode`` enum — the app-server has NO ``--sandbox`` flag), while
  writable_roots/network_access ride the SAME ``-c sandbox_workspace_write.*``
  overrides on the ``codex app-server`` command line
  (:func:`build_sandbox_config_overrides`).

  Schema note (probed, codex-cli 0.142.5 ``codex app-server
  generate-json-schema``): ``ThreadStartParams`` exposes only ``sandbox``
  (SandboxMode enum) + a generic ``config`` object — there is NO
  ``sandboxPolicy`` object on ``thread/start``. A structured ``SandboxPolicy``
  (a ``type``-tagged union: ``workspaceWrite``/``readOnly``/
  ``dangerFullAccess``, camelCase ``writableRoots``/``networkAccess``) exists
  only on ``turn/start``, which optio does not use to carry the sandbox — the
  mode+``-c`` pair at launch defines the whole app-server process's posture.

Probed divergences vs grok/claudecode (codex-cli 0.142.5, 2026-07-02):

* ``workspace-write`` restricts WRITES only — the READ side is open, so
  ``AllowedDir(mode="ro")`` grants are a documented no-op here (additive
  grant, trivially satisfied). Only ``rw`` grants change behavior.
* Network is OFF by default in workspace-write (``[sandbox_workspace_write]
  network_access``) — stricter than the other wrappers' fs-only sandboxes;
  ``CodexTaskConfig.network_access=True`` relaxes it.
* ``.git/`` and ``.codex/`` under a writable root stay read-only for
  sandboxed commands — the agent's shell cannot rewrite the per-task
  ``auth.json`` even though ``CODEX_HOME`` lives inside the workdir.
* Failure mode with NO mechanism available: **FAIL-CLOSED** (Task-0 probe
  verdict, codex-cli 0.142.5). codex never runs the model's shell command
  unconfined as a result of a sandbox-setup failure — it errors/panics
  (bwrap "Creating new namespace failed" rc=1, or bare-binary "bubblewrap is
  unavailable" panic rc=101) and the command does not run. The only
  unconfined path is the explicit ``--dangerously-bypass-approvals-and-
  sandbox`` opt-out, which optio-codex never emits. Consequence: no
  launch-time enforcement guard is required (Task 5B, evidence-only).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from optio_codex.types import CodexTaskConfig, SandboxMode


def _expand_home(path: str, host_home: str) -> str:
    """Expand a leading ``~/`` against the REAL host home.

    The codex process runs under an isolated ``$HOME`` (``<workdir>/home``),
    so a ``~/`` grant cannot rely on shell expansion — it is resolved against
    the operator's real home here, at settings-resolution time.
    """
    home = host_home.rstrip("/")
    if path == "~":
        return home
    if path.startswith("~/"):
        return f"{home}/{path[2:]}"
    return path


@dataclass(frozen=True)
class SandboxSettings:
    """One task's resolved sandbox posture — the SSOT every launch surface
    (iframe/exec ``--sandbox`` argv, and the app-server's thread/start
    ``sandbox`` mode + ``-c`` config overrides) renders from."""

    mode: "SandboxMode"
    writable_roots: tuple[str, ...] = ()
    network_access: bool = False


def resolve_sandbox_settings(
    config: "CodexTaskConfig", *, host_home: str,
) -> SandboxSettings:
    """Resolve ``fs_isolation``/``sandbox``/``extra_allowed_dirs``/
    ``network_access`` into one :class:`SandboxSettings`.

    ``ro``/``rox`` grants are skipped (codex never restricts reads — see module
    docstring); ``rw``/``rwx`` grants become ``writable_roots`` with ``~/``
    expanded against ``host_home`` (codex's native sandbox has no execute bit,
    so ``rwx``==``rw`` and ``rox``==``ro``). Roots/network only apply to
    workspace-write (validated in CodexTaskConfig.__post_init__).
    """
    mode = config.effective_sandbox_mode
    roots: list[str] = []
    if mode == "workspace-write":
        for ad in config.extra_allowed_dirs or []:
            if ad.mode in ("rw", "rwx"):
                roots.append(_expand_home(ad.path, host_home).rstrip("/"))
    return SandboxSettings(
        mode=mode,
        writable_roots=tuple(roots),
        network_access=bool(config.network_access) and mode == "workspace-write",
    )


def _toml_str_array(paths: tuple[str, ...]) -> str:
    # json.dumps output is valid TOML for basic strings.
    return "[" + ", ".join(json.dumps(p) for p in paths) + "]"


def build_sandbox_config_overrides(settings: SandboxSettings) -> list[str]:
    """Render the ``-c sandbox_workspace_write.*`` overrides ONLY (no
    ``--sandbox`` flag).

    Used by the ``codex app-server`` launch, which selects the mode
    out-of-band via ``thread/start``'s ``sandbox`` field and has no
    ``--sandbox`` flag; the writable_roots/network_access still need to reach
    the process, and ``codex app-server`` accepts ``-c`` config overrides.
    :func:`build_sandbox_cli_args` composes on top of this — one SSOT, two
    launch surfaces. ``-c`` values are parsed as TOML, so the roots array is
    emitted in TOML syntax. Empty outside workspace-write (and for a
    workspace-write posture with no extras).
    """
    if settings.mode != "workspace-write":
        return []
    out: list[str] = []
    if settings.writable_roots:
        out += [
            "-c",
            "sandbox_workspace_write.writable_roots="
            + _toml_str_array(settings.writable_roots),
        ]
    if settings.network_access:
        out += ["-c", "sandbox_workspace_write.network_access=true"]
    return out


def build_sandbox_cli_args(settings: SandboxSettings) -> list[str]:
    """Render settings as codex CLI args (interactive TUI and ``exec``).

    ``--sandbox`` is accepted by both surfaces; the ``-c`` overrides are the
    same ones :func:`build_sandbox_config_overrides` produces for the
    app-server (one SSOT). No overrides are emitted outside workspace-write.
    """
    return ["--sandbox", settings.mode, *build_sandbox_config_overrides(settings)]
