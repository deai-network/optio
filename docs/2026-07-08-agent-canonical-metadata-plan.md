# Canonical Agent Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every agent wrapper one canonical user-facing name and URL, stored SSOT-per-engine, aggregated in `optio-agents-all`, and used consistently in all user-facing communication.

**Architecture:** A frozen `AgentInfo` dataclass in the base `optio-agents` package. Each of the 7 wrappers declares one `AGENT_INFO` constant (SSOT). `optio-agents-all` imports and aggregates them into an `AGENTS` map + `get_agent_info()` lookup, keyed by the existing `AgentType` slug. Per-engine work also unifies the internal `agent_label` and sweeps hardcoded engine names out of user-facing strings.

**Tech Stack:** Python, dataclasses, pytest (+ pytest-xdist two-phase `make test`), pnpm monorepo, uv/pip editable installs.

## Global Constraints

- Canonical data (verbatim — slug / name / URL):
  - `claudecode` / `Claude Code` / `https://claude.com/product/claude-code`
  - `opencode` / `OpenCode` / `https://opencode.ai`
  - `codex` / `Codex` / `https://openai.com/codex`
  - `cursor` / `Cursor CLI` / `https://cursor.com/cli`
  - `grok` / `Grok Build` / `https://x.ai/cli`
  - `kimicode` / `Kimi Code` / `https://www.kimi.com/coding`
  - `antigravity` / `Antigravity CLI` / `https://antigravity.google`
- `AgentInfo.slug` MUST equal the engine's existing `agent_type` Literal.
- Do NOT auto-default the task `name` in `create_*_task` — `name` stays caller-supplied (explicitly out of scope).
- Do NOT change the `agent_type` discriminant or the `AgentType` union.
- No cross-package file edits inside an engine task — each engine task touches only its own `packages/optio-<engine>/` tree. Each executing agent runs `git add` on its own paths only (never `-A`).
- Package layout: base type in `packages/optio-agents/src/optio_agents/`; per engine in `packages/optio-<engine>/src/optio_<engine>/`; aggregation in `packages/optio-agents-all/src/optio_agents_all/`.
- Editable installs are global — execute in a worktree `.venv`; reinstall the edited package from the worktree before running its tests.

---

### Task 1: `AgentInfo` type in `optio-agents` (base)

Gates every engine task — they import `AgentInfo` from here.

**Files:**
- Create: `packages/optio-agents/src/optio_agents/agent_info.py`
- Modify: `packages/optio-agents/src/optio_agents/__init__.py`
- Test: `packages/optio-agents/tests/test_agent_info.py`

**Interfaces:**
- Produces: `from optio_agents import AgentInfo` — frozen dataclass with fields `slug: str`, `name: str`, `url: str`.

- [ ] **Step 1: Write the failing test**

```python
# packages/optio-agents/tests/test_agent_info.py
import dataclasses
import pytest
from optio_agents import AgentInfo


def test_agent_info_fields_and_frozen():
    info = AgentInfo(slug="x", name="X Name", url="https://x.example")
    assert info.slug == "x"
    assert info.name == "X Name"
    assert info.url == "https://x.example"
    with pytest.raises(dataclasses.FrozenInstanceError):
        info.name = "changed"  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/optio-agents/tests/test_agent_info.py -v`
Expected: FAIL — `ImportError: cannot import name 'AgentInfo'`.

- [ ] **Step 3: Write minimal implementation**

```python
# packages/optio-agents/src/optio_agents/agent_info.py
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentInfo:
    """Canonical, user-facing metadata for an agent engine."""

    slug: str  # machine id, equals the engine's agent_type ("claudecode")
    name: str  # canonical user-facing name ("Claude Code")
    url: str   # canonical product URL
```

Add to `packages/optio-agents/src/optio_agents/__init__.py` (follow the existing re-export style in that file):

```python
from .agent_info import AgentInfo
```

and add `"AgentInfo"` to `__all__` if the file defines one.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest packages/optio-agents/tests/test_agent_info.py -v`
Expected: PASS (2 assertions, frozen raise).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-agents/src/optio_agents/agent_info.py \
        packages/optio-agents/src/optio_agents/__init__.py \
        packages/optio-agents/tests/test_agent_info.py
git commit -m "feat(optio-agents): add AgentInfo canonical-metadata type"
```

