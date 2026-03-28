# Generic Metadata Search — Design Spec

## Goal

Replace application-specific query parameters (`type`, `targetId`) with a generic metadata filtering mechanism across all layers of Optio.

## Problem

`list_processes()` has two special-cased filters that leaked from a consuming application:

- `type`: filters on a top-level `type` field that is never set by Optio — dead code.
- `target_id` / `targetId`: filters on `metadata.targetId` — a convention from one specific app, not a general Optio concern.

Additionally, `optio-ui` exports a `useSourceProcesses` hook that is entirely application-specific.

## Design

### Layer 1: optio-core (Python)

Remove `type` and `target_id` parameters from `list_processes()`. Add a `metadata` parameter:

```python
await optio_core.list_processes(
    state: str | None = None,
    root_id: str | None = None,
    metadata: dict[str, str] | None = None,
) -> list[dict]
```

Implementation in `store.py`:

```python
if metadata is not None:
    for key, value in metadata.items():
        filter[f"metadata.{key}"] = value
```

This supports exact-match filtering on any metadata key, and multiple keys can be combined.

### Layer 2: optio-contracts (TypeScript)

Update the `list` endpoint query schema:

- Remove `type` and `targetId` from `PaginationQuerySchema` / list query.
- The contract itself does not need to enumerate metadata keys — they are passed through as `metadata.*` prefixed query parameters and handled by the API layer.

### Layer 3: optio-api (TypeScript)

Update `ListQuery` interface and `listProcesses` handler:

- Remove `type` and `targetId` from `ListQuery`.
- Extract query parameters prefixed with `metadata.` and build MongoDB filter entries: `filter[metadata.${key}] = value` for each.
- The handler parses raw query params, strips the `metadata.` prefix, and passes them to the MongoDB filter.

### Layer 4: optio-ui (React)

- Remove `useSourceProcesses` hook from `useProcessQueries.ts`.
- Remove its export from `index.ts`.
- Add optional `metadata` parameter to `useProcessList` so consumers can filter by arbitrary metadata keys. The hook passes these as `metadata.*` query params to the API.

### Documentation

Update across all layers:

- `packages/optio-core/README.md`: update `list_processes` signature and parameter table.
- `packages/optio-ui/README.md`: remove `useSourceProcesses` from hooks table, update `useProcessList` docs.
- `AGENTS.md`: update Python API section (`list_processes`), TypeScript contract endpoints, API handlers, and UI hooks.

## Breaking Changes

| Layer | Before | After |
|-------|--------|-------|
| Python | `list_processes(type=..., target_id=...)` | `list_processes(metadata={"targetId": ...})` |
| REST | `?type=...&targetId=...` | `?metadata.targetId=...` |
| UI | `useSourceProcesses(sourceId)` | Removed — use `useProcessList` with `metadata` option |

## Out of Scope

- Operators beyond exact match (`$in`, `$exists`, `$regex`, etc.)
- Patching the consuming application (`~/private/guy-montag`) — tracked separately
