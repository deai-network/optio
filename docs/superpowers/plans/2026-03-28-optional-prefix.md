# Optional Prefix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `prefix` parameter optional across all four user-facing configuration surfaces, defaulting to `"optio"`.

**Architecture:** Pure default-value change at four points: `init()` in optio-core, `OptioApiOptions` in optio-api, `OptioProvider` in optio-ui, and the dashboard env var docs. No structural changes. Each README is updated to reflect that prefix is optional (an exit-hatch for name collisions).

**Tech Stack:** Python (optio-core), TypeScript/React (optio-api, optio-ui, optio-dashboard)

---

### Task 1: Make `prefix` optional in optio-core

**Files:**
- Modify: `packages/optio-core/src/optio_core/models.py:84`
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:42`

- [ ] **Step 1: Update `OptioConfig` dataclass**

In `packages/optio-core/src/optio_core/models.py`, change `prefix` from required to optional with default `"optio"`. Because dataclass fields with defaults must come after fields without defaults, move `prefix` after `mongo_db`:

```python
@dataclass
class OptioConfig:
    """Configuration for optio initialization."""
    mongo_db: Any  # motor AsyncIOMotorDatabase
    prefix: str = "optio"
    redis_url: str | None = None
    services: dict[str, Any] = field(default_factory=dict)
    get_task_definitions: Callable[..., Awaitable[list[TaskInstance]]] | None = None
```

This is a single-character change — just add ` = "optio"` to the `prefix` field.

- [ ] **Step 2: Update `init()` signature**

In `packages/optio-core/src/optio_core/lifecycle.py`, change the `init()` method signature so `prefix` defaults to `"optio"`:

```python
async def init(
    self,
    mongo_db: AsyncIOMotorDatabase,
    prefix: str = "optio",
    redis_url: str | None = None,
    services: dict[str, Any] | None = None,
    get_task_definitions: Callable[..., Awaitable[list[TaskInstance]]] | None = None,
) -> None:
```

- [ ] **Step 3: Run optio-core tests**

Run: `cd packages/optio-core && python -m pytest tests/ -v`
Expected: All existing tests pass (tests use explicit prefix values, so nothing breaks).

- [ ] **Step 4: Update optio-core README**

In `packages/optio-core/README.md`:

**a)** Update the Quick Start example (line 47-51) — remove the `prefix` argument to use the default:

```python
async def main():
    client = AsyncIOMotorClient("mongodb://localhost:27017")
    db = client["myapp"]

    await init(
        mongo_db=db,
        get_task_definitions=get_tasks,
    )
```

**b)** Update the `init()` signature block (lines 71-78) to show the default:

```python
await optio_core.init(
    mongo_db: AsyncIOMotorDatabase,
    prefix: str = "optio",
    redis_url: str | None = None,
    services: dict[str, Any] | None = None,
    get_task_definitions: Callable[..., Awaitable[list[TaskInstance]]] | None = None,
) -> None
```

**c)** Update the parameter table (line 85) — change `prefix` from "required" to `"optio"` and add explanation:

```
| `prefix` | `str` | `"optio"` | Namespace for collections (`{prefix}_processes`) and Redis streams (`{prefix}:commands`). Override if you need to avoid name collisions in a shared database. |
```

**d)** Update the `on_command()` example (lines 532-541) — remove the `prefix` argument:

```python
async def main():
    await init(
        mongo_db=db,
        redis_url="redis://localhost:6379",
        get_task_definitions=get_tasks,
    )

    on_command("my_custom_command", handle_custom)

    await run()  # Blocks, listens for commands on Redis stream "optio:commands"
```

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/models.py packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/README.md
git commit -m "Make prefix optional in optio-core, default to 'optio'"
```

---

### Task 2: Make `prefix` optional in optio-api

**Files:**
- Modify: `packages/optio-api/src/adapters/fastify.ts:15`

- [ ] **Step 1: Update `OptioApiOptions` interface**

In `packages/optio-api/src/adapters/fastify.ts`, make `prefix` optional with a default applied in the functions that use it:

```typescript
export interface OptioApiOptions {
  db: Db;
  redis: Redis;
  prefix?: string;
}
```

Then in the `registerProcessRoutes` and `registerProcessStream` functions, destructure with a default:

Find where `opts.prefix` is first used in each function and apply the default. The pattern should be:

```typescript
const prefix = opts.prefix ?? 'optio';
```