---

### Task 2: `claudecode` engine metadata

**Files:**
- Create: `packages/optio-claudecode/src/optio_claudecode/info.py`
- Modify: `packages/optio-claudecode/src/optio_claudecode/__init__.py`
- Modify: `packages/optio-claudecode/src/optio_claudecode/conversation.py` (agent_label ≈ line 39)
- Modify: user-facing message sites in `packages/optio-claudecode/src/optio_claudecode/` (sweep — discover during Step 5)
- Test: `packages/optio-claudecode/tests/test_agent_info.py`

**Interfaces:**
- Consumes: `from optio_agents import AgentInfo` (Task 1).
- Produces: `from optio_claudecode import AGENT_INFO` — `AgentInfo(slug="claudecode", name="Claude Code", url="https://claude.com/product/claude-code")`.

- [ ] **Step 1: Write the failing test**

```python
# packages/optio-claudecode/tests/test_agent_info.py
from optio_claudecode import AGENT_INFO
from optio_claudecode.types import ClaudeCodeTaskConfig  # adjust import if needed


def test_agent_info_values():
    assert AGENT_INFO.slug == "claudecode"
    assert AGENT_INFO.name == "Claude Code"
    assert AGENT_INFO.url == "https://claude.com/product/claude-code"


def test_agent_info_slug_matches_agent_type():
    # agent_type Literal default on the config discriminant must equal the slug
    import dataclasses
    fields = {f.name: f for f in dataclasses.fields(ClaudeCodeTaskConfig)}
    assert fields["agent_type"].default == AGENT_INFO.slug
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/optio-claudecode/tests/test_agent_info.py -v`
Expected: FAIL — `ImportError: cannot import name 'AGENT_INFO'`.

- [ ] **Step 3: Write minimal implementation**

```python
# packages/optio-claudecode/src/optio_claudecode/info.py
from optio_agents import AgentInfo

AGENT_INFO = AgentInfo(
    slug="claudecode",
    name="Claude Code",
    url="https://claude.com/product/claude-code",
)
```

Add to `packages/optio-claudecode/src/optio_claudecode/__init__.py` (match existing re-export style):

```python
from .info import AGENT_INFO
```

and add `"AGENT_INFO"` to `__all__` if present.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest packages/optio-claudecode/tests/test_agent_info.py -v`
Expected: PASS.

- [ ] **Step 5: Unify `agent_label` + sweep user-facing names**

In `conversation.py` replace the hardcoded label default (`agent_label = "claude"`, ≈ line 39) so it derives from the slug:

```python
from .info import AGENT_INFO
# ...
agent_label: str = AGENT_INFO.slug   # "claudecode" (was "claude")
```

Then sweep. Grep the package for every user-facing name-token variant:

```bash
grep -rniE 'claude code|claude' packages/optio-claudecode/src/optio_claudecode/ \
  --include=*.py | grep -viE '#|"""|import|agent_type|slug'
```

For each hit that is a **user-facing** string (status lines, progress, launch/download notices, e.g. `"launching Claude Code"`), replace the literal with `AGENT_INFO.name`. Skip: the `agent_label`/debug-log prefixes (already handled), code needing the raw slug, comments/docstrings. Re-run the package's existing tests after edits:

Run: `pytest packages/optio-claudecode/ -q`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/info.py \
        packages/optio-claudecode/src/optio_claudecode/__init__.py \
        packages/optio-claudecode/src/optio_claudecode/conversation.py \
        packages/optio-claudecode/tests/test_agent_info.py
# add any additional swept files under packages/optio-claudecode/ that you edited
git commit -m "feat(optio-claudecode): canonical AgentInfo, slug label, name sweep"
```

---

### Task 3: `opencode` engine metadata

`opencode`'s conversation ctor currently has NO `agent_label` — add one.

