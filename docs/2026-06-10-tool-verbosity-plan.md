# Conversation Tool-Usage Verbosity — Implementation Plan

> Small TDD feature across config (Python), engine (Python), UI (React). **Spec:** `docs/2026-06-10-tool-verbosity-design.md`.

---

## Task 1: Config field + validation (pytest, TDD)

**Files:** `packages/optio-claudecode/src/optio_claudecode/types.py`; test `packages/optio-claudecode/tests/test_conversation_config.py`.

- [ ] **Step 1: Failing tests** (append to `test_conversation_config.py`):

```python
def test_tool_verbosity_default_is_description_only():
    cfg = _cfg()
    assert cfg.tool_verbosity == "description-only"


def test_tool_verbosity_accepts_levels():
    for v in ("silent", "description-only", "verbose"):
        assert _cfg(tool_verbosity=v).tool_verbosity == v


def test_tool_verbosity_rejects_bad_value():
    with pytest.raises(ValueError, match="tool_verbosity"):
        _cfg(tool_verbosity="loud")
```

- [ ] **Step 2: Run → RED** (`undefined field` / no validation).

- [ ] **Step 3: Implement in `types.py`** — add the type alias near `ConversationMode`:
```python
ToolVerbosity = Literal["silent", "description-only", "verbose"]
_VALID_TOOL_VERBOSITY = {"silent", "description-only", "verbose"}
```
Add the field (in the conversation surface block):
```python
    # Conversation-UI tool-call rendering: "verbose" = full input table,
    # "description-only" = one summary line, "silent" = nothing. Carried to the
    # widget via widgetData; only affects conversation_ui rendering.
    tool_verbosity: ToolVerbosity = "description-only"
```
Add to `__post_init__`:
```python
        if self.tool_verbosity not in _VALID_TOOL_VERBOSITY:
            raise ValueError(
                f"ClaudeCodeTaskConfig.tool_verbosity={self.tool_verbosity!r} "
                f"is not one of {sorted(_VALID_TOOL_VERBOSITY)}"
            )
```

- [ ] **Step 4: Run → GREEN.** Commit: `feat(claudecode): tool_verbosity config field`.

## Task 2: Engine carries it via widgetData

**Files:** `packages/optio-claudecode/src/optio_claudecode/session.py`; test `packages/optio-claudecode/tests/test_conversation_ui_session.py`.

- [ ] **Step 1:** In `session.py`, change the conversation-UI line `await ctx.set_widget_data({})` to:
```python
            await ctx.set_widget_data({"toolVerbosity": config.tool_verbosity})
```

- [ ] **Step 2:** Update the existing conversation-UI session test that asserts `widgetData == {}` → assert `widgetData == {"toolVerbosity": "description-only"}` (the default). If the test constructs the config with a specific verbosity, assert that value.

- [ ] **Step 3: Run** `test_conversation_ui_session.py` → GREEN. Commit: `feat(claudecode): carry toolVerbosity to the conversation widget via widgetData`.

## Task 3: UI renders per verbosity level (vitest, TDD)

**Files:** `packages/optio-claudecode-ui/src/ClaudeCodeConversationWidget.tsx`; test `packages/optio-claudecode-ui/src/__tests__/widget.test.tsx`.

- [ ] **Step 1: Failing tests** (add cases to `widget.test.tsx`) covering a rendered `tool` item under each level:
  - `silent` → no `tool-call` element.
  - `description-only` + `input` `{description: "do X"}` → text `running <name>: do X`, **no** k-v table.
  - `description-only` + `input` `{file_path: "/a/b"}` (no description) → text contains `/a/b` (salient-key fallback).
  - `description-only` + `input` `{}` → just `running <name>` (no summary, no table).
  - `verbose` → the k-v table present (a `<td>` with a key).
  Drive the widget by stubbing the SSE/EventSource (mirror existing `widget.test.tsx`/`events.test.ts` patterns), with `process.widgetData = { toolVerbosity: <level> }`.

- [ ] **Step 2: Run → RED.**

- [ ] **Step 3: Implement in `ClaudeCodeConversationWidget.tsx`:**
  - Near the top of the component: `const toolVerbosity = ((props.process.widgetData as any)?.toolVerbosity ?? 'description-only') as 'silent' | 'description-only' | 'verbose';`
  - Add the module-level helper:
    ```typescript
    const SALIENT_KEYS = ['description', 'command', 'file_path', 'path', 'pattern', 'query', 'url', 'prompt', 'title'];
    function toolSummary(input: unknown): string {
      if (input && typeof input === 'object' && !Array.isArray(input)) {
        const obj = input as Record<string, unknown>;
        for (const k of SALIENT_KEYS) {
          const v = obj[k];
          if (typeof v === 'string' && v.trim()) {
            const s = v.trim();
            return s.length > 120 ? s.slice(0, 117) + '…' : s;
          }
        }
      }
      return '';
    }
    ```
  - Replace the `'tool'` case of `renderItem`:
    ```typescript
    case 'tool': {
      if (toolVerbosity === 'silent') return null;
      const summary = toolVerbosity === 'description-only' ? toolSummary(item.input) : '';
      return (
        <div key={item.seq} data-testid="tool-call" style={{ color: token.colorTextTertiary, fontSize: 12 }}>
          <div style={{ fontFamily: 'monospace' }}>
            running <strong>{item.name}</strong>{toolVerbosity === 'description-only' && summary ? `: ${summary}` : ':'}
          </div>
          {toolVerbosity === 'verbose' ? renderInputKV(item.input, token) : null}
        </div>
      );
    }
    ```
  (Leave the `'permission'` case unchanged — permission dialogs always show the full input so the operator can decide.)

- [ ] **Step 4: Run → GREEN** (`vitest run src/__tests__/widget.test.tsx`), then `tsc --noEmit`. Commit: `feat(claudecode-ui): tool-usage verbosity (silent/description-only/verbose)`.

## Task 4: Full-suite gate

- [ ] `python -m pytest packages/optio-claudecode/tests -q` green; `optio-claudecode-ui` vitest + tsc green; `optio-claudecode-ui` events test unaffected.