Add this line at the top of both `registerProcessRoutes` and `registerProcessStream`, then use `prefix` instead of `opts.prefix` throughout each function.

- [ ] **Step 2: Update optio-api README**

In `packages/optio-api/README.md`:

**a)** Update the Quick Setup example (lines 35-39) — remove the `prefix` property:

```typescript
const opts: OptioApiOptions = {
  db,
  redis,
};
```

Remove the comment `// MongoDB collection prefix: \`myapp_processes\`` since we're not showing a custom prefix.

**b)** Update the publisher signatures table (lines 57-60) — add a note that prefix defaults to `"optio"`.

**c)** Update the handler description (line 79) — mention that prefix defaults to `"optio"`:

```
Handler functions take `db: Db` and `prefix: string` as their first two arguments
(the Fastify adapter defaults `prefix` to `"optio"` when not specified in `OptioApiOptions`),
```

- [ ] **Step 3: Commit**

```bash
git add packages/optio-api/src/adapters/fastify.ts packages/optio-api/README.md
git commit -m "Make prefix optional in optio-api, default to 'optio'"
```

---

### Task 3: Make `prefix` optional in optio-ui

**Files:**
- Modify: `packages/optio-ui/src/context/OptioProvider.tsx:13,18`

- [ ] **Step 1: Update `OptioProviderProps` and component**

In `packages/optio-ui/src/context/OptioProvider.tsx`:

Make `prefix` optional in the props interface and apply a default in the destructuring:

```typescript
interface OptioProviderProps {
  prefix?: string;
  baseUrl?: string;
  children: ReactNode;
}

export function OptioProvider({ prefix = 'optio', baseUrl = '', children }: OptioProviderProps) {
```

No other changes needed — the context value and hook will continue to work since the default is applied at the provider level.

- [ ] **Step 2: Update optio-ui README**

In `packages/optio-ui/README.md`:

**a)** Update the Quick Setup example (line 39) — remove the `prefix` prop:

```tsx
      <OptioProvider baseUrl="http://localhost:3000">
```

**b)** Add a note after the setup example (around line 44) explaining the optional prefix:

```
`OptioProvider` accepts an optional `prefix` prop (defaults to `"optio"`). Override it if you
need a custom namespace to avoid collection name collisions in a shared database.
```

- [ ] **Step 3: Commit**

```bash
git add packages/optio-ui/src/context/OptioProvider.tsx packages/optio-ui/README.md
git commit -m "Make prefix optional in optio-ui, default to 'optio'"
```

---

### Task 4: Update optio-dashboard README

The dashboard already defaults prefix to `"optio"` via `process.env.OPTIO_PREFIX || 'optio'` in `cli.ts`. No code changes needed — just a README update.

**Files:**
- Modify: `packages/optio-dashboard/README.md:26`

- [ ] **Step 1: Update the configuration table**

In `packages/optio-dashboard/README.md`, update the `OPTIO_PREFIX` row description to clarify it's an optional override:

```
| `OPTIO_PREFIX` | `optio` | Optional namespace override for MongoDB collections and Redis streams. Change only to avoid name collisions in a shared database. |
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-dashboard/README.md
git commit -m "Clarify OPTIO_PREFIX is optional in optio-dashboard README"
```

---

### Task 5: Update root README

**Files:**
- Modify: `README.md:153-168,172,204`

- [ ] **Step 1: Update the Level 1 code example**

In `README.md`, update the `init()` call in the quick start example (lines 157-161) — remove the `prefix` argument:

```python
    await init(
        mongo_db=db,
        get_task_definitions=get_tasks,
    )
```

- [ ] **Step 2: Update the Level 2 Redis description**

In `README.md`, update line 172 to use the default prefix value instead of the template:

```
Adds external command ingestion via Redis Streams, enabling remote control of processes from other services. External systems can publish commands (launch, cancel, dismiss, resync, or custom) to the `optio:commands` Redis stream (customizable via the `prefix` parameter). Custom command handlers can be registered with `on_command()`.
```

- [ ] **Step 3: Update the dashboard configuration line**

In `README.md`, update line 204 — remove `OPTIO_PREFIX` from the listed required config:

```
Configuration is handled entirely through environment variables (`MONGODB_URL`, `REDIS_URL`, `PORT`). An optional `OPTIO_PREFIX` variable overrides the default namespace if needed. If you later need custom API endpoints or custom UI components, you can switch to using [optio-api](packages/optio-api) and [optio-ui](packages/optio-ui) directly.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Update root README to reflect optional prefix"
```