**Files:**
- Create: `packages/optio-opencode/src/optio_opencode/info.py`
- Modify: `packages/optio-opencode/src/optio_opencode/__init__.py`
- Modify: `packages/optio-opencode/src/optio_opencode/conversation.py` (add agent_label)
- Modify: swept user-facing sites under `packages/optio-opencode/src/optio_opencode/`
- Test: `packages/optio-opencode/tests/test_agent_info.py`

**Interfaces:**
- Consumes: `from optio_agents import AgentInfo` (Task 1).
- Produces: `from optio_opencode import AGENT_INFO` — `AgentInfo(slug="opencode", name="OpenCode", url="https://opencode.ai")`.

- [ ] **Step 1: Write the failing test**

```python
# packages/optio-opencode/tests/test_agent_info.py
from optio_opencode import AGENT_INFO
from optio_opencode.types import OpencodeTaskConfig


def test_agent_info_values():
    assert AGENT_INFO.slug == "opencode"
    assert AGENT_INFO.name == "OpenCode"
    assert AGENT_INFO.url == "https://opencode.ai"


def test_agent_info_slug_matches_agent_type():
    import dataclasses
    fields = {f.name: f for f in dataclasses.fields(OpencodeTaskConfig)}
    assert fields["agent_type"].default == AGENT_INFO.slug
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/optio-opencode/tests/test_agent_info.py -v`
Expected: FAIL — `ImportError: cannot import name 'AGENT_INFO'`.

- [ ] **Step 3: Write minimal implementation**

```python
# packages/optio-opencode/src/optio_opencode/info.py
from optio_agents import AgentInfo

AGENT_INFO = AgentInfo(
    slug="opencode",
    name="OpenCode",
    url="https://opencode.ai",
)
```

Add `from .info import AGENT_INFO` to `__init__.py` (+ `__all__` if present).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest packages/optio-opencode/tests/test_agent_info.py -v`
Expected: PASS.

- [ ] **Step 5: Add `agent_label` + sweep user-facing names**

Add an `agent_label` to the conversation class deriving from the slug (mirror the field other engines use for log prefixes):

```python
from .info import AGENT_INFO
# ...
agent_label: str = AGENT_INFO.slug   # "opencode"
```

Sweep:

```bash
grep -rniE 'opencode' packages/optio-opencode/src/optio_opencode/ \
  --include=*.py | grep -viE '#|"""|import|agent_type|slug'
```

Replace user-facing literals with `AGENT_INFO.name` (`"OpenCode"`). Skip debug prefixes / raw-slug code / comments.

Run: `pytest packages/optio-opencode/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/info.py \
        packages/optio-opencode/src/optio_opencode/__init__.py \
        packages/optio-opencode/src/optio_opencode/conversation.py \
        packages/optio-opencode/tests/test_agent_info.py
# + any swept files under packages/optio-opencode/
git commit -m "feat(optio-opencode): canonical AgentInfo, slug label, name sweep"
```

---

### Task 4: `codex` engine metadata

**Files:**
- Create: `packages/optio-codex/src/optio_codex/info.py`
- Modify: `packages/optio-codex/src/optio_codex/__init__.py`
- Modify: `packages/optio-codex/src/optio_codex/conversation.py` (agent_label ≈ line 123)
- Modify: swept user-facing sites under `packages/optio-codex/src/optio_codex/`
- Test: `packages/optio-codex/tests/test_agent_info.py`

**Interfaces:**
- Consumes: `from optio_agents import AgentInfo` (Task 1).
- Produces: `from optio_codex import AGENT_INFO` — `AgentInfo(slug="codex", name="Codex", url="https://openai.com/codex")`.

- [ ] **Step 1: Write the failing test**

```python
# packages/optio-codex/tests/test_agent_info.py
from optio_codex import AGENT_INFO
from optio_codex.types import CodexTaskConfig


def test_agent_info_values():
    assert AGENT_INFO.slug == "codex"
    assert AGENT_INFO.name == "Codex"
    assert AGENT_INFO.url == "https://openai.com/codex"


