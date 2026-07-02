# optio-codex Plan E — Stage 8 (native-sandbox filesystem isolation) + release readiness

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Confine every codex tool subprocess to the task workdir + explicit grants using codex's **native sandbox** (bundled bubblewrap primary, Landlock+seccomp fallback — no claustrum, per the design doc's "Filesystem isolation (Stage 8): codex-native" section), with an empirically-recorded fail-open/fail-closed verdict driving a loud launch-time guard if needed; then close out the wrapper for release: real-binary enforcement test, final Appendix-A parity audit, README truth-up, design-doc as-shipped reconciliation, version/registration sanity.

**Architecture:** One semantic SSOT — `fs_allowlist.resolve_sandbox_settings(config, host_home)` produces a frozen `SandboxSettings(mode, writable_roots, network_access)` — with exactly two renderers: `build_sandbox_cli_args(settings)` (iframe TUI argv + `codex exec` probe flags, both `--sandbox <mode>` + `-c sandbox_workspace_write.*` overrides) and `build_sandbox_policy(settings)` (app-server `thread/start.sandboxPolicy`). `host_actions.build_codex_flags` stays the single CLI-argv composition seam and consumes the rendered args. Config reconciliation: the existing `sandbox: SandboxMode` field becomes `SandboxMode | None` derived from `fs_isolation` (no duplicate knobs), plus `extra_allowed_dirs: list[AllowedDir]` and `network_access: bool`. Unlike grok there is **no planted profile file** — codex has no custom-profile fail-closed trick, which is exactly why Task 0's failure-mode verdict is mandatory before the guard decision.

**Tech Stack:** Python ≥3.11, pytest + pytest-asyncio (asyncio_mode=auto), codex-cli 0.142.5 (all codex facts below live-probed against that version — re-verify if the pinned version moved), tmux + ttyd, MongoDB via the existing test fixtures.

## Global Constraints

- Worktree: `/home/csillag/deai/optio/.worktrees/csillag/optio-codex` — branch `csillag/optio-codex`. All paths relative to the worktree root unless absolute.
- Python env: the worktree venv **only**: `.venv/bin/python` / `.venv/bin/pip`. NEVER `pip install` against the global interpreter. If `import optio_codex` fails at baseline: `.venv/bin/pip install -e packages/optio-codex`.
- Test command shape: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q` (MongoDB on `localhost:27017`; if down: `cd packages/optio-demo && make deps-up`).
- Commit style: conventional commits (`feat(optio-codex): …`), one commit per task step marked "Commit". **NO `Co-Authored-By` lines** (user rule).
- **Execution precondition:** Plan E runs AFTER Plans B (Stages 1–2), C (Stages 3–5) and D (Stages 6–7) have landed on this branch. Those plans were being written in parallel with this one and did **not** exist in `docs/` when this plan was finalized. Before Task 1, re-check for collisions: `grep -rn "network_access\|fs_isolation\|extra_allowed_dirs\|AllowedDir\|FAKE_CODEX_RECORD\|fs_allowlist" docs/2026-07-02-optio-codex-plan-{b,c,d}-*.md packages/optio-codex/src packages/optio-codex/tests`. If any of these names already landed with different semantics, adapt THIS plan's code to the landed names (extend, never duplicate) and say so in the commit body.
- Tasks referencing B/C/D artifacts (the conversation module, `run_codex_probe`, the fake app-server responder, the vendored app-server schema, `FAKE_CODEX_RECORD`) carry an explicit *adaptation note*: keep the stated invariant, rename to the as-landed symbols.
- Every task leaves the whole codex suite green before its commit. Real-binary tests are env-gated (`OPTIO_CODEX_SANDBOX_ENFORCE_TEST=1`) and NEVER run in the default suite.
- Reference implementations: `packages/optio-grok` (Stage-8 template: `fs_allowlist.py`, `test_fs_allowlist.py`, `test_session_sandbox.py`, `test_sandbox_enforce.py`, grok plan `docs/2026-07-02-optio-grok-stage8-plan.md` on the main checkout), `packages/optio-claudecode` (claustrum posture — NOT the mechanism here).
- Pinned codex sandbox facts (live-probed, 0.142.5): modes `read-only | workspace-write | danger-full-access`; Linux mechanism = bundled bubblewrap primary, Landlock+seccomp fallback, helper bins materialized to `$CODEX_HOME/tmp/arg0/`; workspace-write: network OFF by default, `.git/` and `.codex/` kept RO inside writable roots, `/tmp` writable, read side OPEN (no read-only grant vocabulary); extra writable roots via `-c sandbox_workspace_write.writable_roots=[...]` (or `--add-dir`); config keys `sandbox_mode`, `[sandbox_workspace_write] network_access`; test surface `codex sandbox -- <cmd>`; `codex doctor` reports "filesystem sandbox restricted"; probed: `codex sandbox -- touch ~/x` → "Read-only file system". A newer `[permissions.<name>]` profile system exists (`codex sandbox -P`) — out of scope, noted in Task 0.

**Decisions fixed by this plan (do not relitigate mid-execution):**

- **`AllowedDir("…", "ro")` is ACCEPTED and is a documented no-op** on codex. Rationale ("question the constraint" applied): the `AllowedDir` grant contract everywhere in optio is *additive* — a grant widens access, never narrows it. codex workspace-write leaves the read side globally open, so every `ro` grant is trivially satisfied (superset), not violated. Rejecting `ro` would gratuitously break config portability across wrappers (the same `extra_allowed_dirs` list must work for claudecode/grok/codex). The real divergence — codex does NOT deny reads outside the allowlist, unlike grok/claudecode — is documented in the `AllowedDir` docstring, the `fs_allowlist` module docstring, and the README (Task 8). Only `rw` grants change behavior (→ `writable_roots`).
- **`sandbox` field reconciliation:** `sandbox: SandboxMode | None = None`; effective mode = explicit value if set, else `workspace-write` when `fs_isolation=True`, `danger-full-access` when `False`. Validation (Task 1): `fs_isolation=True` + `sandbox="danger-full-access"` → `ValueError`; `fs_isolation=False` + explicit restrictive `sandbox` → `ValueError` (contradiction); `rw` grants or `network_access=True` under effective `read-only` → `ValueError` (a grant that cannot be honored is a config error; a grant that is over-satisfied — e.g. anything under `danger-full-access` — is not).
- **`network_access: bool = False`** mirrors codex's own workspace-write default (network OFF). This is *stricter* than grok/claudecode, whose fs sandboxes never touch the network — documented divergence, not silently loosened.
- **The per-task `CODEX_HOME` (`<workdir>/home/.codex`) sits inside the writable workdir but codex keeps `.codex/` RO for sandboxed tool commands** — the agent's own shell cannot rewrite its `auth.json`. codex's *own* (unsandboxed) process writes rollouts/auth there normally. Free hardening; note it in docs, don't fight it.

---

### Task 0: Investigation — sandbox failure-mode verdict (fail-open vs fail-closed) + `codex sandbox` invocation pin

Grok lesson: its built-in profiles fail OPEN (warn + run unconfined), which forced the custom-profile design. codex has no custom-profile analogue, so we must determine empirically what codex does when **neither bubblewrap nor Landlock is available**, and record the verdict. The verdict selects Task 5A (launch-time guard) vs 5B (evidence-only). This task also pins the exact `codex sandbox` invocation forms Tasks 5/6 depend on.

**Files:**
- Modify: `docs/2026-07-02-optio-codex-design.md` (append a probe-verdict subsection to the Stage-8 section)
- No source changes.

**Interfaces:**
- Consumes: a real codex binary (host-installed or the Stage-5 cache `${OPTIO_CODEX_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/optio-codex/bin}/codex`).
- Produces: a written verdict (`FAIL-OPEN` or `FAIL-CLOSED`) with command-level evidence, and the pinned `codex sandbox` CLI forms.

- [ ] **Step 1: Resolve the binary and pin the `codex sandbox` CLI surface**

```bash
CODEX="$(command -v codex || echo "${OPTIO_CODEX_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/optio-codex/bin}/codex")"
"$CODEX" --version
"$CODEX" sandbox --help
```

Record: (a) how a sandbox **mode** is selected for `codex sandbox` (`-s/--sandbox <mode>`? `--full-auto`? only `-c sandbox_mode=…`? a `-P <permission-profile>` from the newer `[permissions]` system?); (b) whether `-c` overrides are accepted; (c) the `--` command separator form. These pinned forms are used verbatim in Tasks 5 and 6. If mode selection is only possible via `-c sandbox_mode=workspace-write`, that IS the pinned form — do not guess flag spellings.

- [ ] **Step 2: Baseline enforcement probes (mechanism available — this host)**

```bash
PROBE_DIR="$HOME/.optio-codex-probe-dir-$$"; mkdir -p "$PROBE_DIR"; cd "$PROBE_DIR"
export CODEX_HOME="$PROBE_DIR/.codex"; mkdir -p "$CODEX_HOME"
# (throwaway CODEX_HOME so helper-bin materialization to $CODEX_HOME/tmp/arg0/ can't interfere)