def test_agent_info_slug_matches_agent_type():
    import dataclasses
    fields = {f.name: f for f in dataclasses.fields(CodexTaskConfig)}
    assert fields["agent_type"].default == AGENT_INFO.slug
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/optio-codex/tests/test_agent_info.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# packages/optio-codex/src/optio_codex/info.py
from optio_agents import AgentInfo

AGENT_INFO = AgentInfo(
    slug="codex",
    name="Codex",
    url="https://openai.com/codex",
)
```

Add `from .info import AGENT_INFO` to `__init__.py` (+ `__all__`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest packages/optio-codex/tests/test_agent_info.py -v`
Expected: PASS.

- [ ] **Step 5: Unify `agent_label` + sweep**

`conversation.py`: replace `agent_label = "codex"` default with slug-derived:

```python
from .info import AGENT_INFO
agent_label: str = AGENT_INFO.slug   # "codex"
```

Sweep:

```bash
grep -rniE 'codex' packages/optio-codex/src/optio_codex/ \
  --include=*.py | grep -viE '#|"""|import|agent_type|slug'
```

Replace user-facing literals (e.g. `"launching Codex"`) with `AGENT_INFO.name`.

Run: `pytest packages/optio-codex/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-codex/src/optio_codex/info.py \
        packages/optio-codex/src/optio_codex/__init__.py \
        packages/optio-codex/src/optio_codex/conversation.py \
        packages/optio-codex/tests/test_agent_info.py
git commit -m "feat(optio-codex): canonical AgentInfo, slug label, name sweep"
```

---

### Task 5: `cursor` engine metadata

**Files:**
- Create: `packages/optio-cursor/src/optio_cursor/info.py`
- Modify: `packages/optio-cursor/src/optio_cursor/__init__.py`
- Modify: `packages/optio-cursor/src/optio_cursor/conversation.py` (agent_label ≈ line 123)
- Modify: swept user-facing sites under `packages/optio-cursor/src/optio_cursor/`
- Test: `packages/optio-cursor/tests/test_agent_info.py`

**Interfaces:**
- Consumes: `from optio_agents import AgentInfo` (Task 1).
- Produces: `from optio_cursor import AGENT_INFO` — `AgentInfo(slug="cursor", name="Cursor CLI", url="https://cursor.com/cli")`.

- [ ] **Step 1: Write the failing test**

```python
# packages/optio-cursor/tests/test_agent_info.py
from optio_cursor import AGENT_INFO
from optio_cursor.types import CursorTaskConfig


def test_agent_info_values():
    assert AGENT_INFO.slug == "cursor"
    assert AGENT_INFO.name == "Cursor CLI"
    assert AGENT_INFO.url == "https://cursor.com/cli"


def test_agent_info_slug_matches_agent_type():
    import dataclasses
    fields = {f.name: f for f in dataclasses.fields(CursorTaskConfig)}
    assert fields["agent_type"].default == AGENT_INFO.slug
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/optio-cursor/tests/test_agent_info.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# packages/optio-cursor/src/optio_cursor/info.py
from optio_agents import AgentInfo

AGENT_INFO = AgentInfo(
    slug="cursor",
    name="Cursor CLI",
    url="https://cursor.com/cli",
)
```