# Outside write (real home is not a writable root) — expect DENIAL:
"$CODEX" sandbox <pinned-ws-write-form> -- touch "$HOME/.optio-probe-outside-$$"; echo "rc=$?"
ls -la "$HOME/.optio-probe-outside-$$" 2>&1   # expect: No such file

# Inside write (cwd is the workspace; NOT under /tmp, so this isn't the tmp carve-out) — expect SUCCESS:
"$CODEX" sandbox <pinned-ws-write-form> -- touch ./inside.txt; echo "rc=$?"; ls inside.txt

# Doctor line, for the record:
"$CODEX" doctor 2>&1 | grep -i sandbox
```

Record exit codes and the denial text (probed earlier: "Read-only file system").

- [ ] **Step 3: THE core probe — behavior with NO sandbox mechanism available**

Simulate a worker where bubblewrap cannot run (namespaces restricted) AND Landlock is unavailable (syscalls filtered), using systemd-run's per-unit seccomp/namespace controls:

```bash
systemd-run --user --pipe --wait \
  -p RestrictNamespaces=yes \
  -p SystemCallFilter='~landlock_create_ruleset landlock_add_rule landlock_restrict_self' \
  -p SystemCallErrorNumber=ENOSYS \
  --setenv=CODEX_HOME="$CODEX_HOME" --working-directory="$PROBE_DIR" \
  -- "$CODEX" sandbox <pinned-ws-write-form> -- touch "$HOME/.optio-probe-nomech-$$"
echo "rc=$?"; ls -la "$HOME/.optio-probe-nomech-$$" 2>&1
```

Interpretation — this is the recorded verdict:
- codex exits **nonzero** with an explicit no-mechanism error AND the probe file is **absent** → **FAIL-CLOSED**.
- codex exits **0** (or warns) AND the probe file **exists** → **FAIL-OPEN**.

Also run `codex doctor` under the same restriction and record its "filesystem sandbox" line. Fallback recipe if systemd-run is unavailable on the probe host: `docker run` with a custom seccomp profile that removes `landlock_*` syscalls and `--security-opt=no-new-privileges` + default userns restrictions (bwrap fails there — the claustrum findings already established userns/bwrap fail on this class of host); the container needs the codex musl binary bind-mounted.

- [ ] **Step 4: Corroborating evidence (source strings + helper-materialization failure branch)**

```bash
strings -n 12 "$CODEX" | grep -iE 'sandbox.*(unavailable|not available|falling back|disabled|mandat|refus)' | sort -u | head -30
# Separate failure vector: helper bins can't materialize (read-only CODEX_HOME):
RO_HOME="$PROBE_DIR/ro-codex-home"; mkdir -p "$RO_HOME"; chmod 555 "$RO_HOME"
CODEX_HOME="$RO_HOME" "$CODEX" sandbox <pinned-ws-write-form> -- touch "$HOME/.optio-probe-rohome-$$"; echo "rc=$?"
ls -la "$HOME/.optio-probe-rohome-$$" 2>&1; chmod 755 "$RO_HOME"
```

If Step 3 and Step 4's helper-failure branch disagree (e.g. no-mechanism fails closed but helper-materialization failure runs unconfined), the verdict is **FAIL-OPEN** — the guard must cover the weakest branch.

- [ ] **Step 5: Clean up probe artifacts** (`rm -rf "$PROBE_DIR"`; `rm -f $HOME/.optio-probe-*` — verify none of the outside probes exist).

- [ ] **Step 6: Record the verdict.** Append to the design doc's "Filesystem isolation (Stage 8): codex-native" section:

```markdown
### Stage-8 probe verdict (2026-07-02, codex-cli <version>)

**Verdict: FAIL-<OPEN|CLOSED>** when no sandbox mechanism (bubblewrap or
Landlock) is available. Evidence:
- `codex sandbox <pinned form> -- touch $HOME/probe` (mechanism available):
  rc=<n>, "<denial text>", file absent.
- Same under `systemd-run RestrictNamespaces=yes SystemCallFilter=~landlock_*`:
  rc=<n>, "<error/warning text>", file <absent|CREATED>.
- Read-only `CODEX_HOME` (helper materialization blocked): rc=<n>, file <absent|CREATED>.
- `codex doctor` sandbox line, both environments: "<text>" / "<text>".
- Binary strings: "<the load-bearing string(s) found>".

**Pinned `codex sandbox` invocation** (used by the enforcement guard/test):
`codex sandbox <exact flags> -- <cmd>`; `-c` overrides <accepted|not accepted>.
Consequence: Task 5A (launch-time enforcement guard) <IS|IS NOT> required.
```

Fill every `<…>` with the actual probed values — no placeholders survive into the committed doc.

- [ ] **Step 7: Commit** `docs(optio-codex): stage-8 sandbox failure-mode verdict + codex-sandbox CLI pin (probed)`.

---

### Task 1: Config reconciliation — `AllowedDir`, `fs_isolation`, `extra_allowed_dirs`, `network_access`, `sandbox: SandboxMode | None`

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/types.py`, `packages/optio-codex/src/optio_codex/__init__.py`
- Test: `packages/optio-codex/tests/test_config.py`