Add `from .info import AGENT_INFO` to `__init__.py` (+ `__all__`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest packages/optio-cursor/tests/test_agent_info.py -v`
Expected: PASS.

- [ ] **Step 5: Unify `agent_label` + sweep**

`conversation.py`: replace `agent_label = "cursor"` with slug-derived:

```python
from .info import AGENT_INFO
agent_label: str = AGENT_INFO.slug   # "cursor"
```

Sweep (note short form "Cursor" → "Cursor CLI"):

```bash
grep -rniE 'cursor cli|cursor' packages/optio-cursor/src/optio_cursor/ \
  --include=*.py | grep -viE '#|"""|import|agent_type|slug'
```

Replace user-facing literals with `AGENT_INFO.name` (`"Cursor CLI"`).

Run: `pytest packages/optio-cursor/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-cursor/src/optio_cursor/info.py \
        packages/optio-cursor/src/optio_cursor/__init__.py \
        packages/optio-cursor/src/optio_cursor/conversation.py \
        packages/optio-cursor/tests/test_agent_info.py
git commit -m "feat(optio-cursor): canonical AgentInfo, slug label, name sweep"
```

---

### Task 6: `grok` engine metadata

**Files:**
- Create: `packages/optio-grok/src/optio_grok/info.py`
- Modify: `packages/optio-grok/src/optio_grok/__init__.py`
- Modify: `packages/optio-grok/src/optio_grok/conversation.py` (agent_label ≈ line 90)
- Modify: swept user-facing sites under `packages/optio-grok/src/optio_grok/`
- Test: `packages/optio-grok/tests/test_agent_info.py`

**Interfaces:**
- Consumes: `from optio_agents import AgentInfo` (Task 1).
- Produces: `from optio_grok import AGENT_INFO` — `AgentInfo(slug="grok", name="Grok Build", url="https://x.ai/cli")`.

- [ ] **Step 1: Write the failing test**

```python
# packages/optio-grok/tests/test_agent_info.py
from optio_grok import AGENT_INFO
from optio_grok.types import GrokTaskConfig


def test_agent_info_values():
    assert AGENT_INFO.slug == "grok"
    assert AGENT_INFO.name == "Grok Build"
    assert AGENT_INFO.url == "https://x.ai/cli"


def test_agent_info_slug_matches_agent_type():
    import dataclasses
    fields = {f.name: f for f in dataclasses.fields(GrokTaskConfig)}
    assert fields["agent_type"].default == AGENT_INFO.slug
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/optio-grok/tests/test_agent_info.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# packages/optio-grok/src/optio_grok/info.py
from optio_agents import AgentInfo

AGENT_INFO = AgentInfo(
    slug="grok",
    name="Grok Build",
    url="https://x.ai/cli",
)
```

Add `from .info import AGENT_INFO` to `__init__.py` (+ `__all__`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest packages/optio-grok/tests/test_agent_info.py -v`
Expected: PASS.

- [ ] **Step 5: Unify `agent_label` + sweep**

`conversation.py`: replace `agent_label = "grok"` with slug-derived (already `"grok"`, but source it from the constant):

```python
from .info import AGENT_INFO
agent_label: str = AGENT_INFO.slug   # "grok"
```

Sweep (note short form "Grok" → "Grok Build"):

```bash
grep -rniE 'grok build|grok' packages/optio-grok/src/optio_grok/ \
  --include=*.py | grep -viE '#|"""|import|agent_type|slug'
```

Replace user-facing literals with `AGENT_INFO.name` (`"Grok Build"`).

Run: `pytest packages/optio-grok/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-grok/src/optio_grok/info.py \
        packages/optio-grok/src/optio_grok/__init__.py \
        packages/optio-grok/src/optio_grok/conversation.py \
        packages/optio-grok/tests/test_agent_info.py
git commit -m "feat(optio-grok): canonical AgentInfo, slug label, name sweep"
```

---

### Task 7: `kimicode` engine metadata

**Files:**
- Create: `packages/optio-kimicode/src/optio_kimicode/info.py`
- Modify: `packages/optio-kimicode/src/optio_kimicode/__init__.py`
- Modify: `packages/optio-kimicode/src/optio_kimicode/conversation.py` (agent_label ≈ line 105)
- Modify: swept user-facing sites under `packages/optio-kimicode/src/optio_kimicode/`
- Test: `packages/optio-kimicode/tests/test_agent_info.py`

**Interfaces:**
- Consumes: `from optio_agents import AgentInfo` (Task 1).
- Produces: `from optio_kimicode import AGENT_INFO` — `AgentInfo(slug="kimicode", name="Kimi Code", url="https://www.kimi.com/coding")`.

- [ ] **Step 1: Write the failing test**

```python
# packages/optio-kimicode/tests/test_agent_info.py
from optio_kimicode import AGENT_INFO
from optio_kimicode.types import KimiCodeTaskConfig


def test_agent_info_values():
    assert AGENT_INFO.slug == "kimicode"
    assert AGENT_INFO.name == "Kimi Code"
    assert AGENT_INFO.url == "https://www.kimi.com/coding"


def test_agent_info_slug_matches_agent_type():
    import dataclasses
    fields = {f.name: f for f in dataclasses.fields(KimiCodeTaskConfig)}
    assert fields["agent_type"].default == AGENT_INFO.slug
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/optio-kimicode/tests/test_agent_info.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# packages/optio-kimicode/src/optio_kimicode/info.py
from optio_agents import AgentInfo

AGENT_INFO = AgentInfo(
    slug="kimicode",
    name="Kimi Code",
    url="https://www.kimi.com/coding",
)
```

Add `from .info import AGENT_INFO` to `__init__.py` (+ `__all__`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest packages/optio-kimicode/tests/test_agent_info.py -v`
Expected: PASS.

- [ ] **Step 5: Unify `agent_label` + sweep**

`conversation.py`: replace `agent_label = "kimi"` with slug-derived:

```python
from .info import AGENT_INFO
agent_label: str = AGENT_INFO.slug   # "kimicode" (was "kimi")
```

Sweep (note short form "Kimi" → "Kimi Code"):

```bash
grep -rniE 'kimi code|kimi' packages/optio-kimicode/src/optio_kimicode/ \
  --include=*.py | grep -viE '#|"""|import|agent_type|slug'
```

Replace user-facing literals (e.g. `"Downloading Kimi Code"`, `"launching Kimi"`) with `AGENT_INFO.name` (`"Kimi Code"`).

Run: `pytest packages/optio-kimicode/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-kimicode/src/optio_kimicode/info.py \
        packages/optio-kimicode/src/optio_kimicode/__init__.py \
        packages/optio-kimicode/src/optio_kimicode/conversation.py \
        packages/optio-kimicode/tests/test_agent_info.py
git commit -m "feat(optio-kimicode): canonical AgentInfo, slug label, name sweep"
```

---

### Task 8: `antigravity` engine metadata

`antigravity`'s conversation ctor currently has NO `agent_label` — add one.

**Files:**
- Create: `packages/optio-antigravity/src/optio_antigravity/info.py`
- Modify: `packages/optio-antigravity/src/optio_antigravity/__init__.py`
- Modify: `packages/optio-antigravity/src/optio_antigravity/conversation.py` (add agent_label)
- Modify: swept user-facing sites under `packages/optio-antigravity/src/optio_antigravity/`
- Test: `packages/optio-antigravity/tests/test_agent_info.py`

**Interfaces:**
- Consumes: `from optio_agents import AgentInfo` (Task 1).
- Produces: `from optio_antigravity import AGENT_INFO` — `AgentInfo(slug="antigravity", name="Antigravity CLI", url="https://antigravity.google")`.

- [ ] **Step 1: Write the failing test**

```python
# packages/optio-antigravity/tests/test_agent_info.py
from optio_antigravity import AGENT_INFO
from optio_antigravity.types import AntigravityTaskConfig


def test_agent_info_values():
    assert AGENT_INFO.slug == "antigravity"
    assert AGENT_INFO.name == "Antigravity CLI"
    assert AGENT_INFO.url == "https://antigravity.google"


def test_agent_info_slug_matches_agent_type():
    import dataclasses
    fields = {f.name: f for f in dataclasses.fields(AntigravityTaskConfig)}
    assert fields["agent_type"].default == AGENT_INFO.slug
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/optio-antigravity/tests/test_agent_info.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# packages/optio-antigravity/src/optio_antigravity/info.py
from optio_agents import AgentInfo

AGENT_INFO = AgentInfo(
    slug="antigravity",
    name="Antigravity CLI",
    url="https://antigravity.google",
)
```

Add `from .info import AGENT_INFO` to `__init__.py` (+ `__all__`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest packages/optio-antigravity/tests/test_agent_info.py -v`
Expected: PASS.

- [ ] **Step 5: Add `agent_label` + sweep**

Add `agent_label` to the conversation class deriving from the slug:

```python
from .info import AGENT_INFO
agent_label: str = AGENT_INFO.slug   # "antigravity"
```

Sweep (note short form "Antigravity" → "Antigravity CLI"):

```bash
grep -rniE 'antigravity cli|antigravity' packages/optio-antigravity/src/optio_antigravity/ \
  --include=*.py | grep -viE '#|"""|import|agent_type|slug'
```

Replace user-facing literals with `AGENT_INFO.name` (`"Antigravity CLI"`).

Run: `pytest packages/optio-antigravity/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-antigravity/src/optio_antigravity/info.py \
        packages/optio-antigravity/src/optio_antigravity/__init__.py \
        packages/optio-antigravity/src/optio_antigravity/conversation.py \
        packages/optio-antigravity/tests/test_agent_info.py
git commit -m "feat(optio-antigravity): canonical AgentInfo, slug label, name sweep"
```

---

### Task 9: Aggregate in `optio-agents-all`

Depends on Tasks 2–8 (imports all seven `AGENT_INFO` constants).

**Files:**
- Create: `packages/optio-agents-all/src/optio_agents_all/info.py`
- Modify: `packages/optio-agents-all/src/optio_agents_all/__init__.py`
- Test: `packages/optio-agents-all/tests/test_agent_info.py`

**Interfaces:**
- Consumes: `AGENT_INFO` from each of the 7 wrappers; `AgentType` from `optio_agents_all.types`; `AgentInfo` from `optio_agents`.
- Produces: `from optio_agents_all import AGENTS, get_agent_info` — `AGENTS: dict[AgentType, AgentInfo]`; `get_agent_info(agent_type: AgentType) -> AgentInfo`.

- [ ] **Step 1: Write the failing test (incl. the keys-match guard)**

```python
# packages/optio-agents-all/tests/test_agent_info.py
import typing
from optio_agents_all import AGENTS, get_agent_info
from optio_agents_all.types import AgentType


def test_keys_match_agent_type():
    expected = set(typing.get_args(AgentType))
    assert set(AGENTS.keys()) == expected


def test_each_entry_slug_matches_key():
    for key, info in AGENTS.items():
        assert info.slug == key


def test_get_agent_info_lookup():
    assert get_agent_info("claudecode").name == "Claude Code"
    assert get_agent_info("grok").url == "https://x.ai/cli"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/optio-agents-all/tests/test_agent_info.py -v`
Expected: FAIL — `ImportError: cannot import name 'AGENTS'`.

- [ ] **Step 3: Write minimal implementation**

```python
# packages/optio-agents-all/src/optio_agents_all/info.py
from optio_agents import AgentInfo
from optio_claudecode import AGENT_INFO as _claudecode_info
from optio_opencode import AGENT_INFO as _opencode_info
from optio_codex import AGENT_INFO as _codex_info
from optio_cursor import AGENT_INFO as _cursor_info
from optio_grok import AGENT_INFO as _grok_info
from optio_kimicode import AGENT_INFO as _kimicode_info
from optio_antigravity import AGENT_INFO as _antigravity_info

from .types import AgentType

AGENTS: dict[AgentType, AgentInfo] = {
    "claudecode": _claudecode_info,
    "opencode": _opencode_info,
    "codex": _codex_info,
    "cursor": _cursor_info,
    "grok": _grok_info,
    "kimicode": _kimicode_info,
    "antigravity": _antigravity_info,
}


def get_agent_info(agent_type: AgentType) -> AgentInfo:
    """Return canonical metadata for an agent engine."""
    return AGENTS[agent_type]
```

Add to `__init__.py` (match existing re-export style):

```python
from .info import AGENTS, get_agent_info
```

and add `"AGENTS"`, `"get_agent_info"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest packages/optio-agents-all/tests/test_agent_info.py -v`
Expected: PASS (guard + slug match + lookup).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-agents-all/src/optio_agents_all/info.py \
        packages/optio-agents-all/src/optio_agents_all/__init__.py \
        packages/optio-agents-all/tests/test_agent_info.py
git commit -m "feat(optio-agents-all): aggregate AGENTS map + get_agent_info"
```

---

### Task 10: `optio-demo` consumes the SSOT

Depends on Task 9. Replaces retyped per-engine display strings with canonical names.

**Files:**
- Modify: `packages/optio-demo/src/optio_demo/tasks/*.py` (each engine's task module)
- Test: existing optio-demo tests (no new test required; behavior is string-sourcing)

**Interfaces:**
- Consumes: `from optio_agents_all import get_agent_info` (or per-engine `AGENT_INFO`).

- [ ] **Step 1: Locate retyped display strings**

```bash
grep -rnE 'demo — |Setup .* seed|Claude Code|Kimi Code|Antigravity|Cursor|Grok|OpenCode|opencode|Codex' \
  packages/optio-demo/src/optio_demo/tasks/
```

- [ ] **Step 2: Replace with canonical name**

In each engine's demo task module, import the canonical name and use it. Example for the claudecode demo module:

```python
from optio_agents_all import get_agent_info

_NAME = get_agent_info("claudecode").name  # "Claude Code"
# ...
name=f"{_NAME} demo — {name}"
# ...
name=f"Setup {_NAME} seed"
```

Apply the analogous change in every engine's demo task module, using that engine's `agent_type` slug. Do NOT change the demo's behavior otherwise.

- [ ] **Step 3: Run optio-demo tests**

Run: `pytest packages/optio-demo/ -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-demo/src/optio_demo/tasks/
git commit -m "refactor(optio-demo): source engine display names from canonical AgentInfo"
```

---

### Task 11: Full-suite verification

Deferred cross-package verification (per parallel-shaped-plan discipline — heavy checks run once at the end).

**Files:** none (verification only).

- [ ] **Step 1: Reinstall edited packages into the worktree venv**

Run (from repo root, worktree `.venv` active): reinstall the edited Python packages editable so imports resolve across packages. Follow the repo's existing dev-install command (e.g. `make dev-install` or the per-package `pip install -e`).

- [ ] **Step 2: Run the full Python suite**

Run: `make test`
Expected: PASS — all packages green, including the new `test_agent_info.py` files and the agents-all guard. pytest-xdist two-phase completes without the serial-marked flakes regressing.

- [ ] **Step 3: Grep for stragglers**

Confirm no user-facing hardcoded engine names remain outside the allowed skips:

```bash
grep -rnE 'Claude Code|Kimi Code|Cursor CLI|Grok Build|Antigravity CLI|OpenCode' \
  packages/optio-*/src | grep -viE 'info.py|test_|AGENT_INFO|#|"""'
```
Expected: no user-facing string literals (only constant definitions / references).

- [ ] **Step 4: Final commit (if any straggler fixes)**

```bash
git add -A   # only in this verification task, after the fan-out is merged
git commit -m "chore: final canonical-name sweep verification"
```

---

## Self-Review

**Spec coverage:**
- AgentInfo type (spec §A) → Task 1. ✓
- Per-engine constant (spec §B) → Tasks 2–8. ✓
- agents-all aggregation + guard (spec §C) → Task 9. ✓
- agent_label unify incl. 2 missing (spec §D) → Tasks 2–8 Step 5. ✓
- User-facing sweep incl. short forms (spec §E) → Tasks 2–8 Step 5 + Task 11 Step 3. ✓
- Demo call-sites (spec §F) → Task 10. ✓
- Testing (spec Testing) → per-task tests + Task 11. ✓
- Out-of-scope (no name default, no union change) → Global Constraints. ✓

**Placeholder scan:** No TBD/TODO. The sweep is an inherent discovery step (spec-acknowledged) with an exact grep + replace rule + skip list — not a placeholder.

**Type consistency:** `AgentInfo(slug,name,url)` used identically in Tasks 1–9. `AGENT_INFO` symbol consistent across all wrappers. `get_agent_info`/`AGENTS` signatures match between Task 9 (produced) and Task 10 (consumed).

## Notes for execution

- Task 1 must complete before Tasks 2–8. Tasks 2–8 are mutually independent (disjoint package trees) and parallel-safe. Task 9 needs 2–8; Task 10 needs 9; Task 11 needs all.
- Per [Agents commit own files only]: each engine agent runs `git add` on its own `packages/optio-<engine>/` paths only — never `-A` (except Task 11 after merge).
- Execute in a worktree with its own `.venv`; never `pip install -e` against global Python.