**Interfaces:**
- Produces: `AllowedDir(path: str, mode: Literal["ro","rw"])`; `CodexTaskConfig.fs_isolation: bool = True`, `.extra_allowed_dirs: list[AllowedDir] | None = None`, `.network_access: bool = False`, `.sandbox: SandboxMode | None = None` (was `= "workspace-write"`), `.effective_sandbox_mode` property; the validation matrix above. Exports `AllowedDir` (and the vocabulary Literals, if Plan A/D didn't already) from `optio_codex`.
- Adaptation note: Plans B–D add their own fields to `CodexTaskConfig` — merge into the same `__post_init__`, keep one validation block per concern, grok's `types.py:218-288` is the shape reference.

- [ ] **Step 1: Write the failing tests** — append to `packages/optio-codex/tests/test_config.py`:

```python
import pytest

from optio_codex.types import AllowedDir, CodexTaskConfig


def test_allowed_dir_rejects_bad_mode():
    with pytest.raises(ValueError):
        AllowedDir("/x", "wx")  # type: ignore[arg-type]


def test_sandbox_defaults_derive_from_fs_isolation():
    on = CodexTaskConfig(consumer_instructions="x")
    assert on.fs_isolation is True
    assert on.sandbox is None
    assert on.effective_sandbox_mode == "workspace-write"
    off = CodexTaskConfig(consumer_instructions="x", fs_isolation=False)
    assert off.effective_sandbox_mode == "danger-full-access"


def test_fs_isolation_forbids_danger_full_access():
    with pytest.raises(ValueError, match="danger-full-access"):
        CodexTaskConfig(
            consumer_instructions="x", sandbox="danger-full-access",
        )


def test_fs_isolation_off_forbids_restrictive_sandbox():
    with pytest.raises(ValueError, match="fs_isolation=False"):
        CodexTaskConfig(
            consumer_instructions="x",
            fs_isolation=False,
            sandbox="workspace-write",
        )


def test_explicit_read_only_with_fs_isolation_is_valid():
    c = CodexTaskConfig(consumer_instructions="x", sandbox="read-only")
    assert c.effective_sandbox_mode == "read-only"


def test_rw_grant_under_read_only_rejected():
    with pytest.raises(ValueError, match="read-only"):
        CodexTaskConfig(
            consumer_instructions="x",
            sandbox="read-only",
            extra_allowed_dirs=[AllowedDir("/scratch", "rw")],
        )


def test_ro_grant_always_accepted_and_noop():
    # codex workspace-write leaves the READ side open, so "ro" grants are
    # trivially satisfied (documented no-op) — accepted in every mode.
    c = CodexTaskConfig(
        consumer_instructions="x",
        sandbox="read-only",
        extra_allowed_dirs=[AllowedDir("~/data", "ro")],
    )
    assert c.extra_allowed_dirs[0].mode == "ro"


def test_network_access_requires_workspace_write():
    with pytest.raises(ValueError, match="network_access"):
        CodexTaskConfig(
            consumer_instructions="x", sandbox="read-only", network_access=True,
        )
    ok = CodexTaskConfig(consumer_instructions="x", network_access=True)
    assert ok.network_access is True


def test_allowed_dir_exported():
    import optio_codex

    assert optio_codex.AllowedDir is AllowedDir
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest packages/optio-codex/tests/test_config.py -q` → the new tests FAIL (ImportError on `AllowedDir`, then assertion failures).

- [ ] **Step 3: Implement.** In `types.py`, add `AllowedDir` above the config class:

```python
@dataclass
class AllowedDir:
    """A caller-supplied extra path grant for filesystem isolation (Stage 8).

    ``mode`` is ``"ro"`` or ``"rw"``. Grants are ADDITIVE: they may widen the
    sandbox allowlist, never mask the baseline (the workdir/cwd and ``/tmp``
    are always writable in workspace-write).

    codex divergence (vs grok/claudecode, whose sandboxes also deny reads):
    codex ``workspace-write`` restricts WRITES only — the read side is open,
    so ``mode="ro"`` is trivially satisfied and changes nothing (documented
    no-op, kept for cross-wrapper config portability). Only ``mode="rw"``
    grants alter behavior, via ``sandbox_workspace_write.writable_roots``.
    A leading ``~/`` expands against the REAL host home at launch time (the
    codex process runs under an isolated ``$HOME``).
    """

    path: str
    mode: Literal["ro", "rw"]

    def __post_init__(self) -> None:
        if self.mode not in ("ro", "rw"):
            raise ValueError(
                f"AllowedDir.mode={self.mode!r} must be one of 'ro', 'rw' "
                f"(path={self.path!r})."
            )
```

Change the `sandbox` field and add the Stage-8 block (keep the existing field comment style):

```python
    # codex-native sandbox mode. None (default) derives from fs_isolation:
    # workspace-write when isolation is on, danger-full-access when off.
    # Explicit values are cross-validated against fs_isolation below.
    sandbox: SandboxMode | None = None
    # Grant network to sandboxed tool commands (codex workspace-write default
    # is network OFF — [sandbox_workspace_write] network_access). False
    # mirrors codex; note this is STRICTER than grok/claudecode, whose fs
    # sandboxes do not restrict the network at all.
    network_access: bool = False

    # --- filesystem isolation (Stage 8) ---------------------------------
    # Confine codex tool subprocesses to the task workdir + /tmp + explicit
    # rw grants, kernel-enforced via codex's NATIVE sandbox (bundled
    # bubblewrap primary, Landlock+seccomp fallback on Linux). Default-ON.
    fs_isolation: bool = True
    # Additional path grants beyond the workdir + temp dirs. ``~/`` expands
    # against the real host home at launch. "ro" grants are a documented
    # no-op on codex (reads are unrestricted in workspace-write).
    extra_allowed_dirs: list[AllowedDir] | None = None

    @property
    def effective_sandbox_mode(self) -> SandboxMode:
        if self.sandbox is not None:
            return self.sandbox
        return "workspace-write" if self.fs_isolation else "danger-full-access"
```

Replace the existing `sandbox` validation in `__post_init__` and append the matrix:

```python
        if self.sandbox is not None and self.sandbox not in _VALID_SANDBOX_MODES:
            raise ValueError(
                f"CodexTaskConfig.sandbox={self.sandbox!r} "
                f"is not one of {sorted(_VALID_SANDBOX_MODES)}"
            )
        if self.fs_isolation and self.effective_sandbox_mode == "danger-full-access":
            raise ValueError(
                "CodexTaskConfig: fs_isolation=True is incompatible with "
                "sandbox='danger-full-access' — fs_isolation exists to "
                "guarantee a kernel-enforced sandbox. Set fs_isolation=False "
                "to run unconfined."
            )
        if not self.fs_isolation and self.sandbox in ("read-only", "workspace-write"):
            raise ValueError(
                "CodexTaskConfig: fs_isolation=False launches codex "
                "unconfined (danger-full-access); an explicit restrictive "
                f"sandbox={self.sandbox!r} contradicts it. Drop one of the "
                "two settings."
            )
        rw_extras = [d for d in (self.extra_allowed_dirs or []) if d.mode == "rw"]
        if rw_extras and self.effective_sandbox_mode == "read-only":
            raise ValueError(
                "CodexTaskConfig: extra_allowed_dirs rw grants "
                f"({[d.path for d in rw_extras]}) cannot be honored under "
                "sandbox='read-only' — writable_roots is a workspace-write "
                "feature. ('ro' grants are fine: codex never restricts reads.)"
            )
        if self.network_access and self.effective_sandbox_mode == "read-only":
            raise ValueError(
                "CodexTaskConfig: network_access=True is a "
                "[sandbox_workspace_write] knob and cannot apply under "
                "sandbox='read-only'."
            )
```

Add `"AllowedDir"` to `types.__all__`; import/re-export `AllowedDir` (and `IframeMode`/`ApprovalPolicy`/`SandboxMode` if still missing) in `optio_codex/__init__.py` `__all__`.

- [ ] **Step 4: Run** the full suite → GREEN. Any pre-existing test that passed `sandbox="workspace-write"` explicitly still validates; tests that asserted the old default field value `== "workspace-write"` must assert `effective_sandbox_mode` instead.
- [ ] **Step 5: Commit** `feat(optio-codex): fs_isolation/extra_allowed_dirs/network_access config + sandbox-mode reconciliation (Stage 8)`.

---

### Task 2: `fs_allowlist.py` — SandboxSettings SSOT + CLI-args renderer

**Files:**
- Create: `packages/optio-codex/src/optio_codex/fs_allowlist.py`
- Test: `packages/optio-codex/tests/test_fs_allowlist.py`

**Interfaces:**
- Produces: `_expand_home(path, host_home)` (ported from grok `fs_allowlist.py:33-45`); `SandboxSettings` frozen dataclass; `resolve_sandbox_settings(config, *, host_home) -> SandboxSettings`; `build_sandbox_cli_args(settings) -> list[str]`. `build_sandbox_policy` arrives in Task 4 (needs the schema pin).
- The module docstring is the live-probe-pinned protocol record (grok convention) — it MUST state the Task-0 verdict.

- [ ] **Step 1: Write the failing tests** — create `packages/optio-codex/tests/test_fs_allowlist.py`:

```python
"""Unit tests for the codex native-sandbox settings SSOT (Stage 8).

codex divergence from grok: no planted profile file — settings render to
``--sandbox <mode>`` + ``-c sandbox_workspace_write.*`` CLI overrides (and,
in Task 4, an app-server ``sandboxPolicy``). ``ro`` grants are a documented
no-op (codex workspace-write leaves reads open).
"""

from __future__ import annotations

from optio_codex.fs_allowlist import (
    SandboxSettings,
    build_sandbox_cli_args,
    resolve_sandbox_settings,
)
from optio_codex.types import AllowedDir, CodexTaskConfig


def _cfg(**kw) -> CodexTaskConfig:
    return CodexTaskConfig(consumer_instructions="x", **kw)


def test_resolve_default_workspace_write_no_extras():
    s = resolve_sandbox_settings(_cfg(), host_home="/home/u")
    assert s == SandboxSettings(
        mode="workspace-write", writable_roots=(), network_access=False,
    )


def test_resolve_rw_extras_expand_against_real_host_home():
    s = resolve_sandbox_settings(
        _cfg(extra_allowed_dirs=[
            AllowedDir("~/cache", "rw"),
            AllowedDir("/scratch/", "rw"),
            AllowedDir("~/data", "ro"),   # no-op: codex reads are open
        ]),
        host_home="/home/alice",
    )
    assert s.writable_roots == ("/home/alice/cache", "/scratch")


def test_resolve_fs_isolation_off_is_danger_full_access():
    s = resolve_sandbox_settings(_cfg(fs_isolation=False), host_home="/home/u")
    assert s.mode == "danger-full-access"
    assert s.writable_roots == ()
    assert s.network_access is False


def test_cli_args_minimal_default():
    args = build_sandbox_cli_args(SandboxSettings(mode="workspace-write"))
    assert args == ["--sandbox", "workspace-write"]


def test_cli_args_with_roots_and_network():
    args = build_sandbox_cli_args(SandboxSettings(
        mode="workspace-write",
        writable_roots=("/home/u/cache", "/scratch"),
        network_access=True,
    ))
    assert args[:2] == ["--sandbox", "workspace-write"]
    assert (
        'sandbox_workspace_write.writable_roots=["/home/u/cache", "/scratch"]'
        in args
    )
    assert "sandbox_workspace_write.network_access=true" in args
    # every override rides its own -c
    assert args.count("-c") == 2


def test_cli_args_read_only_and_danger_have_no_overrides():
    assert build_sandbox_cli_args(SandboxSettings(mode="read-only")) == [
        "--sandbox", "read-only",
    ]
    assert build_sandbox_cli_args(
        SandboxSettings(mode="danger-full-access")
    ) == ["--sandbox", "danger-full-access"]
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest packages/optio-codex/tests/test_fs_allowlist.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement** — create `packages/optio-codex/src/optio_codex/fs_allowlist.py`:

```python
"""Settings SSOT for codex's NATIVE sandbox (Stage 8 filesystem isolation).

optio-codex confines the agent's TOOL SUBPROCESSES using codex's own
kernel-level sandbox (bundled bubblewrap primary, Landlock+seccomp fallback
on Linux; helper bins materialize to ``$CODEX_HOME/tmp/arg0/``) rather than
porting optio-claudecode's claustrum. Unlike grok there is no planted
profile file: one resolved :class:`SandboxSettings` renders to

* CLI surfaces (interactive TUI + ``codex exec``): ``--sandbox <mode>`` plus
  ``-c sandbox_workspace_write.writable_roots=[…]`` /
  ``-c sandbox_workspace_write.network_access=true`` overrides; and
* the app-server ``thread/start.sandboxPolicy`` (:func:`build_sandbox_policy`).

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
* Failure mode with NO mechanism available: <FAIL-OPEN|FAIL-CLOSED — copy
  the Task-0 verdict line + one-line evidence here when implementing>.
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
    (iframe argv, exec probe flags, app-server sandboxPolicy) renders from."""

    mode: "SandboxMode"
    writable_roots: tuple[str, ...] = ()
    network_access: bool = False


def resolve_sandbox_settings(
    config: "CodexTaskConfig", *, host_home: str,
) -> SandboxSettings:
    """Resolve ``fs_isolation``/``sandbox``/``extra_allowed_dirs``/
    ``network_access`` into one :class:`SandboxSettings`.

    ``ro`` grants are skipped (codex never restricts reads — see module
    docstring); ``rw`` grants become ``writable_roots`` with ``~/`` expanded
    against ``host_home``. Roots/network only apply to workspace-write
    (validated in CodexTaskConfig.__post_init__).
    """
    mode = config.effective_sandbox_mode
    roots: list[str] = []
    if mode == "workspace-write":
        for ad in config.extra_allowed_dirs or []:
            if ad.mode == "rw":
                roots.append(_expand_home(ad.path, host_home).rstrip("/"))
    return SandboxSettings(
        mode=mode,
        writable_roots=tuple(roots),
        network_access=bool(config.network_access) and mode == "workspace-write",
    )


def _toml_str_array(paths: tuple[str, ...]) -> str:
    # json.dumps output is valid TOML for basic strings.
    return "[" + ", ".join(json.dumps(p) for p in paths) + "]"


def build_sandbox_cli_args(settings: SandboxSettings) -> list[str]:
    """Render settings as codex CLI args (interactive TUI and ``exec``).

    ``--sandbox`` is accepted by both surfaces; ``-c`` values are parsed as
    TOML, so the roots array is emitted in TOML syntax. No overrides are
    emitted outside workspace-write.
    """
    out: list[str] = ["--sandbox", settings.mode]
    if settings.mode != "workspace-write":
        return out
    if settings.writable_roots:
        out += [
            "-c",
            "sandbox_workspace_write.writable_roots="
            + _toml_str_array(settings.writable_roots),
        ]
    if settings.network_access:
        out += ["-c", "sandbox_workspace_write.network_access=true"]
    return out
```

When writing the module docstring, replace the `<FAIL-OPEN|FAIL-CLOSED …>` bracket with the actual Task-0 verdict sentence — the committed file must contain the real verdict, not the bracket.

- [ ] **Step 4: Run** the file, then the full suite → GREEN.
- [ ] **Step 5: Commit** `feat(optio-codex): sandbox-settings SSOT + CLI renderer (Stage 8)`.

---

### Task 3: Iframe wiring — settings resolved once, argv proven via FAKE_CODEX_RECORD

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/host_actions.py` (`build_codex_flags`), `packages/optio-codex/src/optio_codex/session.py` (settings resolution + threading), `packages/optio-codex/tests/fake_codex.py` (durable argv record)
- Test: `packages/optio-codex/tests/test_host_actions.py`, new `packages/optio-codex/tests/test_session_sandbox.py`

**Interfaces:**
- `build_codex_flags(*, model, ask_for_approval="never", sandbox_args: list[str]) -> list[str]` — the `sandbox: str` parameter is REPLACED by the rendered args (single argv-composition seam; kills the duplicate-knob path).
- `session.py`: resolve `host_home = await host.resolve_host_home()` in `_prepare` (Plan B/C likely already fetch it for `~` install-dir expansion — reuse, don't re-fetch), compute `settings = resolve_sandbox_settings(config, host_home=host_home)` ONCE, store it (nonlocal, like `codex_path`), and pass `build_sandbox_cli_args(settings)` into every launch surface this task and Task 4 touch.
- `fake_codex.py`: durable launch record `FAKE_CODEX_RECORD` (grok `fake_grok.py:25-46` pattern — the workdir is wiped on teardown, so the record must live outside it). **Adaptation note:** Plan B's resume test may already have added this — if so, extend the existing record with nothing (argv is all we need) and skip Step 3's fake change.

- [ ] **Step 1: Write the failing tests.**

Append to `packages/optio-codex/tests/test_host_actions.py`:

```python
def test_build_codex_flags_embeds_sandbox_args():
    from optio_codex.host_actions import build_codex_flags

    flags = build_codex_flags(
        model="gpt-5.5",
        ask_for_approval="never",
        sandbox_args=["--sandbox", "workspace-write",
                      "-c", 'sandbox_workspace_write.writable_roots=["/s"]'],
    )
    assert flags.index("--sandbox") < flags.index("--model")
    assert flags[flags.index("--sandbox") + 1] == "workspace-write"
    assert 'sandbox_workspace_write.writable_roots=["/s"]' in flags
```

Create `packages/optio-codex/tests/test_session_sandbox.py`:

```python
"""Session-level wiring test for Stage 8 filesystem isolation (iframe).

Runs a local iframe task with the default ``fs_isolation=True`` and asserts
the fake codex was launched with ``--sandbox workspace-write`` plus the
``-c sandbox_workspace_write.*`` overrides derived from the config. The
workdir is wiped on teardown, so the fake records argv to a durable path
(``FAKE_CODEX_RECORD``) that outlives the task.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from optio_codex import AllowedDir, CodexTaskConfig, create_codex_task


def _launch_record(path: pathlib.Path) -> dict:
    """Last recorded LAUNCH argv (skips `sandbox` subcommand probe records
    — Task 5's launch-time guard, if the Task-0 verdict required one)."""
    lines = [l for l in path.read_text().splitlines() if l.strip()]
    assert lines, f"fake codex wrote no launch record to {path}"
    launches = [
        r for r in map(json.loads, lines)
        if (r["argv"] or [""])[0] != "sandbox"
    ]
    assert launches, "no non-probe launch record found"
    return launches[-1]


@pytest.mark.asyncio
async def test_iframe_sandbox_args_wired(
    shim_install_dir: pathlib.Path,
    task_root,
    ctx_and_captures,
    tmp_path: pathlib.Path,
    monkeypatch,
):
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "happy")
    record = tmp_path / "codex_record.jsonl"
    monkeypatch.setenv("FAKE_CODEX_RECORD", str(record))

    task = create_codex_task(
        process_id="codex-sandbox-iframe",
        name="s",
        config=CodexTaskConfig(
            consumer_instructions="do the thing",
            codex_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            extra_allowed_dirs=[AllowedDir("/scratch", "rw")],
            network_access=True,
        ),
    )
    await task.execute(ctx)

    argv = _launch_record(record)["argv"]
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert "danger-full-access" not in argv
    assert 'sandbox_workspace_write.writable_roots=["/scratch"]' in argv
    assert "sandbox_workspace_write.network_access=true" in argv


@pytest.mark.asyncio
async def test_iframe_unconfined_when_fs_isolation_off(
    shim_install_dir: pathlib.Path,
    task_root,
    ctx_and_captures,
    tmp_path: pathlib.Path,
    monkeypatch,
):
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "happy")
    record = tmp_path / "codex_record.jsonl"
    monkeypatch.setenv("FAKE_CODEX_RECORD", str(record))

    task = create_codex_task(
        process_id="codex-sandbox-off",
        name="s",
        config=CodexTaskConfig(
            consumer_instructions="do the thing",
            codex_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            fs_isolation=False,
        ),
    )
    await task.execute(ctx)

    argv = _launch_record(record)["argv"]
    assert argv[argv.index("--sandbox") + 1] == "danger-full-access"
    assert not any(a.startswith("sandbox_workspace_write.") for a in argv)
```

(Fixture names per `tests/conftest.py`: `shim_install_dir`, `task_root`, `ctx_and_captures`. If Plans B–D changed fixture names, adapt.)

- [ ] **Step 2: Run** both files → FAIL (`build_codex_flags` has no `sandbox_args`; no record written; `--sandbox` value derived from the old field).

- [ ] **Step 3: Implement.**

`fake_codex.py` — add near the top (before scenario dispatch in `main()`), skipping if Plan B already added an equivalent:

```python
import json
import sys


def _record_launch() -> None:
    """Durably record this launch's argv (Stage 2/8 wiring assertions).

    When FAKE_CODEX_RECORD is set, append {"argv": [...]} as one JSON line.
    The workdir is wiped on teardown, so the record must live OUTSIDE it.
    """
    rec = os.environ.get("FAKE_CODEX_RECORD")
    if not rec:
        return
    with open(rec, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": sys.argv[1:]}) + "\n")
```

Call `_record_launch()` first thing in `main()`. The fake ACCEPTS and otherwise IGNORES `--sandbox` and `-c` (parse_known_args already tolerates them — verify `-c` with a value doesn't get eaten as a scenario arg; add `parser.add_argument("-c", action="append", default=[])` explicitly).

`host_actions.py`:

```python
def build_codex_flags(
    *,
    model: str | None,
    ask_for_approval: str = "never",
    sandbox_args: list[str],
) -> list[str]:
    """Translate CodexTaskConfig knobs to an interactive ``codex`` argv list.

    ``sandbox_args`` come pre-rendered from
    ``fs_allowlist.build_sandbox_cli_args`` — the settings SSOT; this
    function stays the single argv-composition seam.
    """
    out: list[str] = ["--ask-for-approval", ask_for_approval, *sandbox_args]
    if model:
        out += ["--model", model]
    return out
```

`session.py` — in `_prepare` (after binary resolution), resolve once:

```python
        nonlocal sandbox_settings
        host_home = await host.resolve_host_home()
        sandbox_settings = resolve_sandbox_settings(config, host_home=host_home)
```

and in the iframe body replace `sandbox=config.sandbox` with `sandbox_args=build_sandbox_cli_args(sandbox_settings)`. Declare `sandbox_settings: SandboxSettings | None = None` alongside the other nonlocals; import from `optio_codex.fs_allowlist`.

- [ ] **Step 4: Run** the full suite → GREEN. Update any existing call site of `build_codex_flags(..., sandbox=…)` (Plans B–D may have added exec/resume call sites — convert them to `sandbox_args`, threading the SAME `sandbox_settings`).
- [ ] **Step 5: Commit** `feat(optio-codex): iframe launch under native sandbox via settings SSOT (Stage 8)`.

---

### Task 4: Conversation `sandboxPolicy` + exec-probe flags from the same SSOT

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/fs_allowlist.py` (add `build_sandbox_policy`), the Plan-D conversation module (thread/start params) and `session.py` conversation body, the Plan-C/D exec-probe helper (`run_codex_probe` or as-landed name) in `host_actions.py`
- Test: `packages/optio-codex/tests/test_fs_allowlist.py`, the Plan-D conversation wiring test file (extend)

**Interfaces:**
- `build_sandbox_policy(settings) -> dict` — the app-server `thread/start.sandboxPolicy` value.
- The conversation bootstrap passes `sandboxPolicy=build_sandbox_policy(sandbox_settings)` in `thread/start`; the exec surface (degraded/batch body if Plan D shipped one) passes `build_sandbox_cli_args(sandbox_settings)`. `verify.py`'s auth probe stays hard `-s read-only --ephemeral` (tightest posture for a throwaway credential check; deliberately NOT task-config-driven — document with a one-line comment).
- **Adaptation note:** exact function/field names come from Plan D's landed code and its vendored app-server schema. Keep the invariant: settings resolved once per session, both renderers consume the same object.

- [ ] **Step 0: Pin the SandboxPolicy JSON field names** from the vendored schema (Plan D vendors `codex app-server generate-json-schema` output):

```bash
.venv/bin/python - <<'EOF'
import json, pathlib
# adjust to the vendored schema path from Plan D:
schema = json.loads(pathlib.Path("packages/optio-codex/src/optio_codex/app_server_schema.json").read_text())
defs = schema.get("definitions") or schema.get("$defs") or {}
print(json.dumps({k: v for k, v in defs.items() if "andbox" in k}, indent=2)[:4000])
EOF
```

If no vendored schema exists, run `codex app-server generate-json-schema` against the real binary and inspect the `SandboxPolicy` definition. Record the exact member names (tag key + `writable_roots`/`writableRoots`, `network_access`/`networkAccess`) in the `build_sandbox_policy` docstring. The code below assumes camelCase (the app-server convention for `sandboxPolicy`/`approvalPolicy`); **fix to the pinned names if they differ** — the test in Step 1 must use the pinned names too.

- [ ] **Step 1: Write the failing tests.** Append to `test_fs_allowlist.py` (adjust field names to the Step-0 pin):

```python
def test_sandbox_policy_workspace_write():
    from optio_codex.fs_allowlist import build_sandbox_policy

    pol = build_sandbox_policy(SandboxSettings(
        mode="workspace-write",
        writable_roots=("/scratch",),
        network_access=True,
    ))
    assert pol["mode"] == "workspace-write"
    assert pol["writableRoots"] == ["/scratch"]
    assert pol["networkAccess"] is True


def test_sandbox_policy_other_modes_are_bare():
    from optio_codex.fs_allowlist import build_sandbox_policy

    assert build_sandbox_policy(SandboxSettings(mode="read-only")) == {
        "mode": "read-only",
    }
    assert build_sandbox_policy(
        SandboxSettings(mode="danger-full-access")
    ) == {"mode": "danger-full-access"}
```

And extend the Plan-D conversation wiring test: the fake app-server responder records the `thread/start` request params (Plan D's fake already parses them to reply) — assert `params["sandboxPolicy"]["mode"] == "workspace-write"` for a default-config conversation task, and `... == "danger-full-access"` for `fs_isolation=False, host_protocol=…` per Plan D's test shape. If the fake does not yet persist received params durably, extend its `FAKE_CODEX_RECORD` line with `{"thread_start_params": …}` — same record file, new key, no second mechanism.

- [ ] **Step 2: Run** → FAIL (`build_sandbox_policy` missing; conversation sends no sandboxPolicy).

- [ ] **Step 3: Implement.** In `fs_allowlist.py`:

```python
def build_sandbox_policy(settings: SandboxSettings) -> dict:
    """Render settings as the app-server ``thread/start.sandboxPolicy``.

    Field names pinned from the vendored app-server schema (codex-cli
    0.142.5) — see Plan E Task 4 Step 0; re-pin on version bumps.
    """
    if settings.mode == "workspace-write":
        return {
            "mode": "workspace-write",
            "writableRoots": list(settings.writable_roots),
            "networkAccess": settings.network_access,
        }
    return {"mode": settings.mode}
```

Wire it into the conversation bootstrap's `thread/start` params (the `_conversation_body` already threads config → conversation per Plan D; add `sandbox_settings`). Convert the exec-probe/degraded-mode call sites to `build_sandbox_cli_args(sandbox_settings)`; leave `verify.py` on literal `-s read-only` with the comment.

- [ ] **Step 4: Run** the full suite → GREEN.
- [ ] **Step 5: Commit** `feat(optio-codex): conversation sandboxPolicy + exec probe flags from sandbox SSOT (Stage 8)`.

---

### Task 5: Failure-mode branch — launch-time enforcement guard (5A, if Task 0 = FAIL-OPEN) or evidence-only (5B, if FAIL-CLOSED)

Execute **exactly one** of 5A/5B according to the Task-0 verdict recorded in the design doc. Do not skip 5B's steps on the grounds that "nothing changes" — the evidence pin is the deliverable.

#### Task 5A (verdict = FAIL-OPEN): loud launch-time guard

If codex runs unconfined when no mechanism is available, a task configured with `fs_isolation=True` MUST fail loudly at launch rather than run exposed (grok solved this with fail-closed custom profiles; codex has no equivalent, so optio enforces it).

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/host_actions.py` (new `assert_sandbox_enforcing`), `session.py` (`_prepare` call), `tests/fake_codex.py` (`sandbox` subcommand)
- Test: `packages/optio-codex/tests/test_session_sandbox.py`

**Interfaces:**
- `async def assert_sandbox_enforcing(host, *, codex_path, workdir, host_home, sandbox_args) -> None` — raises `RuntimeError` when a probe write OUTSIDE every writable root succeeds. Called from `_prepare` when `config.fs_isolation` (all modes/bodies — the guard is mode-independent).
- fake codex `sandbox` subcommand: default = deny (exit 1, no file, "Read-only file system" on stderr); `FAKE_CODEX_SANDBOX_BROKEN=1` = simulate fail-open (run the command unconfined, exit 0).

- [ ] **Step 1: Write the failing tests** — append to `test_session_sandbox.py`:

```python
@pytest.mark.asyncio
async def test_launch_fails_loud_when_sandbox_not_enforcing(
    shim_install_dir: pathlib.Path,
    task_root,
    ctx_and_captures,
    tmp_path: pathlib.Path,
    monkeypatch,
):
    """Task-0 verdict was FAIL-OPEN: with fs_isolation=True and a host where
    the sandbox cannot enforce, the task must die at launch, not run exposed."""
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "happy")
    monkeypatch.setenv("FAKE_CODEX_SANDBOX_BROKEN", "1")
    record = tmp_path / "codex_record.jsonl"
    monkeypatch.setenv("FAKE_CODEX_RECORD", str(record))

    task = create_codex_task(
        process_id="codex-sandbox-broken",
        name="s",
        config=CodexTaskConfig(
            consumer_instructions="do the thing",
            codex_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
        ),
    )
    with pytest.raises(RuntimeError, match="NOT enforcing"):
        await task.execute(ctx)


@pytest.mark.asyncio
async def test_launch_guard_probe_ran_and_launch_proceeded(
    shim_install_dir: pathlib.Path,
    task_root,
    ctx_and_captures,
    tmp_path: pathlib.Path,
    monkeypatch,
):
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "happy")
    record = tmp_path / "codex_record.jsonl"
    monkeypatch.setenv("FAKE_CODEX_RECORD", str(record))

    task = create_codex_task(
        process_id="codex-sandbox-guarded",
        name="s",
        config=CodexTaskConfig(
            consumer_instructions="do the thing",
            codex_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
        ),
    )
    await task.execute(ctx)

    records = [
        json.loads(l) for l in record.read_text().splitlines() if l.strip()
    ]
    probes = [r for r in records if (r["argv"] or [""])[0] == "sandbox"]
    assert probes, "fs_isolation=True must run the enforcement probe"
    launches = [r for r in records if (r["argv"] or [""])[0] != "sandbox"]
    assert launches, "the real launch must still happen after a denying probe"
```

- [ ] **Step 2: Run** → FAIL (no guard, no `sandbox` subcommand in the fake — the BROKEN test errors differently or hangs; fix forward).

- [ ] **Step 3: Implement.**

`fake_codex.py` — at the top of `main()`, after `_record_launch()`:

```python
    if len(sys.argv) > 1 and sys.argv[1] == "sandbox":
        # Launch-time enforcement probe (Stage 8 guard). Default: behave
        # like a working sandbox (deny + nonzero). FAKE_CODEX_SANDBOX_BROKEN=1
        # simulates a fail-open host: run the command unconfined, exit 0.
        if os.environ.get("FAKE_CODEX_SANDBOX_BROKEN") == "1":
            import subprocess
            cmd = sys.argv[sys.argv.index("--") + 1:]
            subprocess.run(cmd, check=False)
            return 0
        print("Read-only file system (fake sandbox deny)", file=sys.stderr)
        return 1
```

`host_actions.py` (use the Task-0 pinned `codex sandbox` invocation form for the mode flags — the snippet passes the rendered `sandbox_args` through; if the pinned form differs, e.g. only `-c sandbox_mode=…` is accepted by the subcommand, translate here and note it in the docstring):

```python
async def assert_sandbox_enforcing(
    host: "Host",
    *,
    codex_path: str,
    workdir: str,
    host_home: str,
    sandbox_args: list[str],
) -> None:
    """Fail LOUD if codex's sandbox is not actually enforcing (Stage 8).

    Task-0 verdict (codex-cli 0.142.5): codex FAILS OPEN when no sandbox
    mechanism (bubblewrap or Landlock) is available, so requesting
    ``--sandbox workspace-write`` is no guarantee. Before launch we probe a
    write OUTSIDE every writable root via ``codex sandbox -- touch <canary>``
    and refuse to run if it lands. The canary lives in the REAL host home:
    the workdir/cwd and /tmp are writable in workspace-write, so neither can
    serve as a deny target.
    """
    workdir_clean = workdir.rstrip("/")
    canary = (
        f"{host_home.rstrip('/')}/.optio-codex-sandbox-canary-"
        f"{hashlib.sha256(workdir_clean.encode()).hexdigest()[:12]}"
    )
    iso = _isolation_env(workdir_clean)
    env_bits = " ".join(f"{k}={shlex.quote(v)}" for k, v in iso.items())
    probe_argv = " ".join(
        shlex.quote(a)
        for a in [codex_path, "sandbox", *sandbox_args, "--", "touch", canary]
    )
    cmd = (
        f"cd {shlex.quote(workdir_clean)} && rm -f {shlex.quote(canary)} && "
        f"env {env_bits} {probe_argv}; rc=$?; "
        f"if [ -e {shlex.quote(canary)} ]; then "
        f"rm -f {shlex.quote(canary)}; echo OPTIO_SANDBOX_LEAKED; fi; "
        f"exit $rc"
    )
    r = await host.run_command(cmd)
    if "OPTIO_SANDBOX_LEAKED" in (r.stdout or ""):
        raise RuntimeError(
            "codex sandbox is NOT enforcing on this host: a probe write "
            "outside the workspace succeeded (no working bubblewrap or "
            "Landlock — codex fails open). Refusing to launch with "
            "fs_isolation=True. Set fs_isolation=False to run unconfined, "
            "or provision a sandbox-capable kernel/worker."
        )
```

`session.py` `_prepare`, immediately after `sandbox_settings` is resolved:

```python
        if config.fs_isolation:
            await host_actions.assert_sandbox_enforcing(
                host,
                codex_path=codex_path,
                workdir=host.workdir,
                host_home=host_home,
                sandbox_args=build_sandbox_cli_args(sandbox_settings),
            )
```

- [ ] **Step 4: Run** the full suite → GREEN (every default-on session test now exercises the guard against the denying fake).
- [ ] **Step 5: Commit** `feat(optio-codex): launch-time sandbox-enforcement guard (fail-open verdict, Stage 8)`.

#### Task 5B (verdict = FAIL-CLOSED): evidence pin, no guard

- [ ] **Step 1:** Confirm the design-doc verdict subsection (Task 0 Step 6) contains the full evidence chain (all three probe branches + doctor lines + strings). If any evidence line is thin, re-run that probe and complete it.
- [ ] **Step 2:** Ensure the `fs_allowlist.py` module docstring's failure-mode bullet states FAIL-CLOSED with the one-line evidence and probe date/version (Task 2 already required filling it — verify it matches the verdict verbatim).
- [ ] **Step 3:** Add a README note (folded into Task 8's sandbox section): fs_isolation relies on codex's own fail-closed behavior; no optio-side guard is needed and none exists.
- [ ] **Step 4:** Full suite → GREEN (nothing changed). **Commit** `docs(optio-codex): pin fail-closed sandbox evidence; no launch guard needed (Stage 8)`.

---

### Task 6: Real-binary enforcement test (`test_sandbox_enforce.py`, env-gated)

Prove the isolation is genuinely kernel-enforced, not merely requested — against the REAL codex binary. Grok's analogue needs a live authenticated ~180s agent run; codex offers `codex sandbox -- <cmd>` (raw command under the sandbox, **no model call, no auth, no billing**), so this test is auth-free and runs in seconds — a deliberate structural divergence from `optio-grok/tests/test_sandbox_enforce.py`, kept env-gated because it depends on a real binary + kernel mechanism. **Never in the default suite.**

**Files:**
- Create: `packages/optio-codex/tests/test_sandbox_enforce.py`

- [ ] **Step 1: Write the test** (opt-in, so "failing first" here means: run WITH the gate env set on this host and watch it fail before implementation-details are fixed; the default suite must show it SKIPPED):

```python
"""Real-codex sandbox enforcement test for Stage 8 (opt-in, env-gated).

Unlike the rest of the suite (fake codex), this exercises the REAL codex
binary's sandbox via ``codex sandbox -- <cmd>`` and verifies that a write
OUTSIDE the workspace (the operator's real home — /tmp and the cwd are
writable in workspace-write, so neither can serve as a deny target) is
denied by the kernel, a write INSIDE the cwd is allowed, and a
``writable_roots`` grant is honored — proving isolation is enforced, not
merely requested.

Divergence from grok's analogue: ``codex sandbox`` runs a raw command with
NO model call, so no auth is needed and the test costs nothing — it stays
opt-in (OPTIO_CODEX_SANDBOX_ENFORCE_TEST=1) purely because it requires a
real binary and a sandbox-capable kernel (bubblewrap or Landlock). It NEVER
runs in the default suite.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest


# The pinned `codex sandbox` workspace-write invocation form (Plan E Task 0,
# codex-cli 0.142.5). Update here if the pin differs.
_WS_WRITE_ARGS: list[str] = ["--sandbox", "workspace-write"]


def _cache_dir() -> Path:
    root = os.environ.get("OPTIO_CODEX_CACHE_DIR")
    if root:
        return Path(root)
    xdg = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    return Path(xdg) / "optio-codex" / "bin"


def _resolve_real_codex() -> "str | None":
    found = shutil.which("codex")
    if found:
        return found
    cached = _cache_dir() / "codex"
    return str(cached) if cached.exists() else None


def _skip_reason() -> "str | None":
    # Opt-in only: requires the REAL codex binary and a sandbox-capable
    # kernel. Never runs in the default suite.
    if os.environ.get("OPTIO_CODEX_SANDBOX_ENFORCE_TEST") != "1":
        return (
            "set OPTIO_CODEX_SANDBOX_ENFORCE_TEST=1 to run the real-codex "
            "enforcement test"
        )
    if platform.system() != "Linux":
        return "sandbox enforcement test requires Linux"
    if _resolve_real_codex() is None:
        return "real codex binary not found (PATH or optio cache)"
    return None


pytestmark = pytest.mark.skipif(
    _skip_reason() is not None, reason=_skip_reason() or "",
)


def _run_sandboxed(
    argv_tail: list[str], *, cwd: Path, codex_home: Path,
    extra_args: "list[str] | None" = None,
) -> subprocess.CompletedProcess:
    codex = _resolve_real_codex()
    env = {
        **os.environ,
        "HOME": str(cwd / "home"),
        "CODEX_HOME": str(codex_home),
    }
    return subprocess.run(
        [codex, "sandbox", *_WS_WRITE_ARGS, *(extra_args or []), "--",
         *argv_tail],
        cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120,
    )


@pytest.fixture()
def sandbox_env(tmp_path: Path):
    """Workspace + throwaway CODEX_HOME; skip (not fail) when the kernel has
    no usable sandbox mechanism (that's an environment verdict, not a bug —
    the launch-time posture for such hosts is covered by Task 5)."""
    workdir = tmp_path / "work"
    codex_home = workdir / "home" / ".codex"
    codex_home.mkdir(parents=True)   # helper bins go to $CODEX_HOME/tmp/arg0/
    probe = _run_sandboxed(["true"], cwd=workdir, codex_home=codex_home)
    if probe.returncode != 0:
        pytest.skip(
            "codex sandbox cannot run here: "
            f"rc={probe.returncode} stderr={probe.stderr.strip()[:300]!r}"
        )
    return workdir, codex_home


def test_outside_write_denied(sandbox_env):
    workdir, codex_home = sandbox_env
    real_home = Path.home()
    canary = real_home / f".optio-codex-enforce-probe-{os.getpid()}"
    if canary.exists():
        canary.unlink()
    try:
        r = _run_sandboxed(
            ["touch", str(canary)], cwd=workdir, codex_home=codex_home,
        )
        assert not canary.exists(), (
            "sandbox FAILED: codex wrote outside the workspace "
            f"(rc={r.returncode}, stderr={r.stderr[:300]!r})"
        )
        assert r.returncode != 0
    finally:
        if canary.exists():
            canary.unlink()


def test_inside_write_allowed(sandbox_env):
    workdir, codex_home = sandbox_env
    target = workdir / "inside.txt"
    r = _run_sandboxed(
        ["touch", str(target)], cwd=workdir, codex_home=codex_home,
    )
    assert r.returncode == 0, r.stderr[:300]
    assert target.exists()


def test_writable_roots_grant_honored(sandbox_env, tmp_path: Path):
    """A -c sandbox_workspace_write.writable_roots grant makes an
    otherwise-denied dir writable — the exact plumbing optio's
    extra_allowed_dirs(rw) rides on. Uses a real-home subdir because /tmp is
    already writable in workspace-write."""
    workdir, codex_home = sandbox_env
    grant_dir = Path.home() / f".optio-codex-grant-{os.getpid()}"
    grant_dir.mkdir(exist_ok=True)
    target = grant_dir / "granted.txt"
    try:
        r = _run_sandboxed(
            ["touch", str(target)], cwd=workdir, codex_home=codex_home,
            extra_args=[
                "-c",
                f'sandbox_workspace_write.writable_roots=["{grant_dir}"]',
            ],
        )
        assert r.returncode == 0, r.stderr[:300]
        assert target.exists()
    finally:
        shutil.rmtree(grant_dir, ignore_errors=True)
```

- [ ] **Step 2: Prove the gate** — default suite: `.venv/bin/python -m pytest packages/optio-codex/tests/test_sandbox_enforce.py -q` → all SKIPPED with the opt-in reason.
- [ ] **Step 3: Run gated on this host** — `OPTIO_CODEX_SANDBOX_ENFORCE_TEST=1 .venv/bin/python -m pytest packages/optio-codex/tests/test_sandbox_enforce.py -q -rs` → 3 passed (this host exercises the Landlock fallback path per the claustrum findings — bwrap/userns fail here; that is exactly the interesting configuration). If `_WS_WRITE_ARGS` doesn't match the Task-0 pin, fix the constant, not the assertions.
- [ ] **Step 4:** Full default suite → GREEN (enforce tests SKIPPED). **Commit** `test(optio-codex): real-codex sandbox enforcement, auth-free via codex-sandbox surface (opt-in env gate)`.

---

### Task 7: Final parity audit — Appendix A × 29 with per-item evidence

Investigation task with a recorded deliverable: walk every Appendix-A item of `docs/writing-agent-wrappers.md` against the FINISHED tree (post B/C/D/E) and produce the audit doc. This is the review doc's (`docs/2026-07-02-optio-codex-stage0-review.md`) "roughly 8–9 of 29" scoreboard, redone at the end with evidence.

**Files:**
- Create: `docs/2026-07-02-optio-codex-parity-audit.md`

- [ ] **Step 1: Walk all 29 items.** For each item of the Appendix-A table, find the implementing code AND its test, and record `file:line` evidence (absolute repo-relative paths). Verify claims by reading the code, not by trusting plan documents — the stage-0 review's method ("every finding independently verified") is the bar. Items to check per the checklist: 1 iframe, 2 conversation, 3 conversation-ui widget (in `optio-conversation-ui/src/codex/`), 4 optio.log protocol, 5 local+remote SSH, 6 readiness/monitoring/teardown, 7 resume/snapshots, 8 at-rest encryption (threaded-not-activated counts as grok-parity — say so), 9 crash-orphan rescue, 10 auto-resume, 11 seeds, 12 leases, 13 cred save-back, 14 verify/refresh, 15 binary cache, 16 HOME/XDG isolation, 17 hooks, 18 prompt SSOT, 19 permission gating, 20 model switching, 21 upload, 22 download, 23 tool verbosity, 24 session restore/rebase, 25 filesystem isolation, 26 browser handling, 27 headless-login strategy, 28 packaging/registration, 29 demo trio.
- [ ] **Step 2: Write the doc** with this exact structure:

```markdown
# optio-codex — final Appendix-A parity audit

**Date:** <date>. **Tree:** branch `csillag/optio-codex` @ <commit>.
**Yardstick:** docs/writing-agent-wrappers.md Appendix A (29 items).
**Method:** every row verified by reading the cited code/tests, not plans.

| # | Capability | Req/Opt | Status | Evidence (file:line) | Test evidence |
|---|---|---|---|---|---|
| 1 | iframe mode | opt | GREEN | packages/optio-codex/src/optio_codex/session.py:<line> | tests/test_session_local.py:<line> |
| … all 29 rows, no omissions … |

## Remaining opt gaps
- <item #>: <one-line reason it is deliberately not shipped + README cross-ref>

## Verdict
<N>/29 green; all `req` items green. <or the honest alternative>
```

- [ ] **Step 3: STOP rule.** If ANY `req` item (2, 3, 4, 5, 6, 11, 15, 16, 17, 18, 26, 27, 28, 29) is not green, halt the plan and surface the gap to the user — do not paper over it in the audit, and do not proceed to Tasks 8–9 (they publish claims this audit is supposed to back).
- [ ] **Step 4: Commit** `docs(optio-codex): final Appendix-A parity audit (29 items, per-item evidence)`.

---

### Task 8: README truth-up + design-doc "as shipped" reconciliation

**Files:**
- Modify: `packages/optio-codex/README.md`, `docs/2026-07-02-optio-codex-design.md`

- [ ] **Step 1: README Status section.** Replace the Stage-0 "Status — Stage 0 (MVP)" section with the shipped-surface truth, reconciled against the Task-7 audit (every claim must have a green audit row; every remaining gap must appear in the audit's gap list). Required content — adjust wording to what actually shipped:
  - Shipped: iframe/ttyd + conversation (app-server) modes; local + remote SSH; resume/snapshots (session-id keyed); seeds + leases + credential save-back + verify; binary cache with real auto-download; conversation-ui widget + permission gate + inline model switching + upload/download + tool verbosity; **filesystem isolation via codex's native sandbox** (default-ON `fs_isolation`, `extra_allowed_dirs`, `network_access`); demo trio.
  - New "Sandbox" subsection documenting: mechanism (bubblewrap primary / Landlock fallback, kernel-enforced, covers all tool subprocesses); `fs_isolation=True` default ⇒ `workspace-write` (writes confined to workdir + `/tmp` + `rw` grants; **reads are NOT restricted** — divergence from grok/claudecode, `AllowedDir("…","ro")` is a documented no-op); network OFF by default (`network_access=True` to relax — stricter than the other wrappers); `fs_isolation=False` ⇒ `danger-full-access`; the Task-0 failure-mode verdict and (5A) the launch-time guard semantics — a task on a sandbox-incapable host fails loudly rather than running exposed — or (5B) codex's own fail-closed behavior; `.codex/`/`.git/` stay RO to the agent's shell.
  - Remaining opt gaps (if any) listed honestly with one-line reasons — mirror the audit.
- [ ] **Step 2: Design-doc reconciliation.** Append `## Implementation reconciliation (as shipped)` to `docs/2026-07-02-optio-codex-design.md` (grok convention — `docs/2026-07-02-optio-grok-design.md` §7 is the template): every decided-during-build deviation from the design sections above, including at minimum: the `sandbox: SandboxMode | None` reconciliation semantics, the ro-grant no-op decision + rationale, `network_access` default, the Task-0 verdict + guard (or no-guard) outcome, the auth-free enforcement-test divergence from grok's structure, plus any B/C/D deviations not yet recorded there. Also update the design doc's Stage-8 paragraph sentence "`AllowedDir(mode="ro")` semantics need a decision in the Stage-8 plan" → point to the decision. State the final test counts (suite green evidence) in the section header line, grok-style.
- [ ] **Step 3:** Full suite → GREEN (docs only). **Commit** `docs(optio-codex): README truth-up + design as-shipped reconciliation`.

---

### Task 9: Version + release-registration sanity

**Files:**
- Verify (modify only if a check fails): `packages/optio-codex/pyproject.toml`, root `Makefile`, `packages/optio-demo/pyproject.toml`, `packages/optio-demo/Makefile`

- [ ] **Step 1: PyPI state → version decision.** `curl -s -o /dev/null -w '%{http_code}' https://pypi.org/pypi/optio-codex/json` — expected `404` (never published; the Stage-0 review's M2 registration was premature but nothing shipped). If 404: **keep `version = "0.1.0"`** as the first published version — a first release does not bump. If 200 (someone published): read the published version and bump minor above it, updating the optio-demo floor to match.
- [ ] **Step 2: Registration checks** (all expected to already pass — they were landed early and flagged by the review as premature; Plan E is the moment they become true):
  - `grep RELEASABLE_PY Makefile` → contains `optio-codex` (already registered).
  - `grep -n optio-codex packages/optio-demo/pyproject.toml` → `optio-codex>=0.1,<0.2` — consistent with the Step-1 version; the demo actually imports it now (Plan C/D demo tasks): `grep -rn "optio_codex" packages/optio-demo/src | head`.
  - `grep -n "PY_PACKAGES\|optio-codex" Makefile` → optio-codex in the repo test target (Plan A Task); `grep -n "optio-codex" packages/optio-demo/Makefile` → in the editable-install list.
  - Release hygiene (user's release rules): `git status --short` at release time must be clean — flag any stray untracked files now; note in the commit body that the release itself is a separate, user-approved step (**never merge/publish without explicit user approval**).
- [ ] **Step 3: Final full-tree verification.** `.venv/bin/python -m pytest packages/optio-codex/tests/ -q` → all green (real-agent/enforce tests SKIPPED); if Plan D touched `optio-conversation-ui`, run its TS suite too (`packages/optio-conversation-ui`: `node_modules/.bin/vitest run` or the package's `make test` — never npx). Record the counts; they feed the Task-8 reconciliation header if not already written (order note: run this check BEFORE finalizing Task 8's counts, or update them here).
- [ ] **Step 4: Commit** (only if something changed) `chore(optio-codex): release-readiness sanity (version/registration/floors)`. If nothing changed, record "verified, no changes" in the execution notes and skip the commit.

---

## Self-Review

- **Scope ↔ tasks:** Task 0 = fail-open analysis with concrete probe recipes and a recorded verdict that BRANCHES the plan (5A guard vs 5B evidence) — the grok lesson applied empirically, not assumed. Tasks 1–2 = config semantics reconciled with the pre-existing `sandbox` field (no duplicate knobs; `fs_isolation=True` forbids `danger-full-access`; ro grants accepted as a documented no-op with the additive-grant rationale — the "question the constraint" answer, not a silent lie and not a gratuitous ValueError). Task 3–4 = all three launch surfaces (iframe argv, exec probe flags, app-server sandboxPolicy) render from ONE resolved `SandboxSettings` (settings SSOT in `fs_allowlist`, argv seam in `host_actions.build_codex_flags` — satisfies both the guide's #25 pointer convention and the SSOT-in-host_actions requirement). Task 6 = real-binary enforcement, env-gated `OPTIO_CODEX_SANDBOX_ENFORCE_TEST=1`, never-in-default-suite, with the deliberate auth-free divergence from grok's structure justified (codex's `codex sandbox` raw-command surface exists; grok had none). Tasks 7–9 = release readiness: 29-item evidence audit with a STOP rule on red `req` items, README/design truth-up per grok convention, version/registration sanity with the never-publish-without-approval guard.
- **No placeholders:** every bracketed `<…>` in this plan appears only inside *instructions to record probed values* (Task 0 verdict template, Task 4 schema pin, audit skeleton) with an explicit "fill with actual values before committing" rule; all Python code is complete and runnable as written, with pinned-form constants (`_WS_WRITE_ARGS`) called out as the single adjustment point.
- **Parallel-plan safety:** Plans B–D did not exist in `docs/` when this plan was finalized; the Global Constraints mandate a collision re-check (`network_access`, `AllowedDir`, `FAKE_CODEX_RECORD`, `fs_allowlist`, fixture names) before Task 1, and every task touching B/C/D artifacts carries an adaptation note that preserves the invariant while renaming to as-landed symbols.
- **Canary-placement correctness re-checked:** deny probes never target the cwd or `/tmp` (both writable in workspace-write) — the launch guard and the enforcement test both probe the REAL host home, with cleanup in every exit path.
- **Fail-loud discipline:** default-on `fs_isolation`; a sandbox-incapable host either can't run (5B fail-closed) or is refused at launch with an actionable error (5A); the fake exercises both the deny and the broken path so the guard itself is tested.
- **Tree green per task**, conventional commits, no Co-Authored-By, venv-only, real-agent tests opt-in only.
